"""SQLite persistence, immutable snapshots, portable project exchange."""
import base64
import copy
from io import BytesIO
import json
import sqlite3
from contextlib import contextmanager
from hashlib import sha256
from pathlib import Path
from zipfile import BadZipFile, ZipFile

from model import identifier, new_project, now, text, uid, validate_project
from mbse import METRICS, OPERATORS, VERDICTS, basis, clear_old_approvals, numeric, same_source_design, suite_observation
from results import (
    PERFORMANCE_DESIGNS, archive_suite, blade_stage1_share, compare_suites, parse as parse_result, verify_suite_files,
)
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
                CREATE TABLE IF NOT EXISTS suite_bundles (
                    result_id TEXT PRIMARY KEY REFERENCES design_results(id),
                    archive BLOB NOT NULL);
                CREATE TABLE IF NOT EXISTS mbse_evidence (
                    id TEXT PRIMARY KEY, project_id TEXT NOT NULL, case_id TEXT NOT NULL,
                    document TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS mbse_evidence_project ON mbse_evidence(project_id, case_id);
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

    def _write(self, db, project, revision, action, *, restoring=False):
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
            if not restoring:
                clear_old_approvals(json.loads(old["document"]), project)
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
            return self._write(db, json.loads(row["document"]), revision, f"リビジョン {row['revision']} を復元", restoring=True)

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

    def verification_status(self, project_id):
        project_id = identifier(project_id)
        with self.connect() as db:
            db.execute("BEGIN")
            env = self.get(project_id, db)
            project = env["project"]
            runs = {}
            for row in db.execute("SELECT case_id,document FROM mbse_evidence WHERE project_id=? ORDER BY rowid DESC", (project_id,)):
                runs.setdefault(row["case_id"], []).append(json.loads(row["document"]))
        cases = []
        for case in project.get("mbse", {}).get("cases", []):
            digest, parts, parameters = basis(project, case)
            records = runs.pop(case["id"], [])
            latest = records[0] if records else None
            state = "pending"
            if latest:
                state = latest["verdict"] if latest["fingerprint"] == digest else "stale"
            cases.append({"id": case["id"], "state": state, "component_ids": parts,
                          "parameter_ids": parameters, "records": records})
        archived = sorted((run for records in runs.values() for run in records),
                          key=lambda run: run["saved_at"], reverse=True)
        return {"revision": env["revision"], "cases": cases, "archived": archived}

    def register_verification(self, project_id, revision, data):
        project_id = identifier(project_id)
        case_id = identifier(data.get("case_id"))
        artifact_id = identifier(data.get("artifact_id"))
        note = text(data.get("note", ""), "条件の照合・判断根拠", 2000, True)
        if data.get("conditions_confirmed") is not True:
            raise ValueError("検証ケースと根拠の運転条件・構成を照合してください。")
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            env = self.get(project_id, db)
            if type(revision) is not int or revision != env["revision"]:
                raise Conflict("案件が更新されました。再読込して検証結果を登録してください。")
            project = env["project"]
            model = project.get("mbse", {})
            case = next((row for row in model.get("cases", []) if row["id"] == case_id), None)
            if case is None:
                raise LookupError("検証ケースが見つかりません。")
            requirement = next(row for row in project["requirements"] if row["id"] == case["requirement_id"])
            trace = next(row for row in model["traces"] if row["id"] == requirement["id"])
            mode = next(row for row in model["modes"] if row["id"] == case["mode_id"])
            digest, _, _ = basis(project, case)
            kind = data.get("artifact_kind")
            cycle_source = None
            if kind == "result":
                row = db.execute("SELECT * FROM design_results WHERE id=? AND project_id=? AND kind='suite'",
                                 (artifact_id, project_id)).fetchone()
                bundle = db.execute("SELECT archive FROM suite_bundles WHERE result_id=?", (artifact_id,)).fetchone()
                if row is None or bundle is None:
                    raise ValueError("詳細一式を登録した定格通しケースを選んでください。")
                if case["method"] != "analysis":
                    raise ValueError("試験・検査・実演の検証には該当する資料を指定してください。")
                source_revision = row["source_revision"]
                source_project = self._revision_project(db, project_id, source_revision)
                if not same_source_design(source_project, project, case):
                    raise ValueError("成果物の設計版と現在の検証条件・関連設計が一致しません。")
                original = row["content"]
                manifest = json.loads(original)
                source = manifest.get("source_kind")
                if source not in ("synthetic", "analysis"):
                    raise ValueError("定格通しケースの出典区分を確認してください。")
                cycle_source = {key: manifest["source_cycle"][key] for key in ("run_id", "input_hash")}
                name = json.loads(row["metadata"])["name"]
                file_name = "suite-report.json"
                observed, unit = None, ""
                if case["metric"]:
                    file_name = METRICS[case["metric"]][2]
                    try:
                        with ZipFile(BytesIO(bundle["archive"])) as archive:
                            original = archive.read(file_name)
                    except (BadZipFile, KeyError):
                        raise ValueError("判定に使う詳細JSONを取得できません。") from None
                    observed, unit = suite_observation(json.loads(original), case["metric"])
            elif kind == "attachment":
                if case["metric"]:
                    raise ValueError("自動取得する指標には定格通しケースを指定してください。")
                name, original = self.file(project_id, artifact_id, db)
                file_name = name
                source_revision = revision
                source = data.get("source_kind")
                if source not in (case["method"], "synthetic"):
                    raise ValueError("資料の出典を検証方法に合わせてください。")
                observed, unit = None, ""
            else:
                raise ValueError("根拠資料の種類を確認してください。")
            threshold = None
            if case["comparison"]:
                threshold = numeric(requirement["value"])
                if observed is None:
                    observed, unit = numeric(text(data.get("observed", ""), "確認値", 100, True)), requirement["unit"]
                comparison = OPERATORS[case["comparison"]][1]
                verdict = "pass" if comparison(observed, threshold) else "fail"
            else:
                verdict = data.get("verdict")
                if verdict not in VERDICTS:
                    raise ValueError("適合・不適合・判定保留から選択してください。")
            record = {"id": uid(), "case_id": case_id, "case_name": case["name"], "design_revision": revision,
                      "requirement_code": trace["code"], "requirement_name": requirement["name"], "mode": mode["name"],
                      "conditions": mode["conditions"], "method": case["method"], "comparison": case["comparison"],
                      "fingerprint": digest, "verdict": verdict, "value": observed, "unit": unit, "threshold": threshold,
                      "artifact_id": artifact_id, "artifact_kind": kind, "artifact_name": name, "file_name": file_name,
                      "artifact_sha256": sha256(original).hexdigest(), "source_revision": source_revision,
                      "source_kind": source, "cycle_source": cycle_source, "note": note, "saved_at": now()}
            # Identical submission of the same evidence is safe to retry.
            latest = db.execute("SELECT document FROM mbse_evidence WHERE project_id=? AND case_id=? ORDER BY rowid DESC LIMIT 1",
                                (project_id, case_id)).fetchone()
            stable = record.keys() - {"id", "saved_at", "design_revision"}
            if latest is not None:
                previous = json.loads(latest["document"])
                if all(previous[key] == record[key] for key in stable):
                    return previous
            db.execute("INSERT INTO mbse_evidence (id,project_id,case_id,document) VALUES(?,?,?,?)",
                       (record["id"], project_id, case_id, json.dumps(record, ensure_ascii=False, allow_nan=False)))
            return record

    def verification_file(self, project_id, evidence_id):
        with self.connect() as db:
            row = db.execute("SELECT document FROM mbse_evidence WHERE id=? AND project_id=?",
                             (identifier(evidence_id), identifier(project_id))).fetchone()
            if row is None:
                raise LookupError("検証記録が見つかりません。")
            record = json.loads(row["document"])
            if record["artifact_kind"] == "attachment":
                result = db.execute("SELECT name,content FROM files WHERE id=? AND project_id=?",
                                    (record["artifact_id"], project_id)).fetchone()
                content = result["content"] if result is not None else None
            else:
                result = db.execute("SELECT archive FROM suite_bundles JOIN design_results ON design_results.id=result_id "
                                    "WHERE result_id=? AND project_id=?", (record["artifact_id"], project_id)).fetchone()
                content = None
                if result is not None:
                    try:
                        with ZipFile(BytesIO(result["archive"])) as archive:
                            content = archive.read(record["file_name"])
                    except (BadZipFile, KeyError):
                        raise LookupError("記録した根拠ファイルを確認できません。") from None
            if content is None or sha256(content).hexdigest() != record["artifact_sha256"]:
                raise LookupError("記録した根拠ファイルを確認できません。")
            return record["file_name"], content

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
        if row["kind"] == "suite" and "bundle_available" in row.keys():
            metadata["bundle_available"] = bool(row["bundle_available"])
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

    @staticmethod
    def _link_station_review_blade(db, project_id, item):
        compressor = item["geometry"]["compressor"]
        blade = db.execute("""
            SELECT saved.id, EXISTS (
                SELECT 1 FROM design_results AS successor WHERE successor.project_id=saved.project_id
                  AND successor.kind='blade' AND json_extract(successor.metadata, '$.replaces_blade_id')=saved.id
            ) AS replaced
            FROM design_results AS saved WHERE saved.project_id=? AND saved.kind='blade' AND saved.source_revision=?
              AND json_extract(saved.metadata, '$.project_sha256')=? AND json_extract(saved.metadata, '$.run_id')=?
              AND json_extract(saved.metadata, '$.input_hash')=?
            ORDER BY saved.saved_at DESC, saved.rowid DESC LIMIT 1
            """, (project_id, item["source_revision"], compressor["geometry_sha256"], compressor["run_id"],
                  compressor["input_hash"])).fetchone()
        item["blade_result_id"] = blade["id"] if blade is not None else None
        if blade is not None and blade["replaced"]:
            item["outdated"] = True

    def _registered_result_summary(self, db, row, revision):
        item = self._result_summary(row, revision)
        if item["kind"] == "suite":
            item["bundle_available"] = db.execute("SELECT 1 FROM suite_bundles WHERE result_id=?",
                                                   (row["id"],)).fetchone() is not None
        if item["kind"] == "station-review":
            self._link_station_review_blade(db, row["project_id"], item)
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
                       EXISTS (SELECT 1 FROM suite_bundles WHERE result_id=design_results.id) AS bundle_available,
                       CASE WHEN kind='blade' AND json_type(metadata, '$.stage1_share') IS NULL
                            THEN handoff END AS legacy_handoff
                FROM design_results WHERE project_id=? ORDER BY saved_at DESC, rowid DESC
                """, (project_id,))
            items = [self._result_summary(row, revision) for row in rows]
            for item in items:
                if item["kind"] == "station-review":
                    self._link_station_review_blade(db, project_id, item)
        cycles = {(item["source_revision"], item["run_id"], item["input_hash"]): item["id"]
                  for item in items if item["kind"] == "cycle"}
        replacements = {item["replaces_blade_id"]: item["id"] for item in items if item.get("replaces_blade_id")}
        for item in items:
            if item["kind"] == "cooling":
                item["cycle_result_id"] = cycles.get((item["source_revision"], item["run_id"], item["input_hash"]))
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
                        blade_ids=None, replaces_blade_id=None, suite_files=None):
        project_id = identifier(project_id)
        component_id = identifier(component_id) if component_id != "" else ""
        imported = parse_result(kind, content, handoff)
        metadata = imported.metadata
        if suite_files is not None:
            if kind != "suite":
                raise ValueError("詳細レポートは定格通しケースの登録時に指定してください。")
            verify_suite_files(imported, suite_files, len(content))
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
        if kind == "station-review":
            for geometry in metadata["geometry"].values():
                context = geometry["context"]
                if (context["project_id"] not in (None, project_id)
                        or context["source_revision"] not in (None, source_revision)):
                    raise ValueError("評価断面の入力元の案件または設計版が登録先と一致しません。")
        context = metadata["context"]
        for key, value in (("project_id", project_id), ("component_id", component_id), ("source_revision", source_revision)):
            if kind == "cooling" and key == "component_id":
                continue  # The source cycle's part can differ from the receiving turbine part.
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
            if kind == "cooling":
                stations = {s["code"]: s for s in snapshot.get("stations", []) if s["group"] == "turbine"}
                for row in metadata["injections"]:
                    station = stations.get(row["station"])
                    if station is None:
                        raise ValueError(f"選択した設計版にタービン断面 {row['station']} がありません。")
                    row["station_id"] = station["id"]
                    row["station_name"] = station["name"]
            if kind == "station-review":
                station_index = {row["code"]: (index, row) for index, row in enumerate(snapshot.get("stations", []))}
                for row in metadata["stations"]:
                    _, station = station_index.get(row["code"], (None, None))
                    group = "compressor" if row["code"].startswith("CP-") else "turbine"
                    if station is None or station["group"] != group:
                        raise ValueError(f"選択した設計版に評価断面 {row['code']} がありません。")
                    row.update(station_id=station["id"], station_name=station["name"])
                metadata["stations"].sort(key=lambda row: station_index[row["code"]][0])
            existing = db.execute("SELECT * FROM design_results WHERE project_id=? AND kind=? AND identity=?",
                                  (project_id, kind, imported.identity)).fetchone()
            if existing is not None:
                previous = parse_result(kind, existing["content"], existing["handoff"])
                if (existing["component_id"] != component_id or previous.comparison != imported.comparison
                        or (kind == "cooling" or suite_files is not None) and existing["source_revision"] != source_revision):
                    raise Conflict("同じ成果物IDの登録済みデータが、今回の内容または対象部品と一致しません。")
                saved = self._registered_result_summary(db, existing, current)
                if kind == "performance" and blade_ids != {
                        key: item["blade_result_id"] for key, item in saved["designs"].items()}:
                    raise Conflict("登録済みの性能比較に紐付く翼形状は変更できません。")
                if replaces_blade_id and replaces_blade_id != saved.get("replaces_blade_id"):
                    raise Conflict("登録済みの翼形状の差し替え元は変更できません。")
                if suite_files is not None and not saved["bundle_available"]:
                    db.execute("INSERT INTO suite_bundles VALUES (?,?)",
                               (existing["id"], archive_suite(existing["content"], suite_files)))
                    saved["bundle_available"] = True
                    return {"created": False, "bundle_added": True, "result": saved}
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
            if suite_files is not None:
                db.execute("INSERT INTO suite_bundles VALUES (?,?)", (result_id, archive_suite(content, suite_files)))
            row = db.execute("SELECT * FROM design_results WHERE id=?", (result_id,)).fetchone()
            return {"created": True, "result": self._registered_result_summary(db, row, current)}

    def result_file(self, project_id, result_id, part):
        with self.connect() as db:
            row = db.execute("SELECT kind, metadata, content, handoff FROM design_results WHERE project_id=? AND id=?",
                             (identifier(project_id), identifier(result_id))).fetchone()
            if row is not None and row["kind"] == "suite" and part in ("bundle", "support-review"):
                bundle = db.execute("SELECT archive FROM suite_bundles WHERE result_id=?", (result_id,)).fetchone()
                if bundle is not None:
                    if part == "bundle":
                        return "suite-design-point.zip", bundle["archive"]
                    try:
                        with ZipFile(BytesIO(bundle["archive"])) as archive:
                            return "support-review.json", archive.read("support-review.json")
                    except (BadZipFile, KeyError):
                        raise LookupError("定格レビューのJSONが見つかりません。") from None
        if row is not None:
            if part == "primary":
                return json.loads(row["metadata"])["filename"], row["content"]
            if part == "handoff" and row["handoff"] is not None:
                return "blade-project.handoff.json", row["handoff"]
            if row["kind"] == "performance" and part in PERFORMANCE_DESIGNS:
                case = json.loads(row["content"])["cycle_cases"][part]
                return f"two-stage-cycle-{part}.json", json.dumps(case, ensure_ascii=False, indent=2).encode("utf-8")
        raise LookupError("成果物ファイルが見つかりません。")

    def suite_comparison(self, project_id, reference_id, candidate_id):
        project_id = identifier(project_id)
        ids = [identifier(reference_id), identifier(candidate_id)]
        if ids[0] == ids[1]:
            raise ValueError("比較する2件を個別に選択してください。")
        with self.connect() as db:
            db.execute("BEGIN")
            revision = self.get(project_id, db)["revision"]
            rows = {row["id"]: row for row in db.execute("""
                SELECT results.id, results.kind, results.metadata, results.component_id, results.source_revision,
                       results.saved_at, results.content, bundles.archive
                FROM design_results AS results LEFT JOIN suite_bundles AS bundles ON bundles.result_id=results.id
                WHERE results.project_id=? AND results.kind='suite' AND results.id IN (?,?)
                """, (project_id, *ids))}
            if len(rows) != 2:
                raise LookupError("同じ案件に登録した定格通しケースを選択してください。")
            if any(rows[result_id]["archive"] is None for result_id in ids):
                raise ValueError("両方のケースに詳細レポートを登録してください。")
            cases = [{**self._result_summary(rows[result_id], revision),
                      "source_kind": json.loads(rows[result_id]["content"]).get("source_kind")} for result_id in ids]
            for case in cases:
                related = db.execute("""
                    SELECT blade.id, successor.id AS replaced_by
                    FROM design_results AS blade LEFT JOIN design_results AS successor
                      ON successor.project_id=blade.project_id AND successor.kind='blade'
                      AND json_extract(successor.metadata, '$.replaces_blade_id')=blade.id
                    WHERE blade.project_id=? AND blade.kind='blade' AND blade.source_revision=?
                      AND json_extract(blade.metadata, '$.run_id')=? AND json_extract(blade.metadata, '$.input_hash')=?
                    ORDER BY blade.saved_at, blade.rowid
                    """, (project_id, case["source_revision"], case["run_id"], case["input_hash"]))
                case["related_blades"] = [dict(row) for row in related]
        return {"cases": cases, **compare_suites(*(rows[result_id]["archive"] for result_id in ids))}
