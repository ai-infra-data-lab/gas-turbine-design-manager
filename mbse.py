"""Versioned requirements, operating modes, interfaces, and verification definitions."""
from hashlib import sha256
import json
import math
import operator
import re

from model import identifier, rows, text, unique


METRICS = {
    "net_power": ("正味電力", "kW", "cycle-cooled.json", ("result", "metrics", "net_electric_power_w"), .001),
    "cold_oil_a": ("冷間・軸受Aの給油流量余裕", "L/min", "oil.json", ("conditions", "冷間始動", "bearings", "A", "flow_margin_lpm"), 1.),
    "cold_oil_b": ("冷間・軸受Bの給油流量余裕", "L/min", "oil.json", ("conditions", "冷間始動", "bearings", "B", "flow_margin_lpm"), 1.),
}
METHODS = {"analysis": "解析", "test": "試験", "inspection": "検査", "demonstration": "実演"}
OPERATORS = {"ge": ("以上", operator.ge), "le": ("以下", operator.le)}
VERDICTS = ("pass", "fail", "inconclusive")
GROUPS = ("modes", "functions", "interfaces", "traces", "cases")


def links(raw, valid, label):
    if not isinstance(raw, list) or len(raw) > 100:
        raise ValueError(f"{label}は100件まで指定できます。")
    refs = [identifier(value) for value in raw]
    if len(refs) != len(set(refs)) or not set(refs).issubset(valid):
        raise ValueError(f"{label}に重複、または存在しない参照があります。")
    return sorted(refs)


def ref(item, field, valid, label, optional=False):
    value = item.get(field, "")
    if optional and value == "":
        return ""
    value = identifier(value)
    if value not in valid:
        raise ValueError(f"{label}が見つかりません。関連付けを確認してください。")
    return value


def numeric(value):
    try:
        number = float(value)
    except (ValueError, TypeError, OverflowError):
        raise ValueError("要求値は有限の数値で指定してください。") from None
    if not math.isfinite(number):
        raise ValueError("要求値は有限の数値で指定してください。")
    return number


def named(item, label):
    return {"id": identifier(item.get("id")), "name": text(item.get("name"), label, 150, True)}


