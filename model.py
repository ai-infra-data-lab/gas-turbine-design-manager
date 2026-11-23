"""Versioned design information. No engineering calculations are performed."""
from __future__ import annotations

import copy
import json
import uuid
from datetime import datetime, timezone

from stations import CODE, GROUPS, validate_layout

SCHEMA = 1
STATUSES = ("draft", "designing", "reviewing", "approved", "archived")
KINDS = ("inlet", "compressor", "combustor", "turbine", "exhaust", "shaft", "bearing", "casing", "heat_exchanger", "auxiliary", "other")


def uid():
    return str(uuid.uuid4())


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def text(value, label, limit=2000, required=False):
    if not isinstance(value, str) or len(value) > limit:
        raise ValueError(f"{label}: 文字列（{limit}文字以内）を指定してください。")
    value = value.strip()
    if required and not value:
        raise ValueError(f"{label}を入力してください。")
    return value


def identifier(value):
    try:
        return str(uuid.UUID(value))
    except (ValueError, TypeError, AttributeError):
        raise ValueError("IDが不正です。") from None


def rows(value, label, limit=500):
    if not isinstance(value, list) or len(value) > limit or any(not isinstance(x, dict) for x in value):
        raise ValueError(f"{label}: 最大{limit}件の一覧を指定してください。")
    return value


def unique(items):
    ids = [x["id"] for x in items]
    if len(set(ids)) != len(ids):
        raise ValueError("同じIDが重複しています。")
    return items


def parameters(items):
    return unique([{
        "id": identifier(x.get("id")),
        "name": text(x.get("name"), "項目名", 150, True),
        "value": text(x.get("value", ""), "値", 1000),
        "unit": text(x.get("unit", ""), "単位", 60),
        "source": text(x.get("source", ""), "出典・根拠", 2000),
        "note": text(x.get("note", ""), "備考", 4000),
    } for x in rows(items, "設計項目")])


def evaluation_stations(items, components):
    component_kinds = {part["id"]: part["kind"] for part in components}
    result = []
    for item in rows(items, "評価断面", 300):
        group = item.get("group")
        if not isinstance(group, str) or group not in GROUPS:
            raise ValueError("評価断面の区分が不正です。")
        code = text(item.get("code"), "断面ID", 40, True).upper()
        if not CODE.fullmatch(code) or not code.startswith(GROUPS[group] + "-"):
            raise ValueError(f"断面IDは {GROUPS[group]}- から始め、英数字とハイフンを使ってください。")
        sequence = item.get("sequence")
        if type(sequence) is not int or not 0 <= sequence <= 10000:
            raise ValueError("流れ順は0〜10000の整数で指定してください。")
        component_id = text(item.get("component_id", ""), "対象部品", 36)
        if component_id and (group == "engine" or component_kinds.get(component_id) != group):
            raise ValueError("評価断面の区分に合う対象部品を選んでください。")
        engine_id = text(item.get("engine_station_id", ""), "全機側の対応断面", 36)
        cycle_id = text(item.get("cycle_station_id", ""), "Cycle Studio位置ID", 1)
        cooled_cycle_id = text(item.get("cooled_cycle_station_id", cycle_id), "冷却時の位置ID", 1)
        if any(value and (group != "engine" or value not in "012345") for value in (cycle_id, cooled_cycle_id)):
            raise ValueError("Cycle Studio位置IDはガスタービン全体の断面に0〜5を指定できます。")
        template_key = text(item.get("template_key", ""), "ひな形ID", 40)
        if template_key and (not CODE.fullmatch(template_key) or not template_key.startswith(GROUPS[group] + "-")):
            raise ValueError("評価断面のひな形IDが不正です。")
        result.append({
            "id": identifier(item.get("id")), "group": group, "code": code,
            "name": text(item.get("name"), "断面名", 150, True), "sequence": sequence,
            "position": text(item.get("position", ""), "位置の定義", 1000),
            "quantities": text(item.get("quantities", ""), "評価量", 500),
            "basis": text(item.get("basis", ""), "集計・座標の条件", 1000),
            "component_id": component_id, "engine_station_id": engine_id, "cycle_station_id": cycle_id,
            "cooled_cycle_station_id": cooled_cycle_id, "template_key": template_key,
        })
    unique(result)
    for field in ("code", "template_key", "cycle_station_id", "cooled_cycle_station_id"):
        assigned = [item[field] for item in result if item[field]]
        if len(set(assigned)) != len(assigned):
            raise ValueError("断面ID、ひな形ID、または同じ冷却条件のCycle Studio位置IDが重複しています。")
    engine_ids = {item["id"] for item in result if item["group"] == "engine"}
    for item in result:
        if item["engine_station_id"] and (item["group"] == "engine" or item["engine_station_id"] not in engine_ids):
            raise ValueError("全機側の対応断面が見つかりません。断面の対応を見直してください。")
    return result


