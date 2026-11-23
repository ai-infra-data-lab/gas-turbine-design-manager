"""Fictional operating scenarios using the existing design applications."""
from model import uid
from stations import DEFAULT_LAYOUT, configure


def populate(project):
    def entry(name, **fields):
        return {"id": uid(), "name": name, **fields}

    project.update(application="要求と検証の操作演習", purpose="定格発電と冷間始動の仮想例。要求値・設計値は操作説明用。")
    parts = {part["kind"]: part for part in project["components"]}
    for kind, name in (("secondary_air", "二次空気回路"), ("oil", "給油装置")):
        part = entry(name, kind="auxiliary")
        project["components"].append(part)
        parts[kind] = part
    project["station_layout"] = DEFAULT_LAYOUT.copy()
    project["stations"] = configure(project["components"], [], project["station_layout"])
    station_id = {row["code"]: row["id"] for row in project["stations"]}

    def parameter(name, value, unit):
        return entry(name, value=value, unit=unit, source="操作演習用の仮想条件")

    share = parameter("第1段の対数全圧比分配", "75", "%")
    oil_temperature = parameter("冷間の油温", "10", "°C")
    parts["compressor"]["parameters"] = [share]
    parts["oil"]["parameters"] = [oil_temperature]
    rated = entry("定格発電", purpose="所定の運転点で必要な電力を供給する",
                  conditions="仮想定格の入口条件と39,000 rpm。第1段対数全圧比分配は設計値を参照する。")
    cold = entry("冷間始動", purpose="始動時の軸受に必要な給油を確保する",
                 conditions="仮想の冷間始動。油温とポンプ・各軸受の最低給油条件を照合する。")

    def component_ids(*keys):
        return [parts[key]["id"] for key in keys]

    def function(name, mode, *components):
        return entry(name, mode_ids=[mode["id"]], component_ids=component_ids(*components))

    def interface(name, mode, origin, receiver, quantities, from_code="", to_code=""):
        return entry(name, mode_ids=[mode["id"]], quantities=quantities,
                     from_component_id=parts[origin]["id"], to_component_id=parts[receiver]["id"],
                     from_station_id=station_id.get(from_code, ""), to_station_id=station_id.get(to_code, ""))

    generation = function("発電用の軸動力を取り出す", rated, "compressor", "combustor", "turbine", "shaft")
    cooling = function("タービン翼を冷却する", rated, "compressor", "secondary_air", "turbine")
    supply = function("軸受に給油する", cold, "oil", "bearing")
    extraction = interface("圧縮機からの抽気", rated, "compressor", "secondary_air", "絶対全圧 [Pa]、全温 [K]、質量流量 [kg/s]", "CP-OUT")
    injection = interface("翼冷却の注入", rated, "secondary_air", "turbine", "背圧は絶対静圧 [Pa]、冷却空気の全温 [K]、質量流量 [kg/s]", to_code="TB-01-NGV-TE")
    lubrication = interface("軸受への給油", cold, "oil", "bearing", "体積流量 [L/min]、入口ゲージ圧 [kPa]、油温 [°C]")
    power = parameter("定格正味電力", "73", "kW")
    cool_supply = parameter("各タービン翼列への冷却空気を確保", "", "")
    oil_a = parameter("冷間・軸受Aの給油流量余裕", "0", "L/min")
    oil_b = parameter("冷間・軸受Bの給油流量余裕", "0", "L/min")
    project["requirements"] = [power, cool_supply, oil_a, oil_b]

    def trace(requirement, code, mode, function, interactions, parameters, parents=()):
        return {"id": requirement["id"], "code": code, "mode_ids": [mode["id"]], "function_ids": [function["id"]],
                "component_ids": function["component_ids"], "interface_ids": [item["id"] for item in interactions],
                "parameter_ids": [item["id"] for item in parameters], "parent_ids": [item["id"] for item in parents]}

    def verify(name, requirement, mode, metric):
        return entry(name, requirement_id=requirement["id"], mode_id=mode["id"], method="analysis",
                     metric=metric, comparison="ge", procedure="仮想定格通しケースの運転条件と設計入力を照合し、要求値以上か判定する。")

    project["mbse"] = {
        "modes": [rated, cold], "functions": [generation, cooling, supply], "interfaces": [extraction, injection, lubrication],
        "traces": [trace(power, "REQ-GT-001", rated, generation, [], [share]),
                   trace(cool_supply, "REQ-TB-001", rated, cooling, [extraction, injection], [], [power]),
                   trace(oil_a, "REQ-OIL-001", cold, supply, [lubrication], [oil_temperature]),
                   trace(oil_b, "REQ-OIL-002", cold, supply, [lubrication], [oil_temperature])],
        "cases": [verify("定格通しケースの正味出力", power, rated, "net_power"),
                  verify("冷間・軸受Aの流量余裕", oil_a, cold, "cold_oil_a"),
                  verify("冷間・軸受Bの流量余裕", oil_b, cold, "cold_oil_b")],
    }
