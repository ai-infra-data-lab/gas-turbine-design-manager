"""Persistence and exchange contracts for versioned design artifacts."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

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
