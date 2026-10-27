import json
import re
import tempfile
import unittest
from pathlib import Path

from create_samples import create_samples
from store import Store


class SampleTests(unittest.TestCase):
    def test_training_values_differences_and_unknowns(self):
        with tempfile.TemporaryDirectory() as temp:
            result = create_samples(temp)
            store = Store(result["database"])
            a = store.get(result["baseline_id"])["project"]
            b = store.get(result["alternative_id"])["project"]
            values = lambda p: {x["name"]: x["value"] for x in p["requirements"]}
            self.assertEqual((values(a)["設計回転数"], values(b)["設計回転数"]), ("60000", "55000"))
            self.assertEqual(values(a)["目標軸出力"], "100")
            self.assertEqual(len(a["components"]), 8)
            all_values = a["requirements"] + [x for c in a["components"] for x in c["parameters"]]
            self.assertEqual(len(all_values), 23)
            self.assertEqual(sum(not x["value"] for x in all_values), 3)
            self.assertTrue(all(x["source"] for x in all_values))
            self.assertIn("架空値", a["purpose"])
            parameters = lambda p: {x["name"]: x["value"] for c in p["components"] if c["kind"] == "compressor" for x in c["parameters"]}
            self.assertEqual((parameters(a)["インペラ出口外径"], parameters(b)["インペラ出口外径"]), ("110", "118"))
            self.assertEqual((parameters(a)["断熱効率の目標"], parameters(b)["断熱効率の目標"]), ("0.78", "0.80"))
            self.assertTrue(any("CSV" in n["body"] and "同一ではありません" in n["body"] for n in b["notes"]))

    def test_relaunch_preserves_user_edits_and_does_not_duplicate(self):
        with tempfile.TemporaryDirectory() as temp:
            result = create_samples(temp)
            store = Store(result["database"])
            env = store.get(result["baseline_id"])
            env["project"]["owner"] = "演習で変更した担当"
            store.save(env["project"], env["revision"])
            self.assertFalse(create_samples(temp)["created"])
            self.assertEqual(len(store.list()), 2)
            self.assertEqual(store.get(result["baseline_id"])["project"]["owner"], "演習で変更した担当")

    def test_bundles_are_portable_and_attachments_are_exact_copies(self):
        with tempfile.TemporaryDirectory() as temp:
            create_samples(temp)
            self.assertTrue((Path(temp) / "03_追加資料_演習用.txt").is_file())
            target = Store(Path(temp) / "import.sqlite3")
            for filename in ("A_基準案.json", "B_低回転案.json"):
                bundle = json.loads((Path(temp) / filename).read_text(encoding="utf-8"))
                env = target.import_bundle(bundle)
                p = env["project"]
                self.assertEqual(len(p["attachments"]), 2)
                self.assertEqual(len(p["links"]), 1)
                for item in p["attachments"]:
                    self.assertEqual(target.file(p["id"], item["id"])[1], (Path(temp) / item["name"]).read_bytes())

    def test_manual_image_assets_and_section_links_exist(self):
        manual = Path(__file__).parent / "docs/manual/index.html"
        html = manual.read_text(encoding="utf-8")
        for name in re.findall(r'<img src="([^"]+)"', html):
            with self.subTest(asset=name):
                self.assertTrue((manual.parent / name).is_file(), name)
        sections = set(re.findall(r'\bid="([^"]+)"', html))
        for section in re.findall(r'href="#([^"]+)"', html):
            self.assertIn(section, sections)
        self.assertTrue((manual.parent / "manual.css").is_file())


if __name__ == "__main__":
    unittest.main(verbosity=2)
