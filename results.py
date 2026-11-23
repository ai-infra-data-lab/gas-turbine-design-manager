"""Validate and identify immutable results produced by the independent apps."""
from dataclasses import dataclass
from hashlib import sha256
from io import BytesIO
import json
from math import isfinite
import re
from sys import float_info
from zipfile import BadZipFile, ZIP_DEFLATED, ZipFile

from model import identifier, text


MAX_ARTIFACT_BYTES = 5 * 1024 * 1024
MAX_SUITE_BYTES = 30 * 1024 * 1024
MAX_SUITE_FILES = 40
SHA256 = re.compile(r"[0-9a-f]{64}\Z")
CYCLE_MODULE = "gt.cycle.simple_single_shaft"
PERFORMANCE_DESIGNS = {"equal": .5, "75-percent": .75}
PERFORMANCE_FORMAT = "centrifugal-two-stage-performance-comparison"


@dataclass(frozen=True)
class Result:
    identity: str
    metadata: dict
    comparison: dict


def _object(value, label):
    if not isinstance(value, dict):
        raise ValueError(f"{label}を確認してください。")
    return value


def _bad_number(_):
    raise ValueError("成果物に有限でない数値があります。")


def _unique_fields(pairs):
    output = {}
    for key, value in pairs:
        if key in output:
            raise ValueError("成果物のJSONに重複した項目名があります。")
        output[key] = value
    return output


def _decode(content):
    if not isinstance(content, bytes) or not 0 < len(content) <= MAX_ARTIFACT_BYTES:
        raise ValueError("成果物は1ファイル5MB以内のJSONを指定してください。")
    try:
        value = json.loads(content.decode("utf-8-sig"), parse_constant=_bad_number, object_pairs_hook=_unique_fields)
        # JSON exponent overflow also becomes infinity in a standard decoder.
        json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8")
    except (UnicodeError, json.JSONDecodeError, RecursionError):
        raise ValueError("UTF-8の成果物JSONを読み込めませんでした。") from None
    return _object(value, "成果物JSON")


def _digest(value, label):
    if not isinstance(value, str) or SHA256.fullmatch(value) is None:
        raise ValueError(f"{label}のSHA-256が不正です。")
    return value


def _version(value, maximum):
    if type(value) is not int or not 1 <= value <= maximum:
        raise ValueError("この成果物の交換形式の版には対応していません。")
    return value


def _context(value):
    if value is None:
        value = {}
    obj = _object(value, "入力元の案件情報")
    project = obj.get("project_id")
    component = obj.get("component_id")
    revision = obj.get("source_revision")
    if project is None and (component is not None or revision is not None):
        raise ValueError("入力元の案件IDがありません。")
    if revision is not None and (type(revision) is not int or revision < 1):
        raise ValueError("入力元の設計版が不正です。")
    return {"project_id": identifier(project) if project is not None else None,
            "component_id": identifier(component) if component is not None else None,
            "source_revision": revision}


def blade_stage1_share(reference):
    value = reference.get("stage1_share")
    if value is not None and (type(value) not in (int, float) or not 0 < value < 1):
        raise ValueError("翼形状の第1段全圧比配分が不正です。")
    return value


def _number(value, label, minimum=-float_info.max, maximum=float_info.max):
    if type(value) not in (int, float) or not minimum <= value <= maximum:
        raise ValueError(f"{label}が不正です。")
    return value


def _cycle(document):
    wrapped = document.get("format") == "centrifugal-two-stage-design"
    if wrapped:
        _version(document.get("schema_version"), 1)
    case = _object(document.get("cycle_case") if wrapped else document, "サイクルケース")
    if case.get("format") != "gas-turbine-cycle-case" or case.get("module_id") != CYCLE_MODULE:
        raise ValueError("Cycle StudioのサイクルケースJSONか、2段設計一式JSONを指定してください。")
    schema = _version(case.get("schema_version"), 6)
    run = identifier(case.get("run_id"))
    digest = _digest(case.get("input_hash"), "サイクル入力")
    inputs = _object(case.get("input"), "サイクル入力")
    result = _object(case.get("result"), "サイクル結果")
    if result.get("status") not in ("success", "non_generating"):
        raise ValueError("完了したサイクル結果を指定してください。")
    metrics = _object(result.get("metrics"), "サイクル性能値")
    summary = {}
    for key in ("net_electric_power_w", "air_mass_flow_kg_s"):
        value = metrics.get(key)
        if value is not None:
            if type(value) not in (int, float) or not -float_info.max <= value <= float_info.max:
                raise ValueError("サイクル性能値が不正です。")
            summary[key] = value
    metadata = {"name": text(inputs.get("name"), "ケース名", 200, True), "module_id": CYCLE_MODULE,
                "module_version": text(case.get("module_version"), "モジュール版", 80, True), "schema_version": schema,
                "run_id": run, "input_hash": digest, "context": _context(inputs.get("context")),
                "status": result["status"], "metrics": summary,
                "filename": "two-stage-design.json" if wrapped else "cycle-case.json"}
    return Result(run, metadata, case)