def validate(raw, project):
    if not isinstance(raw, dict) or set(raw) - set(GROUPS):
        raise ValueError("システムモデルの形式が不正です。")
    source = {key: rows(raw.get(key, []), key, 300) for key in GROUPS}
    requirements = {row["id"]: row for row in project["requirements"]}
    components = {row["id"] for row in project["components"]}
    parameters = {row["id"] for row in unique([row for part in project["components"] for row in part["parameters"]])}
    stations = {row["id"]: row["component_id"] for row in project.get("stations", [])}
    modes = unique([{**named(row, "運転モード名"),
                     "purpose": text(row.get("purpose", ""), "運用目的", 1000, True),
                     "conditions": text(row.get("conditions", ""), "運転条件", 2000, True)}
                    for row in source["modes"]])
    mode_ids = {row["id"] for row in modes}
    functions = unique([{**named(row, "機能名"),
                         "description": text(row.get("description", ""), "機能の説明", 2000),
                         "mode_ids": links(row.get("mode_ids", []), mode_ids, "運転モード"),
                         "component_ids": links(row.get("component_ids", []), components, "機能の割当部品")}
                        for row in source["functions"]])
    interfaces = []
    for row in source["interfaces"]:
        connection = {**named(row, "接続名"), "quantities": text(row.get("quantities", ""), "受け渡す量と単位", 1000, True),
                      "mode_ids": links(row.get("mode_ids", []), mode_ids, "運転モード")}
        for end in ("from", "to"):
            component = ref(row, f"{end}_component_id", components, "接続部品")
            station = ref(row, f"{end}_station_id", stations, "接続断面", optional=True)
            if station and stations[station] and stations[station] != component:
                raise ValueError("接続断面は対応する送出・受入部品の断面を指定してください。")
            connection.update({f"{end}_component_id": component, f"{end}_station_id": station})
        if connection["from_component_id"] == connection["to_component_id"]:
            raise ValueError("送出側と受入側に異なる部品を指定してください。")
        interfaces.append(connection)
    unique(interfaces)
    function_ids = {item["id"] for item in functions}
    interface_ids = {item["id"] for item in interfaces}
    traces = unique([{
        "id": ref(row, "id", requirements, "要求"),
        "code": text(row.get("code", ""), "要求番号", 40, True).upper(),
        "parent_ids": links(row.get("parent_ids", []), requirements.keys(), "親要求"),
        "mode_ids": links(row.get("mode_ids", []), mode_ids, "適用する運転モード"),
        "component_ids": links(row.get("component_ids", []), components, "担当部品"),
        "parameter_ids": links(row.get("parameter_ids", []), parameters, "関連する設計値"),
        "function_ids": links(row.get("function_ids", []), function_ids, "関連機能"),
        "interface_ids": links(row.get("interface_ids", []), interface_ids, "関連接続"),
        "approval": text(row.get("approval", ""), "要求の承認記録", 200),
        "rationale": text(row.get("rationale", ""), "割当・派生の根拠", 2000),
    } for row in source["traces"]])
    codes = [row["code"] for row in traces]
    if len(set(codes)) != len(codes) or any(not re.fullmatch(r"[A-Z0-9][A-Z0-9_-]*", code) for code in codes):
        raise ValueError("要求番号には英数字・ハイフン・下線を使い、重複しない番号を指定してください。")
    for trace in traces:
        if trace["approval"] and (not requirements[trace["id"]]["source"] or not trace["mode_ids"]):
            raise ValueError("承認を記録するには要求の出典と適用する運転モードを指定してください。")
    parents = {row["id"]: row["parent_ids"] for row in traces}
    visited = set()

    def visit(requirement_id, path):
        if requirement_id in path:
            raise ValueError("親要求をたどる関係が循環しています。")
        if requirement_id not in visited:
            for parent in parents.get(requirement_id, []):
                visit(parent, path | {requirement_id})
            visited.add(requirement_id)

    for requirement_id in parents:
        visit(requirement_id, set())

    applicability = {row["id"]: row["mode_ids"] for row in traces}
    cases = []
    for row in source["cases"]:
        requirement = ref(row, "requirement_id", parents, "関連付けを登録した要求")
        mode = ref(row, "mode_id", mode_ids, "検証の運転モード")
        if mode not in applicability[requirement]:
            raise ValueError("検証の運転モードを要求の適用モードに追加してください。")
        method = text(row.get("method", ""), "検証方法", 20)
        metric = text(row.get("metric", ""), "判定に使う結果", 40)
        comparison = text(row.get("comparison", ""), "比較条件", 10)
        if method not in METHODS or (metric and metric not in METRICS) or (comparison and comparison not in OPERATORS):
            raise ValueError("検証方法・結果項目・比較条件を確認してください。")
        if metric and (method != "analysis" or not comparison or requirements[requirement]["unit"] != METRICS[metric][1]):
            raise ValueError("自動判定は解析を選び、比較条件と結果項目に対応する要求の単位を指定してください。")
        if comparison:
            numeric(requirements[requirement]["value"])
            text(requirements[requirement]["unit"], "要求値の単位", 60, True)
        cases.append({**named(row, "検証ケース名"), "requirement_id": requirement, "mode_id": mode,
                      "method": method, "metric": metric, "comparison": comparison,
                      "procedure": text(row.get("procedure", ""), "検証手順・合否基準", 2000, True)})
    return {"modes": modes, "functions": functions, "interfaces": interfaces, "traces": traces, "cases": unique(cases)}


