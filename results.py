"""Validate and identify immutable results produced by the independent apps."""
from dataclasses import dataclass
from hashlib import sha256
import json
import re
from sys import float_info

from model import identifier, text


MAX_ARTIFACT_BYTES = 5 * 1024 * 1024
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
    raise ValueError("サイクル結果、翼形状と手渡し記録、または性能の比較一式を指定してください。")
