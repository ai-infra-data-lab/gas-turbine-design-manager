"""Reference planes for the design ledger, independent of solver geometry."""
import re
from uuid import uuid4


GROUPS = {"engine": "GT", "compressor": "CP", "turbine": "TB", "combustor": "CB"}
CODE = re.compile(r"[A-Z]{2}-[A-Z0-9]+(?:-[A-Z0-9]+)*\Z")
MAX_STAGES = 24
DEFAULT_LAYOUT = {"compressor_stages": 2, "turbine_stages": 3, "axial_guide_vanes": True}

# Each row holds order, ID suffix, label, plane definition, and evaluation quantities.
ENGINE = (
    (10, "0", "大気", "吸気装置上流の静止大気。外部排気系の下流も同じ大気基準。", "大気絶対圧・温度"),
    (20, "2", "圧縮機入口", "フィルター・サイレンサーなどの吸気系を通過した後、最初の圧縮機に入る面。", "全圧・全温・質量流量"),
    (30, "3", "最終圧縮機出口", "最終圧縮機アセンブリの出口。燃焼器へ入る流量は抽気と移送区間を区別する。", "全圧・全温・質量流量・全圧比"),
    (40, "31", "燃焼器入口", "燃焼器に入る面。抽気後に燃焼器へ向かう主流を評価する。", "全圧・全温・質量流量"),
    (50, "4", "燃焼器出口・タービン入口", "燃焼器出口の主流受渡し面。タービン側で供給される冷却空気の混合前。", "全圧・全温・質量流量"),
    (70, "41", "第1静翼出口・第1動翼入口", "等価単段の静翼出口。多段冷却モデルでは実機第1静翼出口は405を参照する。", "全温・全圧・質量流量"),
    (80, "5", "最終タービン出口", "最終動翼列の下流、冷却空気の加算後。タービン出口ダクト入口。", "全圧・全温・質量流量・残留旋回"),
    (90, "6", "タービン出口ダクト出口・排気ディフューザ入口", "機関内のタービン出口ダクトの下流、排気ディフューザ区間の入口。", "全圧・静圧・全温・流れ角・マッハ数"),
    (100, "8", "機関出口・排気ディフューザ出口", "発電用排気の機関出口。外部排気ダクト・煙突から大気までの損失は別区間。", "静圧・全圧・全温・流量・流出速度"),
)
FIRST_VANE = (60, "405", "実第1静翼出口", "多段冷却モデルで用いる実第1静翼の出口温度の位置。", "実第1静翼出口全温")
# Cycle Studio IDs are independent of GasTurb and differ by cooling mode at position 2.
CYCLE_IDS = {"0": ("0", "0"), "2": ("1", "1"), "3": ("2", ""), "31": ("", "2"),
             "4": ("3", "3"), "5": ("4", "4")}
