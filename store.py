"""SQLite persistence, immutable snapshots, portable project exchange."""
import base64
import copy
import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from model import identifier, new_project, now, text, uid, validate_project
from results import PERFORMANCE_DESIGNS, blade_stage1_share, parse as parse_result
from stations import configure as configure_evaluation_stations, validate_layout


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
                CREATE TABLE IF NOT EXISTS design_results (
                    id TEXT PRIMARY KEY, project_id TEXT NOT NULL, component_id TEXT NOT NULL,
                    source_revision INTEGER NOT NULL, kind TEXT NOT NULL, identity TEXT NOT NULL,
                    metadata TEXT NOT NULL, content BLOB NOT NULL, handoff BLOB, saved_at TEXT NOT NULL,
                    UNIQUE(project_id, kind, identity));
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

    def configure_stations(self, project_id, revision, settings):
        layout = validate_layout(settings)
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            env = self.get(project_id, db)
            if type(revision) is not int or env["revision"] != revision:
                raise Conflict("別の画面で案件が更新されました。画面を再読込してから編集し直してください。")
            project = env["project"]
            stations = configure_evaluation_stations(project["components"], project.get("stations", []), layout)
            if project.get("station_layout") == layout and project.get("stations") == stations:
                return env
            project["stations"] = stations
            project["station_layout"] = layout
            action = f"評価断面を設定（圧縮機{layout['compressor_stages']}段・タービン{layout['turbine_stages']}段）"
            return self._write(db, project, revision, action)

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

    def _revision_project(self, db, project_id, source_revision):
        if type(source_revision) is not int or not 1 <= source_revision <= 2**63 - 1:
            raise ValueError("紐付ける設計版を指定してください。")
        snapshot = db.execute("SELECT document FROM history WHERE project_id=? AND revision=?",
                              (project_id, source_revision)).fetchone()
        if snapshot is None:
            raise LookupError("指定した設計版が見つかりません。")
        return json.loads(snapshot["document"])

    def result_targets(self, project_id, source_revision):
        project_id = identifier(project_id)
        with self.connect() as db:
            snapshot = self._revision_project(db, project_id, source_revision)
        return [{"id": part["id"], "name": part["name"]} for part in snapshot["components"]]

    @staticmethod
    def _result_summary(row, revision):
        metadata = json.loads(row["metadata"])
        if row["kind"] == "blade" and "stage1_share" not in metadata:
            handoff = row["legacy_handoff"] if "legacy_handoff" in row.keys() else row["handoff"]
            metadata["stage1_share"] = blade_stage1_share(json.loads(handoff))
        return {**metadata, "id": row["id"], "kind": row["kind"],
                "component_id": row["component_id"], "source_revision": row["source_revision"],
                "saved_at": row["saved_at"], "outdated": row["source_revision"] != revision}

    @staticmethod
    def _mark_performance_status(item, replacements):
        for design in item["designs"].values():
            design["replaced_by_blade_id"] = replacements.get(design["blade_result_id"])
        item["outdated"] = item["outdated"] or any(
            design["replaced_by_blade_id"] for design in item["designs"].values())

    def _registered_result_summary(self, db, row, revision):
        item = self._result_summary(row, revision)
        if item["kind"] == "performance":
            blade_ids = [design["blade_result_id"] for design in item["designs"].values()]
            successors = db.execute("""
                SELECT id, json_extract(metadata, '$.replaces_blade_id') AS old_id
                FROM design_results WHERE project_id=? AND kind='blade'
                    AND json_extract(metadata, '$.replaces_blade_id') IN (?,?)
                """, (row["project_id"], *blade_ids))
            self._mark_performance_status(item, {entry["old_id"]: entry["id"] for entry in successors})
        return item

    def design_results(self, project_id):
        project_id = identifier(project_id)
        with self.connect() as db:
            db.execute("BEGIN")
            revision = self.get(project_id, db)["revision"]
            rows = db.execute("""
                SELECT id, kind, component_id, source_revision, metadata, saved_at,
                       CASE WHEN kind='blade' AND json_type(metadata, '$.stage1_share') IS NULL
                            THEN handoff END AS legacy_handoff
                FROM design_results WHERE project_id=? ORDER BY saved_at DESC, rowid DESC
                """, (project_id,))
            items = [self._result_summary(row, revision) for row in rows]
        cycles = {(item["source_revision"], item["run_id"], item["input_hash"]): item["id"]
                  for item in items if item["kind"] == "cycle"}
        replacements = {item["replaces_blade_id"]: item["id"] for item in items if item.get("replaces_blade_id")}
        for item in items:
            if item["kind"] == "blade":
                item["cycle_result_id"] = cycles.get((item["source_revision"], item["run_id"], item["input_hash"]))
                item["replaced_by_blade_id"] = replacements.get(item["id"])
            if item["kind"] == "performance":
                self._mark_performance_status(item, replacements)
        return {"revision": revision, "results": items}

    def _linked_blade(self, db, project_id, component_id, source_revision, blade_id, expected_share):
        row = db.execute(
            "SELECT * FROM design_results WHERE id=? AND project_id=? AND kind='blade' AND component_id=? AND source_revision=?",
            (blade_id, project_id, component_id, source_revision),
        ).fetchone()
        if row is None:
            raise ValueError("同じ案件・部品・設計版の翼形状を選択してください。")
        shape = self._result_summary(row, source_revision)
        if shape["stage1_share"] != expected_share:
            raise ValueError("選択した翼形状の第1段全圧比配分が一致しません。")
        return shape

    def register_result(self, project_id, revision, source_revision, component_id, kind, content, handoff=None, *,
                        blade_ids=None, replaces_blade_id=None):
        project_id = identifier(project_id)
        component_id = identifier(component_id) if component_id != "" else ""
        imported = parse_result(kind, content, handoff)
        metadata = imported.metadata
        if kind == "performance":
            if not isinstance(blade_ids, dict) or set(blade_ids) != PERFORMANCE_DESIGNS.keys():
                raise ValueError("均等案と75%案それぞれの翼形状を選択してください。")
            blade_ids = {key: identifier(value) for key, value in blade_ids.items()}
        elif blade_ids is not None:
            raise ValueError("2案の翼形状は性能比較を登録するときに指定してください。")
        if replaces_blade_id:
            if kind != "blade":
                raise ValueError("差し替え元は翼形状を登録するときに指定してください。")
            replaces_blade_id = identifier(replaces_blade_id)
        context = metadata["context"]
        for key, value in (("project_id", project_id), ("component_id", component_id), ("source_revision", source_revision)):
            if context[key] is not None and context[key] != value:
                raise ValueError("成果物に記録された案件・部品・設計版と登録先が一致しません。")
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            current = self.get(project_id, db)["revision"]
            if type(revision) is not int or revision != current:
                raise Conflict("別の画面で案件が更新されました。画面を再読込して登録先を確認してください。")
            snapshot = self._revision_project(db, project_id, source_revision)
            part = next((p for p in snapshot["components"] if p["id"] == component_id), None)
            if component_id and part is None:
                raise ValueError("選択した設計版にこの部品はありません。")
            existing = db.execute("SELECT * FROM design_results WHERE project_id=? AND kind=? AND identity=?",
                                  (project_id, kind, imported.identity)).fetchone()
            if existing is not None:
                previous = parse_result(kind, existing["content"], existing["handoff"])
                if existing["component_id"] != component_id or previous.comparison != imported.comparison:
                    raise Conflict("同じ成果物IDの登録済みデータが、今回の内容または対象部品と一致しません。")
                saved = self._registered_result_summary(db, existing, current)
                if kind == "performance" and blade_ids != {
                        key: item["blade_result_id"] for key, item in saved["designs"].items()}:
                    raise Conflict("登録済みの性能比較に紐付く翼形状は変更できません。")
                if replaces_blade_id and replaces_blade_id != saved.get("replaces_blade_id"):
                    raise Conflict("登録済みの翼形状の差し替え元は変更できません。")
                return {"created": False, "result": saved}

            if kind == "performance":
                for key, share in PERFORMANCE_DESIGNS.items():
                    shape = self._linked_blade(db, project_id, component_id, source_revision, blade_ids[key], share)
                    metadata["designs"][key].update(blade_result_id=shape["id"], project_sha256=shape["project_sha256"])
            if replaces_blade_id:
                if metadata["stage1_share"] is None:
                    raise ValueError("差し替える翼形状には第1段全圧比配分の記録が必要です。")
                old = self._linked_blade(db, project_id, component_id, source_revision, replaces_blade_id,
                                         metadata["stage1_share"])
                if old["project_sha256"] == metadata["project_sha256"]:
                    raise ValueError("差し替え元と同じ翼形状です。")
                if db.execute("""SELECT 1 FROM design_results WHERE project_id=? AND kind='blade'
                                  AND json_extract(metadata, '$.replaces_blade_id')=?""",
                              (project_id, replaces_blade_id)).fetchone():
                    raise Conflict("指定した翼形状はすでに差し替え済みです。最新版を選択してください。")
                metadata["replaces_blade_id"] = replaces_blade_id

            metadata["component_name"] = part["name"] if part else "案件全体"
            result_id = uid()
            db.execute("INSERT INTO design_results VALUES(?,?,?,?,?,?,?,?,?,?)",
                       (result_id, project_id, component_id, source_revision, kind, imported.identity,
                        json.dumps(metadata, ensure_ascii=False, allow_nan=False), content, handoff, now()))
            row = db.execute("SELECT * FROM design_results WHERE id=?", (result_id,)).fetchone()
            return {"created": True, "result": self._registered_result_summary(db, row, current)}

    def result_file(self, project_id, result_id, part):
        with self.connect() as db:
            row = db.execute("SELECT kind, metadata, content, handoff FROM design_results WHERE project_id=? AND id=?",
                             (identifier(project_id), identifier(result_id))).fetchone()
        if row is not None:
            if part == "primary":
                return json.loads(row["metadata"])["filename"], row["content"]
            if part == "handoff" and row["handoff"] is not None:
                return "blade-project.handoff.json", row["handoff"]
            if row["kind"] == "performance" and part in PERFORMANCE_DESIGNS:
                case = json.loads(row["content"])["cycle_cases"][part]
                return f"two-stage-cycle-{part}.json", json.dumps(case, ensure_ascii=False, indent=2).encode("utf-8")
        raise LookupError("成果物ファイルが見つかりません。")