def _blade(document, content, handoff):
    if document.get("schema_version") not in ("0.3", "0.4"):
        raise ValueError("Blade Designerの編集用プロジェクトJSONを指定してください。")
    reference = _decode(handoff)
    if reference.get("format") != "blade-designer-two-stage-handoff":
        raise ValueError("対応する手渡し記録 .handoff.json を指定してください。")
    _version(reference.get("schema_version"), 1)
    digest = _digest(reference.get("project_sha256"), "翼形状")
    if sha256(content).hexdigest() != digest:
        raise ValueError("翼形状JSONと手渡し記録のSHA-256が一致しません。")
    rows = document.get("blade_rows")
    recorded = reference.get("rows")
    if not isinstance(rows, list) or not rows or not isinstance(recorded, list):
        raise ValueError("翼形状と手渡し記録の翼列情報を確認してください。")
    row_ids = [_object(row, "翼列").get("identifier") for row in rows]
    if (any(not isinstance(key, str) or not key for key in row_ids) or len(set(row_ids)) != len(row_ids)
            or row_ids != [_object(row, "手渡し記録の翼列").get("id") for row in recorded]):
        raise ValueError("翼形状JSONと手渡し記録で翼列が一致しません。")
    run = identifier(reference.get("allocated_cycle_run_id"))
    source = _digest(reference.get("allocated_cycle_input_hash"), "配分後のサイクル入力")
    metadata = {"name": f"2段圧縮機 · {len(rows)}要素", "module_id": "blade-designer",
                "schema_version": document["schema_version"], "handoff_schema_version": reference["schema_version"],
                "cycle_module_version": text(reference.get("cycle_module_version"), "サイクルモジュール版", 80, True),
                "run_id": run, "input_hash": source, "context": _context(reference.get("cycle_context")),
                "base_cycle_run_id": identifier(reference.get("source_run_id")),
                "base_cycle_input_hash": _digest(reference.get("source_input_hash"), "配分前のサイクル入力"),
                "source_report_content_sha256": _digest(reference.get("source_report_content_sha256"), "設計一式"),
                "project_sha256": digest, "row_count": len(rows), "status": "geometry",
                "stage1_share": blade_stage1_share(reference),
                "filename": "blade-project.json"}
    return Result(f"{run}:{digest}", metadata, {key: value for key, value in reference.items() if key != "project_file"})