COMPRESSOR_ROW = (
    ("IMP-LE", "インペラー前縁", "Hub・Shroudの翼前縁を基準にした入口断面。", "流路面積・相対流れ角・インシデンス・相対マッハ数"),
    ("IMP-TE", "インペラー後縁", "Hub・Shroudの翼後縁を基準にした出口断面。", "流路面積・全圧・全温・絶対流れ角"),
    ("DIF-IN", "ディフューザ入口", "ディフューザ入口。翼付きでは静翼前縁を基準とする。", "静圧・全圧・絶対流れ角・マッハ数"),
    ("DIF-OUT", "ディフューザ出口", "ディフューザ出口。翼付きでは静翼後縁を基準とする。", "静圧回復・全圧損失・絶対流れ角"),
)
RETURN_ROW = (
    ("RC-LE", "リターンチャネル翼入口", "次段へ向かうリターンチャネル静翼の前縁を基準とする。", "全圧・絶対流れ角・流路面積"),
    ("RC-TE", "リターンチャネル翼出口", "リターンチャネル静翼の後縁を基準とする。次段インペラー入口は別断面。", "全圧損失・残留旋回・流れ角"),
)
AXIAL_OUTLET = (
    ("BEND-OUT", "90度転向流路出口", "最終ディフューザ下流の曲がりが終わり、軸方向直線部が始まる面。", "全圧損失・流れ角・速度分布"),
    ("AGV-LE", "軸方向ガイドベーン前縁", "転向後の直線部内にあるガイドベーンの入口。", "流路面積・絶対流れ角・マッハ数"),
    ("AGV-TE", "軸方向ガイドベーン後縁", "転向後の直線部内にあるガイドベーンの出口。", "全圧損失・残留旋回・出口流れ角"),
)
TURBINE_ROW = (
    ("NGV-LE", "静翼前縁", "ノズル静翼の前縁を基準にした入口断面。", "全圧・全温・絶対流れ角・マッハ数"),
    ("NGV-TE", "静翼後縁", "ノズル静翼の後縁を基準にした出口断面。", "全温・全圧損失・マッハ数・絶対流れ角"),
    ("ROT-LE", "動翼前縁", "動翼の前縁を基準にした入口断面。冷却混合の前後を区別する。", "全温・流量・相対流れ角・インシデンス"),
    ("ROT-TE", "動翼後縁", "動翼の後縁を基準にした出口断面。", "全圧・全温・比仕事・残留旋回"),
)
TURBINE_END = (
    ("OUT", "最終タービン出口", "最終動翼列と混合区間の下流、タービン出口ダクトとの受渡し面。", "全圧・全温・流量・絶対流れ角"),
    ("EXD-IN", "排気ディフューザ入口", "タービン出口ダクト下流のディフューザ入口。入口面積と残留旋回を記録する。", "面積・静圧・全圧・マッハ数・旋回・速度分布"),
    ("EXD-OUT", "排気ディフューザ出口", "排気ディフューザ下流の機関出口。外部の煙道は別区間。", "面積・静圧回復・全圧損失・出口速度・温度分布"),
)
COMBUSTOR = (
    (10, "IN", "燃焼器入口", "燃焼器アセンブリ入口。プレディフューザーを含む構成ではその上流。", "全圧・全温・ケース当たりの空気流量"),
    (20, "PD-OUT", "プレディフューザー出口", "プレディフューザーを設ける場合の出口断面。", "静圧回復・全圧損失・流量偏り"),
    (30, "DOME", "ドーム・スワーラ出口", "ライナ一次燃焼域の入口。燃料・空気の各供給位置は別に記録する。", "一次空気流量・旋回・速度分布"),
    (40, "PRI-OUT", "一次燃焼域出口", "一次燃焼域の下流で、採用した設計モデルの境界位置を記録する。", "温度分布・流量・当量比の評価範囲"),
    (45, "SEC-OUT", "二次燃焼域出口", "二次燃焼域を設ける場合、希釈前の評価断面を定義する。", "温度分布・流量・燃焼域の評価範囲"),
    (50, "DIL-IN", "希釈域入口", "希釈孔列の上流を基準にした主流断面。", "希釈前の温度分布・質量流量"),
    (60, "DIL-OUT", "希釈域出口", "評価に用いる希釈混合区間の下流断面。", "混合後の温度分布・全圧損失"),
    (65, "LINER-OUT", "ライナ出口", "ライナの出口。トランジションダクトを設ける場合はその上流。", "全温分布・流速分布・質量流量"),
    (70, "OUT", "燃焼器出口", "燃焼器からタービンへの受渡し面。ライナ出口と異なる場合は各位置を記録する。", "質量平均全温・半径方向温度分布・出口温度パターンファクター"),
)
INTERFACES = {
    "CP-IN": "GT-2", "CP-OUT": "GT-3", "CB-IN": "GT-31",
    "CB-OUT": "GT-4", "TB-IN": "GT-4", "TB-OUT": "GT-5",
    "TB-EXD-IN": "GT-6", "TB-EXD-OUT": "GT-8",
}
LEGACY_ENGINE = {"GT-00": "GT-0", "GT-01": "GT-2", "GT-02": "GT-3",
                 "GT-03": "GT-4", "GT-04": "GT-5", "GT-05": None}
LEGACY_RETURN = {"CP-RC-LE": "CP-01-RC-LE", "CP-RC-TE": "CP-01-RC-TE"}
LEGACY_BEND_POSITION = "第2段ディフューザー下流の90度曲がりが終わり、軸方向直線部が始まる面。"


def validate_layout(raw):
    if not isinstance(raw, dict):
        raise ValueError("評価断面の構成を指定してください。")
    for key, label in (("compressor_stages", "遠心圧縮機"), ("turbine_stages", "軸流タービン")):
        value = raw.get(key)
        if type(value) is not int or not 1 <= value <= MAX_STAGES:
            raise ValueError(f"{label}の段数は1〜{MAX_STAGES}の整数で指定してください。")
    if type(raw.get("axial_guide_vanes")) is not bool:
        raise ValueError("圧縮機出口の軸方向ガイドベーンの有無を指定してください。")
    return {key: raw[key] for key in DEFAULT_LAYOUT}


def _stage_rows(stage, rows):
    return [(f"{stage:02d}-{suffix}", f"第{stage}段{name}", position, values)
            for suffix, name, position, values in rows]


