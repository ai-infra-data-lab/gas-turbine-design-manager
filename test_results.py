"""Persistence and exchange contracts for versioned design artifacts."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from zipfile import ZipFile

from model import uid
from store import Conflict, Store


def encoded(value):
    return json.dumps(value, ensure_ascii=False).encode("utf-8")


def cycle_example(context=None):
    return {
        "format": "gas-turbine-cycle-case", "schema_version": 6,
        "module_id": "gt.cycle.simple_single_shaft", "module_version": "0.6.0",
        "run_id": uid(), "input_hash": "a" * 64,
        "input": {"name": "仮想サイクル", "context": context},
        "result": {"status": "success", "metrics": {"net_electric_power_w": 70000, "air_mass_flow_kg_s": .7}},
    }


def blade_example(cycle):
    rows = ["impeller-1", "diffuser-1", "return-channel", "impeller-2", "diffuser-2", "axial-guide-vane"]
    project = encoded({"schema_version": "0.3", "blade_rows": [{"identifier": row} for row in rows]})
    handoff = {"format": "blade-designer-two-stage-handoff", "schema_version": 1,
               "project_sha256": sha256(project).hexdigest(), "rows": [{"id": row} for row in rows],
               "allocated_cycle_run_id": cycle["run_id"], "allocated_cycle_input_hash": cycle["input_hash"],
               "cycle_module_version": cycle["module_version"], "cycle_context": cycle["input"]["context"],
               "source_run_id": uid(), "source_input_hash": "b" * 64, "source_report_content_sha256": "c" * 64}
    return project, handoff


def performance_example(context=None):
    cases, sources = {}, {}
    for key, source in (("equal", "仮想の均等案"), ("75-percent", "仮想の75%案")):
        case = cycle_example(context)
        case["result"]["metrics"]["specific_fuel_consumption_kg_kwh"] = .54
        case["result"]["stage_results"] = {"compressor": {"specific_work_j_kg": 150000.}}
        cases[key] = case
        sources[key] = {"source_kind": "synthetic", "source_reference": source}
    return {"format": "centrifugal-two-stage-performance-comparison", "schema_version": 1,
            "source_run_id": uid(), "source_input_hash": "d" * 64, "cycle_cases": cases,
            "performance": {"format": "centrifugal-two-stage-performance", "schema_version": 1,
                            "designs": sources,
                            "conditions": {"inlet_total_temperature_k": 288.15, "inlet_total_pressure_pa": 100311.75,
                                           "air_mass_flow_kg_s": .7, "shaft_speed_rpm": 39000}}}


def suite_example(*, flow=.7, share=.5, power=70000, specific_fuel=.5):
    keys = ("cycle-base", "combustor", "turbine", "cooling", "cycle-cooled", "thrust", "oil", "rotor")
    base = cycle_example()
    base["input"].update(mode="airflow", parameters={"shaft_speed_rpm": 39000, "fuel_lhv_j_kg": 50e6},
                         thermodynamics={"model": "nasa9_frozen", "fuel": "methane"})
    base["result"].update(stations=[{"id": "1", "mass_flow_kg_s": flow, "total_temperature_k": 288.15,
                                    "total_pressure_pa": 100311.75}],
                          stage_results={"compressor": {"stages": [{"stage": 1, "pressure_log_fraction": share}]}})
    documents = {**{key: {"format": key} for key in keys}, "cycle-base": base}
    cooled = deepcopy(base)
    cooled["run_id"] = uid()
    cooled["result"]["metrics"].update(net_electric_power_w=power, fuel_mass_flow_kg_s=.011,
                                       specific_fuel_consumption_kg_kwh=specific_fuel)
    documents["cycle-cooled"] = cooled
    documents["cooling"]["injections"] = [{"coolant_flow_kg_s": .035}]
    reaction = {"bearing_reaction": {"force_n": -1500}}
    documents["thrust"]["cases"] = {"基準隙間": {"thrust": reaction}}
    files = {f"{key}.json": encoded(document) + b"\n" for key, document in documents.items()}
    report = {"format": "gas-turbine-suite-design-point", "schema_version": 1, "name": "定格試験",
              "source_cycle": {key: base[key] for key in ("run_id", "input_hash")},
              "source_kind": "synthetic",
              "summary": {"net_electric_power_w": power, "coolant_flow_kg_s": .035, "coupling_iterations": 2},
              "artifacts": {key: {"file": f"{key}.json", "format": document["format"],
                                  "sha256": sha256(files[f"{key}.json"]).hexdigest()}
                            for key, document in documents.items()}}
    return report, files


class ResultTests(unittest.TestCase):
    def setUp(self):
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name) / "design.sqlite3"
        self.store = Store(self.path)
        self.env = self.store.create("成果物検証", template="single_shaft")
        self.project = self.env["project"]["id"]
        self.compressor = next(part for part in self.env["project"]["components"] if part["kind"] == "compressor")
        self.case = cycle_example()

    def test_suite_bundle_extends_existing_summary_and_preserves_original_bytes(self):
        report, files = suite_example()
        original = encoded(report) + b"\n"
        first = self.store.register_result(self.project, 1, 1, "", "suite", original)["result"]
        self.assertFalse(first["bundle_available"])
        with self.assertRaises(LookupError):
            self.store.result_file(self.project, first["id"], "bundle")
        saved = self.store.register_result(self.project, 1, 1, "", "suite", encoded(report), suite_files=files)
        self.assertFalse(saved["created"])
        self.assertTrue(saved["bundle_added"])
        self.assertEqual(first["id"], saved["result"]["id"])
        self.assertTrue(saved["result"]["bundle_available"])
        repeat = self.store.register_result(self.project, 1, 1, "", "suite", encoded(report), suite_files=files)
        self.assertNotIn("bundle_added", repeat)
        self.assertEqual(self.store.get(self.project), self.env)
        listed = Store(self.path).design_results(self.project)["results"]
        self.assertEqual(len(listed), 1)
        self.assertTrue(listed[0]["bundle_available"])
        name, archive_bytes = self.store.result_file(self.project, first["id"], "bundle")
        self.assertEqual(name, "suite-design-point.zip")
        with ZipFile(BytesIO(archive_bytes)) as archive:
            self.assertEqual(set(archive.namelist()), {*files, "suite-report.json"})
            self.assertEqual(archive.read("suite-report.json"), original)
            for filename, content in files.items():
                self.assertEqual(archive.read(filename), content)
        other = self.store.create("別の案件")["project"]["id"]
        with self.assertRaises(LookupError):
            self.store.result_file(other, first["id"], "bundle")

    def test_invalid_suite_bundle_is_rejected_before_registration(self):
        doc, files = suite_example()
        for invalid, error in (({name: value for name, value in files.items() if name != "oil.json"}, "不足"),
                               ({**files, "more.json": b"{}"}, "指定外"),
                               ({**files, "oil.json": files["oil.json"] + b" "}, "SHA-256")):
            with self.subTest(error=error), self.assertRaisesRegex(ValueError, error):
                self.store.register_result(self.project, 1, 1, "", "suite", encoded(doc), suite_files=invalid)
        inconsistent = deepcopy(doc)
        inconsistent["artifacts"]["oil"]["format"] = "cycle-case"
        with self.assertRaisesRegex(ValueError, "形式"):
            self.store.register_result(self.project, 1, 1, "", "suite", encoded(inconsistent), suite_files=files)
        wrong_source = deepcopy(doc)
        wrong_source["source_cycle"]["run_id"] = uid()
        with self.assertRaisesRegex(ValueError, "実行ID"):
            self.store.register_result(self.project, 1, 1, "", "suite", encoded(wrong_source), suite_files=files)
        self.assertEqual(self.store.design_results(self.project)["results"], [])
        outcome = self.store.register_result(self.project, 1, 1, "", "suite", encoded(doc), suite_files=files)
        self.assertTrue(outcome["created"])
        self.assertTrue(outcome["result"]["bundle_available"])

    def register_suite(self, **settings):
        report, files = suite_example(**settings)
        revision = self.store.get(self.project)["revision"]
        return self.store.register_result(self.project, revision, revision, "", "suite", encoded(report),
                                          suite_files=files)["result"]["id"]

    def test_suite_comparison_matches_conditions_and_uses_archived_metrics(self):
        reference = self.register_suite()
        candidate = self.register_suite(share=.75, power=69500, specific_fuel=None)
        result = self.store.suite_comparison(self.project, reference, candidate)
        self.assertTrue(result["same_operating_point"])
        self.assertEqual([item["id"] for item in result["cases"]], [reference, candidate])
        self.assertTrue(all(item["source_kind"] == "synthetic" and not item["outdated"] for item in result["cases"]))
        metrics = {row["label"]: row for row in result["metrics"]}
        self.assertEqual(metrics["圧縮機第1段の対数全圧比分配"]["delta"], 25)
        self.assertEqual(metrics["正味出力"]["delta"], -.5)
        self.assertIsNone(metrics["発電基準の燃料消費率"]["delta"])
        self.assertEqual(metrics["軸受反力（基準隙間）"]["reference"], -1500)
        self.store.save({**self.env["project"], "name":"改訂案"}, self.env["revision"])
        cases = self.store.suite_comparison(self.project, reference, candidate)["cases"]
        self.assertTrue(all(item["outdated"] for item in cases))

    def test_support_review_is_bound_to_the_cooled_cycle_and_compared(self):
        def register(factor, wrong_source=False):
            manifest, files = suite_example()
            cooled = json.loads(files["cycle-cooled.json"])
            source = {"run_id": cooled["run_id"], "input_hash": cooled["input_hash"], "context": cooled["input"]["context"]}
            if wrong_source:
                source["run_id"] = uid()
            name = "support-review.json"
            review = {"format": "gas-turbine-suite-support-review", "schema_version": 1, "source_cycle": source,
                      "combustor_thermal": {"outlet_profile": {"pattern_factor": factor}},
                      "mechanical": {"axial_cases": {}, "oil_conditions": {}, "rotor_cases": {}}}
            files[name] = encoded(review)
            manifest["artifacts"]["support-review"] = {"file": name, "format": review["format"],
                                                       "sha256": sha256(files[name]).hexdigest()}
            result = self.store.register_result(self.project, 1, 1, "", "suite", encoded(manifest), suite_files=files)
            self.assertTrue(result["result"]["support_review"])
            result_id = result["result"]["id"]
            self.assertEqual(self.store.result_file(self.project, result_id, "support-review"), (name, files[name]))
            return result_id

        with self.assertRaisesRegex(ValueError, "冷却後サイクル"):
            register(.12, wrong_source=True)
        left, right = register(.12), register(.15)
        metrics = self.store.suite_comparison(self.project, left, right)["metrics"]
        row = next(metric for metric in metrics if metric["label"] == "燃焼器出口パターンファクター")
        self.assertAlmostEqual(row["delta"], .03)
        older = self.register_suite()
        metrics = self.store.suite_comparison(self.project, older, right)["metrics"]
        self.assertTrue(any(metric["label"] == "正味出力" for metric in metrics))

    def test_suite_comparison_different_conditions_and_incomplete_records(self):
        reference = self.register_suite()
        candidate = self.register_suite(flow=.71)
        result = self.store.suite_comparison(self.project, reference, candidate)
        self.assertFalse(result["same_operating_point"])
        self.assertEqual([r["label"] for r in result["conditions"] if not r["match"]], ["圧縮機入口空気流量"])
        self.assertTrue(all(metric["delta"] is None for metric in result["metrics"]))
        with self.assertRaisesRegex(ValueError, "個別"):
            self.store.suite_comparison(self.project, reference, reference)
        report, _ = suite_example()
        summary = self.store.register_result(self.project, 1, 1, "", "suite", encoded(report))["result"]
        with self.assertRaisesRegex(ValueError, "詳細レポート"):
            self.store.suite_comparison(self.project, reference, summary["id"])
        other = self.store.create("別案件")["project"]["id"]
        with self.assertRaisesRegex(LookupError, "同じ案件"):
            self.store.suite_comparison(other, reference, candidate)

    def test_suite_comparison_identifies_replaced_blade_from_the_same_cycle(self):
        report, files = suite_example()
        self.case.update(report["source_cycle"])
        reference = self.store.register_result(self.project, 1, 1, "", "suite", encoded(report),
                                               suite_files=files)["result"]["id"]
        candidate = self.register_suite(share=.75)
        original = self.register_shape(.5, variation="元の翼形状")
        updated = self.register_shape(.5, variation="更新翼形状", replaces=original["id"])
        left, right = self.store.suite_comparison(self.project, reference, candidate)["cases"]
        self.assertFalse(left["outdated"])
        self.assertEqual(left["related_blades"], [{"id":original["id"], "replaced_by":updated["id"]},
                                                  {"id":updated["id"], "replaced_by":None}])
        self.assertEqual(right["related_blades"], [])

    def test_station_review_binds_station_ids_and_preserves_missing_loss(self):
        layout = {"compressor_stages": 2, "turbine_stages": 3, "axial_guide_vanes": True}
        baseline = self.store.configure_stations(self.project, 1, layout)
        revision = baseline["revision"]
        sources = {name: {"geometry_sha256": "a" * 64, "run_id": uid(), "input_hash": "b" * 64}
                   for name in ("compressor", "turbine")}
        blade = self.register_shape(.5, variation="評価した形状", revision=revision)
        sources["compressor"].update(geometry_sha256=blade["project_sha256"], run_id=blade["run_id"],
                                     input_hash=blade["input_hash"])
        sources["pressure_csv"] = {"kind": "synthetic", "sha256": "c" * 64, "reference": "仮想圧力"}
        rows = [{"code": code, "area_m2": .004, "area_basis": "幾何面積", "observed_total_pressure_pa": pressure}
                for code, pressure in (("CP-AGV-LE", 140000), ("CP-AGV-TE", 139000), ("TB-03-NGV-LE", None))]
        report = {"format": "gas-turbine-station-review", "schema_version": 1, "name": "断面照合",
                  "sources": sources, "stations": rows,
                  "losses": [{"name": "ガイドベーン", "inlet": "CP-AGV-LE", "outlet": "CP-AGV-TE",
                              "model_loss_pa": None, "observed_loss_pa": 1000}]}
        raw = encoded(report)
        wrong = deepcopy(report)
        wrong["losses"][0]["model_loss_pa"] = 1000
        with self.assertRaisesRegex(ValueError, "入口・出口全圧"):
            self.store.register_result(self.project, revision, revision, "", "station-review", encoded(wrong))
        saved = self.store.register_result(self.project, revision, revision, "", "station-review", raw)["result"]
        stations = {row["code"]: row["id"] for row in baseline["project"]["stations"]}
        self.assertEqual({row["code"]: row["station_id"] for row in saved["stations"]},
                         {row["code"]: stations[row["code"]] for row in rows})
        self.assertIsNone(saved["losses"][0]["model_loss_pa"])
        self.assertEqual(saved["losses"][0]["observed_loss_pa"], 1000)
        self.assertEqual(saved["blade_result_id"], blade["id"])
        self.assertEqual(self.store.result_file(self.project, saved["id"], "primary")[1], raw)
        self.register_shape(.5, variation="差し替えた形状", replaces=blade["id"], revision=revision)
        current = self.store.design_results(self.project)["results"]
        self.assertTrue(next(row for row in current if row["id"] == saved["id"])["outdated"])
        next_version = self.store.configure_stations(self.project, revision, {**layout, "turbine_stages": 2})
        with self.assertRaisesRegex(ValueError, "TB-03-NGV-LE"):
            self.store.register_result(self.project, next_version["revision"], next_version["revision"],
                                       "", "station-review", raw)

    def test_cycle_duplicate_across_formats_leaves_design_revision_unchanged(self):
        bundle = encoded({"format": "centrifugal-two-stage-design", "schema_version": 1, "cycle_case": self.case})
        first = self.store.register_result(self.project, 1, 1, "", "cycle", bundle)
        duplicate = self.store.register_result(self.project, 1, 1, "", "cycle", encoded(self.case))
        self.assertTrue(first["created"])
        self.assertFalse(duplicate["created"])
        self.assertEqual(first["result"]["id"], duplicate["result"]["id"])
        self.assertEqual(self.store.get(self.project), self.env)
        self.assertEqual(len(self.store.history(self.project)), 1)
        self.assertEqual(self.store.result_file(self.project, first["result"]["id"], "primary")[1], bundle)

    def test_cooling_tracks_station_revision_and_preserves_source_file(self):
        layout = {"compressor_stages": 2, "turbine_stages": 3, "axial_guide_vanes": True}
        baseline = self.store.configure_stations(self.project, 1, layout)
        rev = baseline["revision"]
        station = next(row for row in baseline["project"]["stations"] if row["code"] == "TB-03-ROT-TE")
        source = {"run_id": self.case["run_id"], "input_hash": self.case["input_hash"],
                  "context": {"project_id": self.project, "source_revision": rev}}
        document = {"format": "cycle-secondary-air-handoff", "schema_version": 1,
                    "source_cycle": source, "cycle_feedback": "design_point_once",
                    "network_result": {"status": "converged", "model": {"name": "仮想冷却"},
                                       "nodes": [{"id": "out", "kind": "boundary", "pressure_pa": 120000}],
                                       "edges": [{"id": "hole", "downstream": "out", "mass_flow_kg_s": .01,
                                                  "outlet_absolute_total_temperature_k": 450}]},
                    "injections": [{"station": station["code"], "edge_id": "hole", "coolant_flow_kg_s": .01,
                                    "coolant_total_temperature_k": 450, "pre_injection_total_pressure_pa": 145000}]}
        original = encoded(document)
        first = self.store.register_result(self.project, rev, rev, "", "cooling", original)["result"]
        self.assertEqual(first["injections"][0]["station_id"], station["id"])
        self.assertFalse(first["outdated"])
        self.assertFalse(self.store.register_result(self.project, rev, rev, "", "cooling", original)["created"])
        self.store.configure_stations(self.project, rev, {**layout, "turbine_stages": 2})
        stored = Store(self.path).design_results(self.project)["results"][0]
        self.assertTrue(stored["outdated"])
        self.assertEqual(stored["injections"][0]["station_id"], station["id"])
        self.assertEqual(self.store.result_file(self.project, first["id"], "primary")[1], original)

    def test_link_by_cycle_case_and_revision_and_keep_original_after_update(self):
        project, reference = blade_example(self.case)
        item = self.store.register_result(self.project, 1, 1, self.compressor["id"], "blade", project, encoded(reference))
        blade_id = item["result"]["id"]
        self.assertIsNone(self.store.design_results(self.project)["results"][0]["cycle_result_id"])
        cycle = self.store.register_result(self.project, 1, 1, "", "cycle", encoded(self.case))
        self.env["project"]["purpose"] = "設計条件変更"
        self.store.save(self.env["project"], 1)
        duplicate = self.store.register_result(self.project, 2, 2, self.compressor["id"], "blade", project, encoded(reference))
        self.assertFalse(duplicate["created"])
        self.assertEqual(duplicate["result"]["source_revision"], 1)
        self.assertTrue(duplicate["result"]["outdated"])
        results = Store(self.path).design_results(self.project)
        self.assertEqual(results["revision"], 2)
        self.assertEqual(len(results["results"]), 2)
        blade = next(result for result in results["results"] if result["id"] == blade_id)
        self.assertEqual(blade["cycle_result_id"], cycle["result"]["id"])
        self.assertEqual(self.store.result_file(self.project, blade_id, "primary")[1], project)
        self.assertEqual(self.store.result_file(self.project, blade_id, "handoff")[1], encoded(reference))
        other = self.store.create("別案件")["project"]["id"]
        with self.assertRaises(LookupError):
            self.store.result_file(other, blade_id, "primary")

    def test_rejects_invalid_pair_and_changed_case_with_same_id(self):
        project, reference = blade_example(self.case)
        with self.assertRaisesRegex(ValueError, "翼形状JSONと手渡し記録のSHA-256"):
            self.store.register_result(self.project, 1, 1, "", "blade", project + b"\n", encoded(reference))
        self.assertEqual(self.store.design_results(self.project)["results"], [])
        self.store.register_result(self.project, 1, 1, "", "cycle", encoded(self.case))
        changed = deepcopy(self.case)
        changed["result"]["metrics"]["net_electric_power_w"] += 1
        with self.assertRaises(Conflict):
            self.store.register_result(self.project, 1, 1, "", "cycle", encoded(changed))
        self.assertEqual(len(self.store.design_results(self.project)["results"]), 1)

    def test_uses_historical_components_and_rejects_conflicting_declared_context(self):
        part = self.compressor["id"]
        case = cycle_example({"project_id": self.project, "component_id": part, "source_revision": 1})
        self.env["project"]["components"] = [p for p in self.env["project"]["components"] if p["id"] != part]
        self.store.save(self.env["project"], 1)
        self.assertIn(part, [p["id"] for p in self.store.result_targets(self.project, 1)])
        self.assertNotIn(part, [p["id"] for p in self.store.result_targets(self.project, 2)])
        with self.assertRaisesRegex(ValueError, "登録先が一致しません"):
            self.store.register_result(self.project, 2, 2, part, "cycle", encoded(case))
        with self.assertRaises(Conflict):
            self.store.register_result(self.project, 1, 1, part, "cycle", encoded(case))
        result = self.store.register_result(self.project, 2, 1, part, "cycle", encoded(case))["result"]
        self.assertEqual(result["component_name"], self.compressor["name"])
        self.assertTrue(result["outdated"])

    def test_concurrent_reimport_creates_one_record(self):
        def register(_):
            return self.store.register_result(self.project, 1, 1, "", "cycle", encoded(self.case))

        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(register, range(2)))
        self.assertEqual(sum(item["created"] for item in outcomes), 1)
        self.assertEqual(len({item["result"]["id"] for item in outcomes}), 1)

    def register_shape(self, share, *, variation="", replaces=None, revision=1):
        project, handoff = blade_example(self.case)
        # Simulate a new native geometry with the same row identities.
        geometry = json.loads(project)
        geometry["variation"] = variation
        project = encoded(geometry)
        handoff.update(stage1_share=share, project_sha256=sha256(project).hexdigest())
        return self.store.register_result(self.project, revision, revision, self.compressor["id"], "blade",
                                          project, encoded(handoff), replaces_blade_id=replaces)["result"]

    def register_comparison(self, doc, blade_ids, revision=1):
        return self.store.register_result(self.project, revision, revision, self.compressor["id"], "performance",
                                          encoded(doc), blade_ids=blade_ids)

    def test_performance_links_known_geometry_and_preserves_original_document(self):
        equal = self.register_shape(.5, variation="均等")
        high = self.register_shape(.75, variation="75%")
        # Simulate metadata created before stage1_share became an indexed display field.
        with self.store.connect() as db:
            db.execute("UPDATE design_results SET metadata=json_remove(metadata, '$.stage1_share') WHERE id=?", (equal["id"],))
        blade_ids = {"equal": equal["id"], "75-percent": high["id"]}
        doc = performance_example()
        result = self.register_comparison(doc, blade_ids)["result"]
        duplicate = self.register_comparison(doc, blade_ids)
        self.assertFalse(duplicate["created"])
        self.assertEqual(duplicate["result"]["id"], result["id"])
        self.assertEqual(self.store.get(self.project)["revision"], 1)
        self.assertEqual(self.store.result_file(self.project, result["id"], "primary")[1], encoded(doc))
        for key, shape in (("equal", equal), ("75-percent", high)):
            self.assertEqual(result["designs"][key]["project_sha256"], shape["project_sha256"])
            self.assertEqual(result["designs"][key]["blade_result_id"], shape["id"])
            _, case = self.store.result_file(self.project, result["id"], key)
            self.assertEqual(json.loads(case), doc["cycle_cases"][key])
        self.assertFalse(next(item for item in self.store.design_results(self.project)["results"]
                              if item["id"] == result["id"])["outdated"])
        with self.assertRaisesRegex(ValueError, "配分が一致しません"):
            self.register_comparison(performance_example(), {"equal": high["id"], "75-percent": equal["id"]})
        wrong_case = deepcopy(doc)
        wrong_case["cycle_cases"]["equal"]["input"]["context"] = {"project_id": uid()}
        with self.assertRaisesRegex(ValueError, "入力元の案件情報"):
            self.register_comparison(wrong_case, blade_ids)
        other = self.store.create("別案件")["project"]["id"]
        with self.assertRaisesRegex(ValueError, "同じ案件・部品・設計版"):
            self.store.register_result(other, 1, 1, "", "performance", encoded(doc), blade_ids=blade_ids)

    def test_only_explicit_geometry_replacement_invalidates_its_comparison_inside_revision(self):
        equal = self.register_shape(.5, variation="均等")
        high = self.register_shape(.75, variation="75%")
        doc = performance_example()
        ids = {"equal": equal["id"], "75-percent": high["id"]}
        comparison = self.register_comparison(doc, ids)["result"]
        alt = self.register_shape(.5, variation="同じ配分の別案")
        unaffected = self.register_comparison(performance_example(), {**ids, "equal": alt["id"]})["result"]

        def summaries():
            return {item["id"]:item for item in Store(self.path).design_results(self.project)["results"]}

        self.assertFalse(summaries()[comparison["id"]]["outdated"])
        updated = self.register_shape(.5, variation="均等を更新", replaces=equal["id"])
        state = summaries()
        self.assertTrue(state[comparison["id"]]["outdated"])
        self.assertEqual(state[comparison["id"]]["designs"]["equal"]["replaced_by_blade_id"], updated["id"])
        self.assertIsNone(state[comparison["id"]]["designs"]["75-percent"]["replaced_by_blade_id"])
        self.assertFalse(state[unaffected["id"]]["outdated"])
        self.assertEqual(state[comparison["id"]]["designs"]["equal"]["blade_result_id"], equal["id"])
        self.assertEqual(self.store.result_file(self.project, comparison["id"], "primary")[1], encoded(doc))
        repeated = self.register_comparison(doc, ids)
        self.assertFalse(repeated["created"])
        self.assertTrue(repeated["result"]["outdated"])
        with self.assertRaises(Conflict):
            self.register_shape(.5, variation="二重差し替え", replaces=equal["id"])
        with self.assertRaises(Conflict):
            self.register_comparison(doc, {**ids, "equal": updated["id"]})
        self.env["project"]["purpose"] = "設計条件変更"
        self.store.save(self.env["project"], 1)
        self.assertTrue(summaries()[unaffected["id"]]["outdated"])