def _performance(document):
    if document.get("format") != PERFORMANCE_FORMAT:
        raise ValueError("Cycle StudioのCFD・試験性能の比較一式JSONを指定してください。")
    _version(document.get("schema_version"), 1)
    performance = _object(document.get("performance"), "2段圧縮機性能")
    if performance.get("format") != "centrifugal-two-stage-performance":
        raise ValueError("比較一式に元の性能JSONがありません。")
    _version(performance.get("schema_version"), 1)
    sources = _object(performance.get("designs"), "比較する性能データ")
    cases = _object(document.get("cycle_cases"), "再計算したケース")
    if set(cases) != PERFORMANCE_DESIGNS.keys() or set(sources) != PERFORMANCE_DESIGNS.keys():
        raise ValueError("均等案と75%案の性能・サイクルケースを指定してください。")
    conditions = _object(performance.get("conditions"), "共通運転条件")
    operating_point = {key: _number(conditions.get(key), key, 0) for key in (
        "inlet_total_temperature_k", "inlet_total_pressure_pa", "air_mass_flow_kg_s", "shaft_speed_rpm")}
    designs, contexts = {}, []
    for key in PERFORMANCE_DESIGNS:
        source = _object(sources[key], "性能の出典")
        source_kind = source.get("source_kind")
        if source_kind not in ("cfd", "test", "synthetic"):
            raise ValueError("性能の出典区分が不正です。")
        case = _object(cases[key], "再計算したサイクルケース")
        if case.get("format") != "gas-turbine-cycle-case":
            raise ValueError("再計算したサイクルケースJSONを確認してください。")
        recorded = _cycle(case)
        contexts.append(recorded.metadata["context"])
        result = recorded.comparison["result"]
        trains = _object(result.get("stage_results"), "サイクルの段別結果")
        train = _object(trains.get("compressor"), "圧縮機の段別結果")
        metrics = result["metrics"]
        net_power = metrics.get("net_electric_power_w")
        sfc = metrics.get("specific_fuel_consumption_kg_kwh")
        designs[key] = {
            "source_kind": source_kind,
            "source_reference": text(source.get("source_reference"), "性能の出典", 200, True),
            "run_id": recorded.identity, "input_hash": recorded.metadata["input_hash"],
            "module_version": recorded.metadata["module_version"],
            "specific_work_j_kg": _number(train.get("specific_work_j_kg"), "圧縮比仕事", 0),
            "net_electric_power_w": None if net_power is None else _number(net_power, "正味出力"),
            "specific_fuel_consumption_kg_kwh": None if sfc is None else _number(sfc, "正味燃料消費率", 0),
        }
    runs = [design["run_id"] for design in designs.values()]
    if contexts[0] != contexts[1] or runs[0] == runs[1]:
        raise ValueError("2案の入力元の案件情報またはサイクル実行IDを確認してください。")
    metadata = {"name": "2段圧縮機のCFD・試験性能比較", "module_id": CYCLE_MODULE, "schema_version": 1,
                "base_cycle_run_id": identifier(document.get("source_run_id")),
                "base_cycle_input_hash": _digest(document.get("source_input_hash"), "基準のサイクル入力"),
                "context": contexts[0], "conditions": operating_point, "designs": designs,
                "filename": "two-stage-performance-comparison.json"}
    return Result(":".join(runs), metadata, document)


def parse(kind, content, handoff=None):
    document = _decode(content)
    if kind == "cycle" and handoff is None:
        return _cycle(document)
    if kind == "blade" and handoff is not None:
        return _blade(document, content, handoff)
    if kind == "performance" and handoff is None:
        return _performance(document)
    if kind == "cooling" and handoff is None:
        return _cooling(document)
    if kind == "suite" and handoff is None:
        return _suite(document)
    if kind == "station-review" and handoff is None:
        return _station_review(document)
    raise ValueError("取込の種類に対応する設計成果物JSONを指定してください。")