def _definitions(layout):
    compressor = [("IN", "圧縮機入口", "圧縮機アセンブリの入口。インペラー前縁とは位置を区別する。",
                   "全圧・全温・質量流量・流れ角")]
    for stage in range(1, layout["compressor_stages"] + 1):
        compressor.extend(_stage_rows(stage, COMPRESSOR_ROW))
        if stage < layout["compressor_stages"]:
            compressor.extend(_stage_rows(stage, RETURN_ROW))
    if layout["axial_guide_vanes"]:
        compressor.extend(AXIAL_OUTLET)
    compressor.append(("OUT", "圧縮機出口", "最終流路の出口。抽気位置と流量の集計範囲を記録する。",
                       "全圧・全温・質量流量・絶対流れ角"))

    turbine = [("IN", "タービン入口", "燃焼器側からの受渡し基準面。冷却供給位置と混合前後を区別する。",
                "全圧・全温・流量・温度分布")]
    for stage in range(1, layout["turbine_stages"] + 1):
        turbine.extend(_stage_rows(stage, TURBINE_ROW))
    turbine.extend(TURBINE_END)

    engine = ENGINE
    if layout["turbine_stages"] > 1:
        engine += (FIRST_VANE,)
    return {"engine": [row[1:] for row in sorted(engine)], "compressor": compressor,
            "turbine": turbine, "combustor": [row[1:] for row in COMBUSTOR]}


def configure(components, existing, layout):
    """Build the selected configuration and retain compatible project-specific data."""
    definitions = _definitions(layout)
    active_keys = {f"{GROUPS[group]}-{row[0]}" for group, specs in definitions.items() for row in specs}
    legacy = not any(item.get("template_key") for item in existing) and any(
        item["code"] in LEGACY_ENGINE and item.get("cycle_station_id") == item["code"][-1] for item in existing)
    legacy_parts = set()
    if legacy:
        for group, specs in _definitions({**DEFAULT_LAYOUT, "turbine_stages": 1}).items():
            if group != "engine":
                legacy_parts.update(f"{GROUPS[group]}-{row[0]}" for row in specs)

    previous, manual = {}, []
    for item in existing:
        code = item["code"]
        key = item.get("template_key", "") or code
        if legacy and key in LEGACY_ENGINE and item.get("cycle_station_id") == key[-1]:
            key = LEGACY_ENGINE[key]
            if key is None:
                continue
        if legacy:
            key = LEGACY_RETURN.get(key, key)
        if item.get("template_key") or key in active_keys or key in legacy_parts:
            if key in previous:
                raise ValueError("ひな形と対応する断面が重複しています。既存の断面IDを確認してください。")
            previous[key] = item
        else:
            manual.append(item)

    interfaces = {**INTERFACES, "TB-01-NGV-TE": "GT-405" if layout["turbine_stages"] > 1 else "GT-41"}
    stations, engine_ids = [], {}
    for group, specs in definitions.items():
        matching = [part["id"] for part in components if part["kind"] == group]
        component = matching[0] if len(matching) == 1 else ""
        for sequence, (suffix, name, position, quantities) in enumerate(specs, 1):
            key = f"{GROUPS[group]}-{suffix}"
            old = previous.get(key, {})
            station_id = old.get("id") or str(uuid4())
            if group == "engine":
                engine_ids[key] = station_id
                if suffix == "41":
                    if layout["turbine_stages"] == 1:
                        position = "単段タービンの静翼出口・動翼入口のサイクル上の受渡し位置。"
                    else:
                        name = "等価単段モデルの静翼出口"
            cycle_normal, cycle_cooled = CYCLE_IDS.get(suffix, ("", "")) if group == "engine" else ("", "")
            reference = engine_ids[interfaces[key]] if key in interfaces else old.get("engine_station_id", "")
            row = {"id": station_id, "group": group, "code": key, "template_key": key,
                   "name": name, "sequence": sequence * 10, "position": position, "quantities": quantities,
                   "basis": "", "component_id": old.get("component_id", component), "engine_station_id": reference,
                   "cycle_station_id": cycle_normal, "cooled_cycle_station_id": cycle_cooled}
            if group != "engine" and old:
                row.update({field: old[field] for field in ("name", "position", "quantities", "basis")})
                if key == "CP-BEND-OUT" and legacy and row["position"] == LEGACY_BEND_POSITION:
                    row["position"] = position
            stations.append(row)

    stations.extend(manual)
    valid_engine_ids = {item["id"] for item in stations if item["group"] == "engine"}
    return [{**item, "engine_station_id": item["engine_station_id"] if item["engine_station_id"] in valid_engine_ids else ""}
            for item in stations]