def _snapshot(project, case):
    model = project["mbse"]
    traces = {row["id"]: row for row in model["traces"]}
    target = case["requirement_id"]
    downstream = {target}
    pending = [target]
    children = {}
    for trace in traces.values():
        for parent in trace["parent_ids"]:
            children.setdefault(parent, []).append(trace["id"])
    while pending:
        for child in children.get(pending.pop(), []):
            if child not in downstream:
                downstream.add(child)
                pending.append(child)
    selected = {key: traces[key] for key in downstream}
    upstream = {parent for row in selected.values() for parent in row["parent_ids"]} - downstream
    pending = list(upstream)
    while pending:
        for parent in traces.get(pending.pop(), {}).get("parent_ids", []):
            if parent not in upstream and parent not in downstream:
                upstream.add(parent)
                pending.append(parent)

    def refs(field):
        return {item for row in selected.values() for item in row[field]}

    parameters = {value["id"]: (part["id"], value) for part in project["components"] for value in part["parameters"]}
    parameter_ids = refs("parameter_ids")
    function_ids, interface_ids = refs("function_ids"), refs("interface_ids")
    functions = [row for row in model["functions"] if row["id"] in function_ids]
    interfaces = [row for row in model["interfaces"] if row["id"] in interface_ids]
    parts = refs("component_ids") | {parameters[key][0] for key in parameter_ids}
    parts.update(component for row in functions for component in row["component_ids"])
    parts.update(row[field] for row in interfaces for field in ("from_component_id", "to_component_id"))
    station_ids = {row[field] for row in interfaces for field in ("from_station_id", "to_station_id") if row[field]}
    modes = refs("mode_ids") | {case["mode_id"]}
    modes.update(mode for row in functions + interfaces for mode in row["mode_ids"])

    def ordered(records):
        return sorted(records, key=lambda row: row["id"])

    document = {
        "case": case, "traces": ordered({key: value for key, value in row.items() if key != "approval"}
                                       for row in selected.values()), "functions": ordered(functions),
        "interfaces": ordered(interfaces), "modes": ordered(row for row in model["modes"] if row["id"] in modes),
        "requirements": ordered(row for row in project["requirements"] if row["id"] in downstream | upstream),
        "ancestors": {key: traces[key]["parent_ids"] for key in upstream if key in traces},
        "components": ordered({key: part[key] for key in ("id", "name", "kind", "stage", "description")}
                              for part in project["components"] if part["id"] in parts),
        "parameters": {key: parameters[key] for key in sorted(parameter_ids)},
        "stations": ordered(row for row in project.get("stations", []) if row["id"] in station_ids),
    }
    return document


def basis(project, case):
    """Identify model inputs that a verification verdict depends on."""
    document = _snapshot(project, case)
    digest = sha256(json.dumps(document, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
    return digest, [part["id"] for part in document["components"]], list(document["parameters"])


def same_source_design(previous, current, case):
    """Allow re-evaluation of a result when the linked design and conditions still match."""
    document = _snapshot(current, case)
    previous_model = previous.get("mbse", {})
    specifications = (
        ("components", previous["components"], ("kind", "stage", "description")),
        ("modes", previous_model.get("modes", []), ("conditions",)),
        ("functions", previous_model.get("functions", []), ("description", "mode_ids", "component_ids")),
        ("interfaces", previous_model.get("interfaces", []),
         ("from_component_id", "to_component_id", "from_station_id", "to_station_id", "mode_ids", "quantities")),
        ("stations", previous.get("stations", []), ("code", "position", "quantities", "basis", "component_id")),
    )
    for group, rows, fields in specifications:
        by_id = {row["id"]: row for row in rows}
        for item in document[group]:
            original = by_id.get(item["id"], {})
            if any(item[field] != original.get(field) for field in fields):
                return False
    original_parameters = {row["id"]: (part["id"], row["value"], row["unit"])
                           for part in previous["components"] for row in part["parameters"]}
    return all(original_parameters.get(key) == (owner, value["value"], value["unit"])
               for key, (owner, value) in document["parameters"].items())


def suite_observation(document, metric):
    _, unit, _, path, factor = METRICS[metric]
    value = document
    for segment in path:
        value = value.get(segment) if isinstance(value, dict) else None
    if type(value) not in (float, int):
        raise ValueError("成果物にこの判定で使う数値がありません。詳細を確認してください。")
    return numeric(value) * factor, unit


def clear_old_approvals(previous, current):
    before, after = previous.get("mbse", {}), current.get("mbse", {})
    old_traces = {item["id"]: item for item in before.get("traces", [])}
    old_requirements = {item["id"]: item for item in previous["requirements"]}
    new_requirements = {item["id"]: item for item in current["requirements"]}
    old_modes = {item["id"]: item for item in before.get("modes", [])}
    new_modes = {item["id"]: item for item in after.get("modes", [])}
    for trace in after.get("traces", []):
        prior = old_traces.get(trace["id"])
        if not trace["approval"] or prior is None or trace["approval"] != prior["approval"]:
            continue
        if (trace != prior or old_requirements[trace["id"]] != new_requirements[trace["id"]]
                or any(old_modes.get(key) != new_modes[key] for key in trace["mode_ids"])):
            trace["approval"] = ""