def _cooling(document):
    if document.get("format") != "cycle-secondary-air-handoff":
        raise ValueError("二次空気の連携結果一式JSONを指定してください。")
    schema = _version(document.get("schema_version"), 1)
    source = _object(document.get("source_cycle"), "入力元のサイクル")
    network = _object(document.get("network_result"), "二次空気のネットワーク結果")
    if network.get("status") != "converged":
        raise ValueError("二次空気の収束済み結果を指定してください。")
    model = _object(network.get("model"), "二次空気のネットワーク入力")
    for key in ("edges", "nodes"):
        if not isinstance(network.get(key), list):
            raise ValueError("二次空気の流路とノードの結果が必要です。")
    edges = {edge.get("id"): edge for edge in (_object(row, "流路結果") for row in network["edges"])
             if isinstance(edge.get("id"), str)}
    boundaries = {node["id"]: node for node in (_object(row, "ノード結果") for row in network["nodes"])
                  if isinstance(node.get("id"), str) and node.get("kind") == "boundary"}
    injections = document.get("injections")
    if not isinstance(injections, list) or not 1 <= len(injections) <= 200:
        raise ValueError("二次空気の注入点を指定してください。")
    receivers, used_edges = [], set()
    for item in injections:
        row = _object(item, "注入点")
        station = text(row.get("station"), "タービン断面コード", 80, True)
        edge_id = text(row.get("edge_id"), "冷却流路ID", 80, True)
        edge = edges.get(edge_id)
        if edge is None or edge_id in used_edges or not isinstance(edge.get("downstream"), str):
            raise ValueError("注入点と冷却流路の対応が不正です。")
        used_edges.add(edge_id)
        node = _object(boundaries.get(edge["downstream"]), "冷却空気の排出境界")
        flow = _number(row.get("coolant_flow_kg_s"), "冷却流量", 0)
        temperature = _number(row.get("coolant_total_temperature_k"), "冷却空気の絶対全温", 1)
        if flow <= 0 or flow != edge.get("mass_flow_kg_s") or temperature != edge.get("outlet_absolute_total_temperature_k"):
            raise ValueError("冷却空気の流量・全温と流路結果が一致しません。")
        backpressure = _number(node.get("pressure_pa"), "排出先の絶対静圧", 1)
        if row.get("backpressure_static_pa", backpressure) != backpressure:
            raise ValueError("注入点の絶対静圧と排出境界が一致しません。")
        receivers.append({
            "station": station, "edge_id": edge_id, "flow_kg_s": flow, "total_temperature_k": temperature,
            "backpressure_pa": backpressure,
            "supply_total_pressure_pa": _number(row.get("pre_injection_total_pressure_pa"), "供給側の絶対全圧", 1),
        })
    canonical = json.dumps(document, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    metadata = {
        "name": text(model.get("name"), "ネットワーク名", 200, True), "module_id": "secondary-air-network",
        "schema_version": schema, "run_id": identifier(source.get("run_id")),
        "input_hash": _digest(source.get("input_hash"), "サイクル入力"),
        "context": _context(source.get("context")), "status": "converged", "injections": receivers,
        "cycle_feedback": text(document.get("cycle_feedback"), "サイクル反映状態", 300, True),
        "filename": "secondary-air-handoff.json",
    }
    if "operating_point" in document:
        point = _object(document["operating_point"], "運転点")
        metadata["operating_point"] = {
            key: _number(point.get(key), key, 0) for key in ("air_mass_flow_kg_s", "shaft_speed_rpm")
        }
    return Result(sha256(canonical).hexdigest(), metadata, document)


def _suite(document):
    if document.get("format") != "gas-turbine-suite-design-point":
        raise ValueError("設計アプリの定格通しケースの suite-report.json を指定してください。")
    schema = _version(document.get("schema_version"), 1)
    source = _object(document.get("source_cycle"), "入力元のサイクル")
    summary = _object(document.get("summary"), "設計点の要約")
    artifacts = _object(document.get("artifacts"), "計算レポート一覧")
    required = {"cycle-base", "combustor", "turbine", "cooling", "cycle-cooled", "thrust", "oil", "rotor"}
    if not required.issubset(artifacts) or len(artifacts) > MAX_SUITE_FILES:
        raise ValueError("定格通しケースに必要な計算レポートが不足しています。")
    for key, entry in artifacts.items():
        row = _object(entry, "計算レポートの識別情報")
        if re.fullmatch(r"[a-z][a-z0-9-]{0,60}", key) is None or row.get("file") != f"{key}.json":
            raise ValueError("計算レポート名が不正です。")
        _digest(row.get("sha256"), "計算レポート")
    run_id = identifier(source.get("run_id"))
    metrics = {key: _number(summary.get(key), key) for key in ("net_electric_power_w", "coolant_flow_kg_s")}
    metrics["coupling_iterations"] = _number(summary.get("coupling_iterations"), "冷却連成の反復回数", 1, 30)
    metadata = {"name": text(document.get("name"), "設計点の名称", 200, True),
                "module_id": "gas-turbine-suite-design-point", "schema_version": schema,
                "context": _context(source.get("context")), "run_id": run_id,
                "input_hash": _digest(source.get("input_hash"), "サイクル入力"),
                "metrics": metrics, "artifact_count": len(artifacts), "filename": "suite-report.json"}
    if artifacts.get("support-review", {}).get("format") == "gas-turbine-suite-support-review":
        metadata["support_review"] = True
    return Result(run_id, metadata, document)


def verify_suite_files(report, files, manifest_size):
    if not isinstance(files, dict) or any(not isinstance(name, str) or not isinstance(data, bytes)
                                         for name, data in files.items()):
        raise ValueError("詳細レポートをファイルで指定してください。")
    expected = {entry["file"]: entry for entry in report.comparison["artifacts"].values()}
    if set(files) != set(expected):
        missing = sorted(expected.keys() - files.keys())
        extra = sorted(files.keys() - expected.keys())
        details = []
        if missing:
            details.append(f"不足：{', '.join(missing)}")
        if extra:
            details.append(f"指定外：{', '.join(extra)}")
        raise ValueError(f"要約と詳細レポートのファイル一覧が一致しません。{'。'.join(details)}")
    if manifest_size + sum(len(data) for data in files.values()) > MAX_SUITE_BYTES:
        raise ValueError("要約と詳細レポートの合計は30MB以内です。")
    support_source = cooled_source = None
    for name, entry in expected.items():
        data = files[name]
        if sha256(data).hexdigest() != entry["sha256"]:
            raise ValueError(f"{name} のSHA-256が要約と一致しません。")
        document = _decode(data)
        if not isinstance(entry.get("format"), str) or document.get("format") != entry["format"]:
            raise ValueError(f"{name} の形式が要約と一致しません。")
        if "source_cycle" in entry and document.get("source_cycle") != entry["source_cycle"]:
            raise ValueError(f"{name} の入力元サイクルが要約と一致しません。")
        if name == "cycle-base.json":
            base = _cycle(document).metadata
            if any(base[key] != report.metadata[key] for key in ("run_id", "input_hash", "context")):
                raise ValueError("cycle-base.json の実行ID・入力ハッシュ・案件情報が要約と一致しません。")
        if name == "support-review.json":
            _version(document.get("schema_version"), 1)
            source = _object(document.get("source_cycle"), "定格レビューの入力元サイクル")
            support_source = {"run_id": identifier(source.get("run_id")),
                              "input_hash": _digest(source.get("input_hash"), "冷却後サイクル入力"),
                              "context": _context(source.get("context"))}
        if name == "cycle-cooled.json" and "support-review.json" in files:
            cooled = _cycle(document).metadata
            cooled_source = {key: cooled[key] for key in ("run_id", "input_hash", "context")}
    if support_source is not None and support_source != cooled_source:
        raise ValueError("定格レビューの入力元が冷却後サイクルと一致しません。")


def archive_suite(manifest_bytes, files):
    output = BytesIO()
    with ZipFile(output, "w", compression=ZIP_DEFLATED, compresslevel=1) as archive:
        archive.writestr("suite-report.json", manifest_bytes)
        for name, data in sorted(files.items()):
            archive.writestr(name, data)
    return output.getvalue()


def suite_support_document(archive):
    if "support-review.json" not in archive.namelist():
        return None
    report = _decode(archive.read("support-review.json"))
    if report.get("format") != "gas-turbine-suite-support-review":
        raise ValueError("定格レビューの形式を確認してください。")
    _version(report.get("schema_version"), 1)
    return report


def _station_review(document):
    if document.get("format") != "gas-turbine-station-review":
        raise ValueError("評価断面照合レポートのJSONを指定してください。")
    schema = _version(document.get("schema_version"), 1)
    sources = _object(document.get("sources"), "評価断面の出典")
    geometry = {}
    for machine in ("compressor", "turbine"):
        source = _object(sources.get(machine), "形状と計算の出典")
        geometry[machine] = {
            "geometry_sha256": _digest(source.get("geometry_sha256"), "翼形状"),
            "run_id": identifier(source.get("run_id")),
            "input_hash": _digest(source.get("input_hash"), "モデル入力"),
            "context": _context(source.get("context")),
        }
    csv_source = None
    if "pressure_csv" in sources:
        csv_source = _object(sources["pressure_csv"], "全圧CSVの出典")
        if csv_source.get("kind") not in ("test", "cfd", "synthetic"):
            raise ValueError("全圧CSVの出典区分を確認してください。")
        csv_source = {"kind": csv_source["kind"], "reference": text(csv_source.get("reference"), "全圧CSVの出典", 400, True),
                      "sha256": _digest(csv_source.get("sha256"), "全圧CSV")}
    raw_stations = document.get("stations")
    if not isinstance(raw_stations, list) or not raw_stations:
        raise ValueError("評価断面の一覧がありません。")
    stations = {}
    for entry in raw_stations:
        entry = _object(entry, "評価断面")
        code = text(entry.get("code"), "評価断面コード", 80, True)
        if code in stations or not code.startswith(("CP-", "TB-")):
            raise ValueError("圧縮機・タービンの評価断面コードを確認してください。")
        row = {"code": code, "area_basis": text(entry.get("area_basis"), "面積の定義", 120, True),
               "pressure_basis": text(entry.get("pressure_basis") or "", "全圧の定義", 120)}
        for key, label in (("area_m2", "面積"), ("model_total_pressure_pa", "モデル絶対全圧"),
                           ("observed_total_pressure_pa", "CSV絶対全圧")):
            value = entry.get(key)
            row[key] = _number(value, label, 0) if value is not None else None
            if row[key] == 0:
                raise ValueError(f"{label}は正の値です。")
        if csv_source is None and row["observed_total_pressure_pa"] is not None:
            raise ValueError("CSV絶対全圧には出典が必要です。")
        stations[code] = row
    raw_losses = document.get("losses")
    if not isinstance(raw_losses, list):
        raise ValueError("全圧損失の区間一覧がありません。")
    intervals, used = [], set()
    for entry in raw_losses:
        entry = _object(entry, "全圧損失の区間")
        a, b = entry.get("inlet"), entry.get("outlet")
        if (not isinstance(a, str) or not isinstance(b, str) or a not in stations or b not in stations
                or a == b or a[:2] != b[:2] or (a, b) in used or any("-IMP-" in code or "-ROT-" in code for code in (a, b))):
            raise ValueError("全圧損失に対応する静止区間の評価断面を確認してください。")
        used.add((a, b))
        interval = {"name": text(entry.get("name"), "全圧損失区間名", 120, True), "inlet": a, "outlet": b}
        for origin in ("model", "observed"):
            key = f"{origin}_total_pressure_pa"
            inlet, outlet = stations[a][key], stations[b][key]
            expected = inlet - outlet if inlet is not None and outlet is not None else None
            if entry.get(f"{origin}_loss_pa") != expected:
                raise ValueError("全圧損失が評価断面の入口・出口全圧と一致しません。")
            interval[f"{origin}_loss_pa"] = expected
        intervals.append(interval)
    canonical = json.dumps(document, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return Result(sha256(canonical).hexdigest(), {
        "name": text(document.get("name"), "評価断面照合の名称", 200, True),
        "schema_version": schema, "context": _context(None), "filename": "station-review.json",
        "geometry": geometry, "pressure_csv": csv_source, "stations": list(stations.values()), "losses": intervals,
    }, document)


def suite_comparison_values(archive_bytes):
    try:
        with ZipFile(BytesIO(archive_bytes)) as archive:
            cooled, cooling, thrust = (_decode(archive.read(f"{name}.json"))
                                       for name in ("cycle-cooled", "cooling", "thrust"))
            support = suite_support_document(archive)
        inputs = cooled["input"]
        inlet = next(station for station in cooled["result"]["stations"] if station["id"] == "1")
        thermo = inputs["thermodynamics"]
        conditions = {
            "flow": _number(inlet["mass_flow_kg_s"], "圧縮機入口流量", 0),
            "temperature": _number(inlet["total_temperature_k"], "圧縮機入口全温", 1),
            "pressure": _number(inlet["total_pressure_pa"], "圧縮機入口絶対全圧", 1) / 1000,
            "rpm": _number(inputs["parameters"]["shaft_speed_rpm"], "回転数", 0),
            "mode": text(inputs["mode"], "サイクルの計算モード", 80, True),
            "property_model": text(thermo["model"], "物性モデル", 80, True),
            "fuel": text(thermo["fuel"], "燃料", 80, True),
            "lhv": _number(inputs["parameters"]["fuel_lhv_j_kg"], "燃料低位発熱量", 1) / 1e6,
        }
        if conditions["fuel"] == "custom_ch":
            conditions["fuel_hydrogen"] = _number(thermo["fuel_hydrogen_mass_fraction"], "燃料水素比", 0, 1)

        compressor = cooled["result"]["stage_results"]["compressor"]
        stage1 = next(stage for stage in compressor["stages"] if stage["stage"] == 1)
        cooled_metrics = cooled["result"]["metrics"]
        power = cooled_metrics.get("net_electric_power_w")
        specific_fuel = cooled_metrics.get("specific_fuel_consumption_kg_kwh")
        coolant = sum(_number(injection["coolant_flow_kg_s"], "冷却流量", 0) for injection in cooling["injections"])
        metrics = {
            "stage1_share": 100 * _number(stage1["pressure_log_fraction"], "第1段対数全圧比分配", 0, 1),
            "power": _number(power, "正味出力") / 1000 if power is not None else None,
            "fuel_rate": _number(cooled_metrics["fuel_mass_flow_kg_s"], "燃料流量", 0),
            "specific_fuel": _number(specific_fuel, "燃料消費率", 0) if specific_fuel is not None else None,
            "coolant": _number(coolant, "冷却流量合計", 0),
        }
        reactions = {text(name, "軸受反力のケース名", 200, True):
                     _number(case["thrust"]["bearing_reaction"]["force_n"], "軸受反力")
                     for name, case in thrust["cases"].items()}
    except (BadZipFile, KeyError, StopIteration, TypeError, AttributeError):
        raise ValueError("定格通しケースに比較に必要な詳細データがありません。") from None
    return conditions, metrics, reactions, support


def support_metrics(document):
    result = {}
    thermal = document.get("combustor_thermal", {})
    if "outlet_profile" in thermal:
        result["燃焼器出口パターンファクター", ""] = thermal["outlet_profile"]["pattern_factor"]
    for row in thermal.get("liner_segments", []):
        result[f"ライナ高温側温度（{row['id']}）", "K"] = row["hot_wall_temperature_k"]
    mechanical = document["mechanical"]
    for name, case in mechanical["axial_cases"].items():
        for stage in case["stages"]:
            result[f"{name}・第{stage['stage']}段の軸力", "N"] = stage["force_n"]
        for index, joint in enumerate(case["joints"], 1):
            result[f"{name}・継手{index}の最大使用率", ""] = joint["maximum_utilization"]
    for name, case in mechanical["oil_conditions"].items():
        for bearing, supply in case["bearings"].items():
            result[f"{name}・軸受{bearing}の給油流量余裕", "L/min"] = supply["flow_margin_lpm"]
            result[f"{name}・軸受{bearing}の給油圧余裕", "kPa"] = supply["pressure_margin_kpa"]
    for name, case in mechanical["rotor_cases"].items():
        critical = case["nearest_critical"]
        result[f"{name}・最寄り危険速度", "rpm"] = critical["rpm"] if critical else None
        result[f"{name}・同期応答片振幅", "µm"] = case["operating_amplitude_um"]
    return {(label, unit): _number(value, label) if value is not None else None for (label, unit), value in result.items()}


def compare_suites(reference, candidate):
    left_conditions, left_metrics, left_reactions, left_support = suite_comparison_values(reference)
    right_conditions, right_metrics, right_reactions, right_support = suite_comparison_values(candidate)
    conditions = []
    for key, label, unit in (
        ("flow", "圧縮機入口空気流量", "kg/s"), ("temperature", "圧縮機入口全温", "K"),
        ("pressure", "圧縮機入口絶対全圧", "kPa"), ("rpm", "回転数", "rpm"),
        ("mode", "計算モード", ""), ("property_model", "物性モデル", ""),
        ("fuel", "燃料", ""), ("lhv", "燃料低位発熱量", "MJ/kg"),
        ("fuel_hydrogen", "燃料水素質量割合", ""),
    ):
        if key in left_conditions or key in right_conditions:
            left, right = left_conditions.get(key), right_conditions.get(key)
            conditions.append({"label": label, "unit": unit, "reference": left, "candidate": right,
                               "match": left == right})
    same = all(row["match"] for row in conditions)

    def metric(label, unit, left, right):
        delta = right - left if same and left is not None and right is not None else None
        return {"label": label, "unit": unit, "reference": left, "candidate": right,
                "delta": delta if delta is not None and isfinite(delta) else None}

    metrics = [
        metric(label, unit, left_metrics[key], right_metrics[key])
        for key, label, unit in (
            ("stage1_share", "圧縮機第1段の対数全圧比分配", "%"),
            ("power", "正味出力", "kW"), ("fuel_rate", "燃料流量", "kg/s"),
            ("specific_fuel", "発電基準の燃料消費率", "kg/kWh"), ("coolant", "冷却空気流量", "kg/s"),
        )
    ]
    metrics.extend(metric(f"軸受反力（{name}）", "N", left_reactions.get(name), right_reactions.get(name))
                   for name in dict.fromkeys((*left_reactions, *right_reactions)))
    if left_support is not None and right_support is not None:
        try:
            left, right = support_metrics(left_support), support_metrics(right_support)
        except (KeyError, TypeError, AttributeError):
            raise ValueError("定格レビューの比較項目を読み込めません。") from None
        metrics.extend(metric(label, unit, left.get((label, unit)), right.get((label, unit)))
                       for label, unit in dict.fromkeys((*left, *right)))
    return {"same_operating_point": same, "conditions": conditions, "metrics": metrics}
