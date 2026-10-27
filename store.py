"""SQLite persistence, immutable snapshots, portable project exchange."""
import base64
import copy
import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from model import identifier, new_project, now, text, uid, validate_project


class Conflict(ValueError):
    pass


class Store:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS projects (
                    id TEXT PRIMARY KEY, document TEXT NOT NULL, revision INTEGER NOT NULL,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS history (
                    id INTEGER PRIMARY KEY, project_id TEXT NOT NULL, revision INTEGER NOT NULL,
                    document TEXT NOT NULL, action TEXT NOT NULL, saved_at TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS history_project ON history(project_id, revision);
                CREATE TABLE IF NOT EXISTS files (
                    id TEXT PRIMARY KEY, project_id TEXT NOT NULL, name TEXT NOT NULL, content BLOB NOT NULL);
            """)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def envelope(row):
        return {"project": json.loads(row["document"]), "revision": row["revision"],
                "created_at": row["created_at"], "updated_at": row["updated_at"]}

    def get(self, project_id, db=None):
        if db is None:
            with self.connect() as conn:
                return self.get(project_id, conn)
        row = db.execute("SELECT * FROM projects WHERE id=?", (identifier(project_id),)).fetchone()
        if row is None:
            raise LookupError("案件が見つかりません。")
        return self.envelope(row)

    def list(self):
        with self.connect() as db:
            output = []
            for row in db.execute("SELECT * FROM projects ORDER BY updated_at DESC, rowid DESC"):
                env = self.envelope(row)
                p = env.pop("project")
                output.append({**env, **{k: p[k] for k in ("id", "name", "code", "status", "owner", "application", "architecture")},
                               "component_count": len(p["components"]), "requirement_count": len(p["requirements"])})
            return output

    def _write(self, db, project, revision, action):
        project = validate_project(project)
        old = db.execute("SELECT * FROM projects WHERE id=?", (project["id"],)).fetchone()
        if old is None:
            if revision is not None:
                raise LookupError("案件が見つかりません。")
            rev, created = 1, now()
        else:
            if type(revision) is not int or old["revision"] != revision:
                raise Conflict("別の画面で案件が更新されました。画面を再読込してから編集し直してください。")
            rev, created = revision + 1, old["created_at"]
        for item in project["attachments"]:
            row = db.execute("SELECT name,length(content) AS size FROM files WHERE id=? AND project_id=?",
                             (item["id"], project["id"])).fetchone()
            if row is None or row["name"] != item["name"] or row["size"] != item["size"]:
                raise ValueError("添付ファイルの実体と登録情報が一致しません。")
        timestamp = now()
        document = json.dumps(project, ensure_ascii=False, allow_nan=False)
        db.execute("INSERT OR REPLACE INTO projects VALUES(?,?,?,?,?)", (project["id"], document, rev, created, timestamp))
        db.execute("INSERT INTO history(project_id,revision,document,action,saved_at) VALUES(?,?,?,?,?)",
                   (project["id"], rev, document, action, timestamp))
        return self.get(project["id"], db)

    def save(self, project, revision, action="設計情報を更新"):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            return self._write(db, project, revision, action)

    def create(self, name, code="", template="blank"):
        return self.save(new_project(name, code, template), None, "案件を作成")

    def history(self, project_id):
        self.get(project_id)
        with self.connect() as db:
            return [dict(r) for r in db.execute("SELECT id,revision,action,saved_at FROM history WHERE project_id=? ORDER BY revision DESC", (project_id,))]

    def restore(self, project_id, revision, history_id):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM history WHERE id=? AND project_id=?", (history_id, project_id)).fetchone()
            if row is None:
                raise LookupError("履歴が見つかりません。")
            return self._write(db, json.loads(row["document"]), revision, f"リビジョン {row['revision']} を復元")

    def upload(self, project_id, revision, name, content, component_id=""):
        if not isinstance(content, bytes) or len(content) > 5 * 1024 * 1024:
            raise ValueError("添付ファイルは1件5MB以内です。")
        name = text(name, "ファイル名", 255, True).replace("\\", "/").split("/")[-1]
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            env = self.get(project_id, db)
            p = env["project"]
            if sum(x["size"] for x in p["attachments"]) + len(content) > 30 * 1024 * 1024:
                raise ValueError("添付ファイルの合計は1案件30MB以内です。")
            file_id = uid()
            db.execute("INSERT INTO files VALUES(?,?,?,?)", (file_id, project_id, name, content))
            p["attachments"].append({"id": file_id, "name": name, "size": len(content), "component_id": component_id, "added_at": now()})
            return self._write(db, p, revision, f"資料を追加: {name}")

    def file(self, project_id, file_id, db=None):
        if db is None:
            with self.connect() as conn:
                return self.file(project_id, file_id, conn)
        p = self.get(project_id, db)["project"]
        if not any(x["id"] == file_id for x in p["attachments"]):
            raise LookupError("この案件の資料が見つかりません。")
        row = db.execute("SELECT name,content FROM files WHERE id=? AND project_id=?", (file_id, project_id)).fetchone()
        if row is None:
            raise LookupError("資料が見つかりません。")
        return row["name"], row["content"]

    def export(self, project_id):
        with self.connect() as db:
            db.execute("BEGIN")
            env = self.get(project_id, db)
            blobs = {x["id"]: base64.b64encode(self.file(project_id, x["id"], db)[1]).decode("ascii")
                     for x in env["project"]["attachments"]}
            return {"format": "gas-turbine-manager", "bundle_version": 1, "exported_at": now(),
                    **env, "files": blobs}

    def import_bundle(self, bundle, name=None):
        if not isinstance(bundle, dict) or bundle.get("format") != "gas-turbine-manager" or bundle.get("bundle_version") != 1:
            raise ValueError("対応する案件JSONを選択してください。")
        p = validate_project(bundle.get("project"))
        p = copy.deepcopy(p)
        p["id"] = uid()
        p["name"] = name if name is not None else p["name"][:140] + "（取込）"
        files = bundle.get("files", {})
        if not isinstance(files, dict):
            raise ValueError("添付ファイルの形式が不正です。")
        total = 0
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            for item in p["attachments"]:
                encoded = files.get(item["id"])
                if not isinstance(encoded, str) or len(encoded) > 7_000_000:
                    raise ValueError("添付ファイルの内容がありません。")
                try:
                    content = base64.b64decode(encoded, validate=True)
                except (ValueError, TypeError):
                    raise ValueError("添付ファイルの内容が不正です。") from None
                total += len(content)
                if len(content) != item["size"] or total > 30 * 1024 * 1024:
                    raise ValueError("添付ファイルのサイズが不正です。")
                item["id"] = uid()
                db.execute("INSERT INTO files VALUES(?,?,?,?)", (item["id"], p["id"], item["name"], content))
            return self._write(db, p, None, "設計案を複製" if name is not None else "案件JSONを取込")

    def clone(self, project_id, name):
        return self.import_bundle(self.export(project_id), name=name)
