"""Regression coverage for requirements, evidence, and affected-design tracking."""
from copy import deepcopy
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from model import uid
from store import Conflict, Store
from test_results import encoded, suite_example


class MbseTests(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.store = Store(Path(directory.name) / "designs.sqlite3")
        self.env = self.store.create("要求と検証の演習", template="mbse_example")
        self.project = self.env["project"]["id"]
        self.power, self.oil_a, self.oil_b = self.env["project"]["mbse"]["cases"]

    def save(self, document):
        self.env = self.store.save(document, self.env["revision"])

    def suite(self, power=73990):
        manifest, files = suite_example(power=power)
        oil = {"format": "oil", "conditions": {"冷間始動": {"bearings": {
            "A": {"flow_margin_lpm": -1.414}, "B": {"flow_margin_lpm": -1.893},
        }}}}
        files["oil.json"] = encoded(oil)
        manifest["artifacts"]["oil"]["sha256"] = sha256(files["oil.json"]).hexdigest()
        # Automatic verification reads the original detailed result, not summary text.
        manifest["summary"]["net_electric_power_w"] = 40000
        revision = self.env["revision"]
        result = self.store.register_result(self.project, revision, revision, "", "suite", encoded(manifest), suite_files=files)
        return result["result"]["id"], files

    def record(self, case, artifact_id, **settings):
        data = {"case_id": case["id"], "artifact_kind": "result", "artifact_id": artifact_id,
                "conditions_confirmed": True, "note": "演習の設計と運転条件を照合", **settings}
        return self.store.register_verification(self.project, self.env["revision"], data)

    def current_status(self):
        return {item["id"]: item["state"] for item in self.store.verification_status(self.project)["cases"]}

    def test_legacy_data_and_project_exchange(self):
        legacy = self.store.create("既存形式", template="single_shaft")
        self.assertNotIn("mbse", legacy["project"])
        self.assertEqual(self.store.verification_status(legacy["project"]["id"])["cases"], [])
        imported = self.store.import_bundle(self.store.export(self.project))
        self.assertEqual(imported["project"]["mbse"], self.env["project"]["mbse"])
        self.assertTrue(all(case["state"] == "pending"
                            for case in self.store.verification_status(imported["project"]["id"])["cases"]))

    def test_register_and_invalidate_only_the_affected_verification(self):
        artifact, files = self.suite()
        positive = self.record(self.power, artifact)
        negative = self.record(self.oil_a, artifact)
        self.assertAlmostEqual(positive["value"], 73.99)
        self.assertEqual(positive["source_kind"], "synthetic")
        self.assertEqual(negative["verdict"], "fail")
        self.assertEqual(self.record(self.power, artifact)["id"], positive["id"])
        self.assertEqual(self.store.verification_file(self.project, positive["id"]),
                         ("cycle-cooled.json", files["cycle-cooled.json"]))
        self.assertEqual(self.current_status()[self.power["id"]], "pass")

        document = deepcopy(self.env["project"])
        document["name"] = "名称だけ変更"
        self.save(document)
        self.assertEqual(self.current_status()[self.power["id"]], "pass")
        self.assertEqual(self.record(self.power, artifact)["id"], positive["id"])
        design = next(part for part in document["components"] if part["kind"] == "compressor")
        design["parameters"][0]["value"] = "70"
        self.save(document)
        self.assertEqual(self.current_status(), {self.power["id"]: "stale", self.oil_a["id"]: "fail", self.oil_b["id"]: "pending"})
        with self.assertRaisesRegex(ValueError, "設計版"):
            self.record(self.power, artifact)
        self.assertEqual(self.store.verification_file(self.project, positive["id"])[1], files["cycle-cooled.json"])

    def test_changed_interface_and_station_invalidate_parent_verification(self):
        artifact, _ = self.suite()
        self.record(self.power, artifact)
        document = deepcopy(self.env["project"])
        connection = document["mbse"]["interfaces"][1]
        station = next(row for row in document["stations"] if row["id"] == connection["to_station_id"])
        station["basis"] += " 全周の平均"
        self.save(document)
        self.assertEqual(self.current_status()[self.power["id"]], "stale")

    def test_changed_limit_can_be_reassessed_with_the_same_compatible_result(self):
        artifact, _ = self.suite()
        self.record(self.power, artifact)
        document = deepcopy(self.env["project"])
        document["requirements"][0]["value"] = "75"
        self.save(document)
        self.assertEqual(self.current_status()[self.power["id"]], "stale")
        record = self.record(self.power, artifact)
        self.assertEqual(record["verdict"], "fail")
        self.assertEqual(record["design_revision"], 2)
        self.assertEqual(record["source_revision"], 1)
        self.assertEqual(record["threshold"], 75)

    def test_invalid_links_are_rejected_and_old_approval_is_cleared_on_change(self):
        document = deepcopy(self.env["project"])
        root, child = document["mbse"]["traces"][:2]
        root["parent_ids"] = [child["id"]]
        with self.assertRaisesRegex(ValueError, "循環"):
            self.save(document)
        root["parent_ids"] = []
        root["approval"] = "要求仕様書 A版"
        self.save(document)
        self.assertEqual(self.env["project"]["mbse"]["traces"][0]["approval"], "要求仕様書 A版")
        document["requirements"][0]["value"] = "74"
        self.save(document)
        self.assertEqual(self.env["project"]["mbse"]["traces"][0]["approval"], "")
        document["mbse"]["interfaces"][0]["to_component_id"] = uid()
        with self.assertRaisesRegex(ValueError, "接続部品"):
            self.save(document)

    def test_test_file_requires_matching_evidence_type_and_remains_downloadable(self):
        document = deepcopy(self.env["project"])
        document["mbse"]["cases"][1].update(method="test", metric="")
        self.save(document)
        self.env = self.store.upload(self.project, self.env["revision"], "flow-test.csv", b"flow,value\nA,4.6\n")
        item = self.env["project"]["attachments"][0]
        with self.assertRaisesRegex(ValueError, "出典"):
            self.record(self.oil_a, item["id"], artifact_kind="attachment", source_kind="analysis", observed="0.1")
        with self.assertRaisesRegex(ValueError, "運転条件"):
            self.record(self.oil_a, item["id"], artifact_kind="attachment", source_kind="test",
                        observed="0.1", conditions_confirmed=False)
        with self.assertRaises(Conflict):
            self.store.register_verification(self.project, self.env["revision"] - 1, {
                "case_id": self.oil_a["id"], "artifact_id": item["id"], "note": "演習", "conditions_confirmed": True,
            })
        record = self.record(self.oil_a, item["id"], artifact_kind="attachment", source_kind="test", observed="0.1")
        self.assertEqual(record["verdict"], "pass")
        document = deepcopy(self.env["project"])
        document["attachments"] = []
        document["mbse"]["cases"].pop(1)
        self.save(document)
        self.assertEqual(self.store.verification_status(self.project)["archived"][0]["id"], record["id"])
        self.assertEqual(self.store.verification_file(self.project, record["id"]), ("flow-test.csv", b"flow,value\nA,4.6\n"))
