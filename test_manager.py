import base64
import copy
import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from model import new_project, uid, validate_project
from server import TOKEN, make_server
from store import Conflict, Store


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "designs.sqlite3"
        self.store = Store(self.path)
        self.env = self.store.create("小型ガスタービン", "GT-001", "single_shaft")

    def tearDown(self):
        self.temp.cleanup()

    def test_template_leaves_unknown_values_blank(self):
        p = self.env["project"]
        self.assertEqual(len(p["components"]), 8)
        self.assertTrue(all(x["value"] == "" for x in p["requirements"]))
        self.assertEqual(p["extensions"], {})

    def test_persistence_and_history_across_store_instances(self):
        p = copy.deepcopy(self.env["project"])
        p["requirements"][0].update(value="100", source="要求仕様書 §2", note="設計点")
        saved = self.store.save(p, 1)
        other = Store(self.path)
        self.assertEqual(other.get(p["id"]), saved)
        self.assertEqual([x["revision"] for x in other.history(p["id"])], [2, 1])

    def test_optimistic_conflict_preserves_newer_data(self):
        p = copy.deepcopy(self.env["project"])
        p["owner"] = "担当A"
        self.store.save(p, 1)
        p["owner"] = "担当B"
        with self.assertRaises(Conflict):
            self.store.save(p, 1)
        self.assertEqual(self.store.get(p["id"])["project"]["owner"], "担当A")
        self.assertEqual(len(self.store.history(p["id"])), 2)

    def test_two_writers_cannot_both_overwrite_same_revision(self):
        results = []
        def write(owner):
            p = copy.deepcopy(self.env["project"])
            p["owner"] = owner
            try:
                self.store.save(p, 1)
                results.append("saved")
            except Conflict:
                results.append("conflict")
        threads = [threading.Thread(target=write, args=(x,)) for x in ("A", "B")]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertCountEqual(results, ["saved", "conflict"])

    def test_attachment_is_copy_and_restorable_after_unlink(self):
        env = self.store.upload(self.env["project"]["id"], 1, "資料.txt", "設計資料".encode())
        p = env["project"]
        fid = p["attachments"][0]["id"]
        self.assertEqual(self.store.file(p["id"], fid)[1], "設計資料".encode())
        p["attachments"] = []
        self.store.save(p, 2)
        with self.assertRaises(LookupError):
            self.store.file(p["id"], fid)
        h = next(x for x in self.store.history(p["id"]) if x["revision"] == 2)
        restored = self.store.restore(p["id"], 3, h["id"])
        self.assertEqual(restored["revision"], 4)
        self.assertEqual(self.store.file(p["id"], fid)[1], "設計資料".encode())

    def test_export_import_preserves_values_and_binary_files_with_new_identity(self):
        payload = b"\x00\xff\x10\x89sample"
        env = self.store.upload(self.env["project"]["id"], 1, "shape.bin", payload)
        bundle = json.loads(json.dumps(self.store.export(env["project"]["id"])))
        # A different database proves that the bundle is self-contained.
        destination = Store(Path(self.temp.name) / "other.sqlite3")
        imported = destination.import_bundle(bundle)
        p = imported["project"]
        self.assertNotEqual(p["id"], env["project"]["id"])
        self.assertEqual(p["requirements"], env["project"]["requirements"])
        self.assertEqual(destination.file(p["id"], p["attachments"][0]["id"])[1], payload)

    def test_clone_is_independent_but_keeps_component_ids_for_comparison(self):
        source = self.env["project"]
        clone = self.store.clone(source["id"], "低回転案")
        p = clone["project"]
        self.assertEqual(p["components"][0]["id"], source["components"][0]["id"])
        p["components"][0]["name"] = "別の吸気系"
        self.store.save(p, 1)
        self.assertEqual(self.store.get(source["id"])["project"]["components"][0]["name"], "吸気系")

    def test_failed_import_rolls_back_every_file(self):
        env = self.store.upload(self.env["project"]["id"], 1, "valid.txt", b"abc")
        bundle = self.store.export(env["project"]["id"])
        item = copy.deepcopy(bundle["project"]["attachments"][0])
        item["id"] = uid()
        bundle["project"]["attachments"].append(item)
        bundle["files"][item["id"]] = "invalid base64"
        with self.store.connect() as db:
            count = db.execute("SELECT count(*) FROM files").fetchone()[0]
        with self.assertRaises(ValueError):
            self.store.import_bundle(bundle)
        with self.store.connect() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM files").fetchone()[0], count)
        self.assertEqual(len(self.store.list()), 1)

    def test_cross_project_attachment_and_history_are_rejected(self):
        source_id = self.env["project"]["id"]
        env = self.store.upload(source_id, 1, "private.txt", b"private")
        other = self.store.create("別案件")
        with self.assertRaises(LookupError):
            self.store.file(other["project"]["id"], env["project"]["attachments"][0]["id"])
        other["project"]["attachments"] = env["project"]["attachments"]
        with self.assertRaises(ValueError):
            self.store.save(other["project"], 1)
        with self.assertRaises(LookupError):
            self.store.restore(other["project"]["id"], 1, self.store.history(source_id)[0]["id"])

    def test_failed_upload_conflict_rolls_back_blob(self):
        pid = self.env["project"]["id"]
        self.store.save(self.env["project"], 1)
        with self.assertRaises(Conflict):
            self.store.upload(pid, 1, "stale.txt", b"stale")
        with self.store.connect() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM files").fetchone()[0], 0)

    def test_validation_duplicate_ids_dangling_reference_and_script_url(self):
        cases = []
        p = copy.deepcopy(self.env["project"])
        p["components"].append(p["components"][0])
        cases.append(p)
        p = copy.deepcopy(self.env["project"])
        p["links"].append({"id": uid(), "title": "資料", "url": "https://example.com", "component_id": uid()})
        cases.append(p)
        p = copy.deepcopy(self.env["project"])
        p["links"].append({"id": uid(), "title": "資料", "url": "javascript:alert(1)"})
        cases.append(p)
        for case in cases:
            with self.subTest(case=case), self.assertRaises(ValueError):
                validate_project(case)

    def test_future_module_data_round_trips_without_execution(self):
        p = self.env["project"]
        p["extensions"] = {"example.cycle": {"schema_version": 1, "run_id": "future", "state": "not_run"}}
        self.store.save(p, 1)
        clone = self.store.clone(p["id"], "連携候補")
        self.assertEqual(clone["project"]["extensions"], p["extensions"])

    def test_large_file_rejected_without_persistence(self):
        with self.assertRaises(ValueError):
            self.store.upload(self.env["project"]["id"], 1, "big.bin", b"x" * (5 * 1024 * 1024 + 1))
        self.assertEqual(self.store.get(self.env["project"]["id"])["revision"], 1)


class APITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.server = make_server(Path(cls.temp.name) / "api.sqlite3")
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.url = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()
        cls.temp.cleanup()

    def request(self, path, data=None, headers=None):
        headers = {"Content-Type": "application/json", "X-Design-Token": TOKEN, **(headers or {})}
        req = urllib.request.Request(self.url + path, data=json.dumps(data).encode() if data is not None else None, headers=headers)
        try:
            with urllib.request.urlopen(req) as response:
                return response.status, response.read(), response.headers
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read(), exc.headers

    def test_create_update_conflict_export_import_download(self):
        status, body, _ = self.request("/api/new", {"name": "API検証", "template": "single_shaft"})
        self.assertEqual(status, 200)
        env = json.loads(body)
        p = env["project"]
        p["owner"] = "担当"
        self.assertEqual(self.request("/api/save", {"project": p, "revision": 1})[0], 200)
        self.assertEqual(self.request("/api/save", {"project": p, "revision": 1})[0], 409)
        status, body, _ = self.request("/api/upload", {"id": p["id"], "revision": 2, "name": "寸法.csv", "data": base64.b64encode(b"a,b\n1,2").decode()})
        self.assertEqual(status, 200)
        f = json.loads(body)["project"]["attachments"][0]
        status, content, headers = self.request(f'/api/file?project={p["id"]}&id={f["id"]}')
        self.assertEqual((status, content), (200, b"a,b\n1,2"))
        self.assertIn("attachment", headers["Content-Disposition"])
        status, body, _ = self.request("/api/export?id=" + p["id"])
        self.assertEqual(status, 200)
        self.assertEqual(self.request("/api/import", {"bundle": json.loads(body)})[0], 200)

    def test_token_origin_host_and_path_boundaries(self):
        self.assertEqual(self.request("/api/new", {"name": "x"}, {"X-Design-Token": "wrong"})[0], 403)
        self.assertEqual(self.request("/api/new", {"name": "x"}, {"Origin": "https://example.com"})[0], 403)
        self.assertEqual(self.request("/api/projects", headers={"Host": "example.com"})[0], 403)
        self.assertEqual(self.request("/../store.py")[0], 404)
        self.assertEqual(self.request("/api/unknown", {})[0], 404)

    def test_invalid_payload_rejected_and_app_assets_served(self):
        self.assertEqual(self.request("/api/new", {"name": "   "})[0], 400)
        self.assertEqual(self.request("/api/save", {"project": None})[0], 400)
        self.assertEqual(self.request("/api/import", {"bundle": {}})[0], 400)
        status, body, headers = self.request("/")
        self.assertEqual(status, 200)
        self.assertIn(TOKEN.encode(), body)
        self.assertIn("frame-ancestors 'none'", headers["Content-Security-Policy"])
        self.assertEqual(self.request("/app.js")[0], 200)
        self.assertEqual(self.request("/style.css")[0], 200)
        self.assertEqual(self.request("/manual/")[0], 200)
        self.assertEqual(self.request("/manual/manual.css")[0], 200)
        self.assertEqual(self.request("/manual/images/01-projects.jpg")[0], 200)
        self.assertEqual(self.request("/manual/images/../../store.py")[0], 404)


if __name__ == "__main__":
    unittest.main(verbosity=2)