def validate_project(raw):
    if not isinstance(raw, dict) or type(raw.get("schema_version")) is not int or raw.get("schema_version") != SCHEMA:
        raise ValueError("対応していない設計データ形式です。")
    result = {"schema_version": SCHEMA, "id": identifier(raw.get("id"))}
    for key, label, limit in (("name", "案件名", 150), ("code", "案件番号", 80),
                               ("application", "用途", 100), ("architecture", "構成", 150),
                               ("owner", "担当", 100), ("purpose", "設計の目的", 10000)):
        result[key] = text(raw.get(key, ""), label, limit, key == "name")
    result["status"] = raw.get("status", "draft")
    if result["status"] not in STATUSES:
        raise ValueError("案件の状態が不正です。")
    result["requirements"] = parameters(raw.get("requirements", []))
    components = []
    for item in rows(raw.get("components", []), "部品", 200):
        kind = item.get("kind", "other")
        status = item.get("status", "draft")
        if kind not in KINDS or status not in STATUSES:
            raise ValueError("部品の種別または状態が不正です。")
        components.append({"id": identifier(item.get("id")), "kind": kind, "status": status,
                           "name": text(item.get("name"), "部品名", 150, True),
                           "stage": text(item.get("stage", ""), "段・位置", 100),
                           "owner": text(item.get("owner", ""), "担当", 100),
                           "description": text(item.get("description", ""), "部品メモ", 10000),
                           "parameters": parameters(item.get("parameters", []))})
    result["components"] = unique(components)
    if "stations" in raw:
        result["stations"] = evaluation_stations(raw["stations"], components)
    if "station_layout" in raw:
        result["station_layout"] = validate_layout(raw["station_layout"])
    if "mbse" in raw:
        from mbse import validate as validate_mbse

        result["mbse"] = validate_mbse(raw["mbse"], result)
    component_ids = {x["id"] for x in components}

    def component_ref(item):
        value = item.get("component_id", "")
        if value and value not in component_ids:
            raise ValueError("関連部品が見つかりません。")
        return value

    result["notes"] = unique([{"id": identifier(x.get("id")),
                               "title": text(x.get("title"), "メモの件名", 150, True),
                               "body": text(x.get("body", ""), "メモ", 20000),
                               "created_at": text(x.get("created_at", ""), "記録日時", 80)}
                              for x in rows(raw.get("notes", []), "メモ", 300)])
    result["links"] = unique([{"id": identifier(x.get("id")),
                               "title": text(x.get("title"), "資料名", 150, True),
                               "url": text(x.get("url"), "URL", 2000, True),
                               "component_id": component_ref(x),
                               "note": text(x.get("note", ""), "備考", 4000)}
                              for x in rows(raw.get("links", []), "リンク", 300)])
    for link in result["links"]:
        from urllib.parse import urlsplit
        parsed = urlsplit(link["url"])
        if parsed.scheme not in ("https", "http") or not parsed.netloc or parsed.username or parsed.password:
            raise ValueError("資料リンクには認証情報を含まないhttp/httpsのURLを指定してください。")
    result["attachments"] = []
    for x in rows(raw.get("attachments", []), "添付資料", 300):
        size = x.get("size")
        if type(size) is not int or not 0 <= size <= 5 * 1024 * 1024:
            raise ValueError("添付ファイルは1件5MB以内です。")
        result["attachments"].append({"id": identifier(x.get("id")),
                                      "name": text(x.get("name"), "ファイル名", 255, True),
                                      "size": size, "component_id": component_ref(x),
                                      "added_at": text(x.get("added_at", ""), "追加日時", 80)})
    unique(result["attachments"])
    if sum(x["size"] for x in result["attachments"]) > 30 * 1024 * 1024:
        raise ValueError("添付ファイルの合計は1案件30MB以内です。")
    # Reserved, inert data for future calculation module adapters.
    extensions = raw.get("extensions", {})
    if not isinstance(extensions, dict) or len(json.dumps(extensions, allow_nan=False)) > 100000:
        raise ValueError("拡張情報が不正、または大きすぎます。")
    result["extensions"] = copy.deepcopy(extensions)
    if len(json.dumps(result, ensure_ascii=False)) > 2_000_000:
        raise ValueError("設計情報は1案件2MB以内にしてください（添付ファイルを除く）。")
    return result


def new_project(name, code="", template="blank"):
    if template == "mbse_example":
        from mbse_example import populate

        project = new_project(name, code, "single_shaft")
        populate(project)
        return validate_project(project)
    project = {"schema_version": SCHEMA, "id": uid(), "name": name, "code": code,
               "application": "", "architecture": "", "owner": "", "purpose": "",
               "status": "draft", "requirements": [], "components": [], "notes": [],
               "links": [], "attachments": [], "extensions": {}}
    if template == "single_shaft":
        project["architecture"] = "一軸・単純サイクル"
        for name, kind in (("吸気系", "inlet"), ("圧縮機", "compressor"), ("燃焼器", "combustor"),
                           ("タービン", "turbine"), ("排気系", "exhaust"), ("ロータ・軸", "shaft"),
                           ("軸受", "bearing"), ("ケーシング", "casing")):
            project["components"].append({"id": uid(), "name": name, "kind": kind, "status": "draft",
                                           "stage": "", "owner": "", "description": "", "parameters": []})
        project["requirements"] = [{"id": uid(), "name": name, "value": "", "unit": unit,
                                    "source": "", "note": ""}
                                   for name, unit in (("目標軸出力", "kW"), ("設計回転数", "rpm"),
                                                       ("空気流量", "kg/s"), ("全圧比", "—"),
                                                       ("タービン入口全温", "K"))]
    elif template != "blank":
        raise ValueError("テンプレートが不正です。")
    return validate_project(project)
