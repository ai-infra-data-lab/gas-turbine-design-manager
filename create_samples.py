"""Create fictional training projects in a separate, non-overwriting sample DB."""
import argparse
import csv
import io
import json
from pathlib import Path

from model import now, uid
from store import Store

ROOT = Path(__file__).resolve().parent
NOTICE = "操作説明用の架空値。計算結果・試験値・設計成立性の確認値ではありません。"


def parameter(name, value, unit, source="演習用要求仕様書 1版", note=""):
    return {"id": uid(), "name": name, "value": value, "unit": unit, "source": source, "note": note}


def create_samples(directory):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    db_path = directory / "sample-designs.sqlite3"
    # The demo is editable. Re-running the launcher must preserve those edits.
    if db_path.exists():
        return {"created": False, "database": str(db_path)}
    store = Store(db_path)
    env = store.create("【サンプル】100 kW級GT・基準案 A", "DEMO-GT-A", "single_shaft")
    p = env["project"]
    p.update(application="小型発電用（操作演習）", architecture="一軸・遠心圧縮機＋ラジアルタービン",
             owner="演習用 設計チーム", purpose=NOTICE + "\n仕様・部品・資料を関連付け、別案比較と履歴復元の操作を学ぶ。",
             status="designing")
    p["requirements"] = [
        parameter("目標軸出力", "100", "kW", note="要求値。実際に100 kW出力できると確認した値ではない。"),
        parameter("設計回転数", "60000", "rpm", note="共通軸の要求回転数。比較案Bは55000 rpm。"),
        parameter("空気流量", "1.20", "kg/s", note="圧縮機入口の全流量。修正流量ではない。"),
        parameter("圧縮機全圧比", "3.8", "—", note="圧縮機出口全圧 / 圧縮機入口全圧の目標値。"),
        parameter("タービン入口全温", "1123", "K", note="全温。静温と区別して登録。"),
        parameter("大気温度", "288.15", "K", note="演習で固定した入口環境。"),
        parameter("大気圧力", "101.325", "kPa（絶対圧）", note="ゲージ圧ではない。"),
        parameter("熱効率の目標", "", "%", source="未決定：サイクル計算モジュール追加後に検討", note="空欄は未知・未定を意味する。0%に置換しない。"),
    ]
    compressor = next(c for c in p["components"] if c["kind"] == "compressor")
    turbine = next(c for c in p["components"] if c["kind"] == "turbine")
    shaft = next(c for c in p["components"] if c["kind"] == "shaft")
    bearing = next(c for c in p["components"] if c["kind"] == "bearing")
    combustor = next(c for c in p["components"] if c["kind"] == "combustor")
    compressor.update(name="遠心圧縮機", stage="第1段 / 入口側", status="reviewing", owner="空力担当（演習）",
                      description="寸法は操作説明用の仮定。将来、Blade Designerへ渡す情報を整理する例。\n圧縮比・効率・寸法の成立性は未検証。")
    compressor["parameters"] = [
        parameter("インペラ出口外径", "110", "mm", "演習用寸法案 A-01", "図面上の外径。ケーシング外径ではない。"),
        parameter("入口チップ径", "48", "mm", "演習用寸法案 A-01"),
        parameter("出口翼高さ", "6.5", "mm", "演習用寸法案 A-01"),
        parameter("主翼枚数", "6", "枚", "演習用寸法案 A-01"),
        parameter("スプリッター枚数", "6", "枚", "演習用寸法案 A-01", "主翼とは別項目。合計枚数の自動計算は行わない。"),
        parameter("チップ隙間", "0.30", "mm", "演習用寸法案 A-01", "冷態での片側隙間の仮定。"),
        parameter("断熱効率の目標", "0.78", "—", "演習用仮定", "目標値。CFDや試験で確認した結果ではない。"),
    ]
    turbine.update(name="ラジアルタービン", stage="第1段 / 燃焼器下流", owner="タービン担当（演習）",
                   description="圧縮機と共通軸の構成を仮定。熱・強度・寿命の計算は未実施。")
    turbine["parameters"] = [parameter("ロータ入口外径", "95", "mm", "演習用寸法案 T-01"),
                              parameter("ロータ翼枚数", "13", "枚", "演習用寸法案 T-01"),
                              parameter("材料", "", "", "未選定", "材料特性・許容温度の値はまだ登録しない。")]
    shaft.update(stage="共通軸", description="圧縮機とタービンを接続する軸。将来のロータ解析用情報を記録。")
    shaft["parameters"] = [parameter("軸の代表直径", "20", "mm", "演習用軸配置 S-01"),
                            parameter("軸受間距離", "220", "mm", "演習用軸配置 S-01")]
    bearing["parameters"] = [parameter("支持位置の数", "2", "箇所", "演習用軸配置 S-01"),
                              parameter("軸受剛性", "", "N/m", "未評価", "ロータ解析前に必要。値の推定・計算は本アプリでは行わない。")]
    combustor["parameters"] = [parameter("圧力損失率の仮定", "4.0", "%", "演習用仮定", "入口全圧に対する全圧低下率。計算はしない。")]
    p["notes"] = [{"id": uid(), "title": "この案件の読み方", "created_at": now(),
                   "body": NOTICE + "\n値の登録・編集・比較が目的です。未知値は空欄にしています。\n参照資料名と章番号を出典欄に残す練習ができます。"},
                  {"id": uid(), "title": "基準案Aと低回転案Bの検討", "created_at": now(),
                   "body": "A：60000 rpm、インペラ出口外径110 mm。\nB：55000 rpm、同外径118 mm、効率目標0.80。\n低回転化と外径変更を一緒に管理する例です。効率改善や出力維持を確認した設計ではありません。"}]
    p["links"] = [{"id": uid(), "title": "サンプル操作マニュアル（このPC）", "url": "http://127.0.0.1:8772/manual/",
                   "component_id": "", "note": "サンプル起動中に閲覧できます。数値の技術出典ではありません。"}]
    env = store.save(p, env["revision"], "要求仕様・部品・仮定・根拠を登録")
    spec = f"演習用要求仕様書 1版\n{NOTICE}\n\n目標軸出力: 100 kW\n設計回転数: 60000 rpm\n空気流量: 1.20 kg/s（全流量）\n圧縮機全圧比: 3.8\nタービン入口全温: 1123 K\n大気温度: 288.15 K\n大気圧力: 101.325 kPa（絶対圧）\n熱効率目標: 未定\n\n設計値の整合性、熱力学性能、応力、振動、製造可能性の検証は行っていません。\n"
    (directory / "01_要求仕様書_演習用.txt").write_bytes(spec.encode("utf-8-sig"))
    env = store.upload(p["id"], env["revision"], "01_要求仕様書_演習用.txt", spec.encode("utf-8-sig"))
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(["項目", "値", "単位", "出典", "備考"])
    writer.writerows([[x[k] for k in ("name", "value", "unit", "source", "note")] for x in compressor["parameters"]])
    csv_content = output.getvalue().encode("utf-8-sig")
    (directory / "02_圧縮機_入力一覧_A.csv").write_bytes(csv_content)
    (directory / "03_追加資料_演習用.txt").write_text("添付の追加・ダウンロード・登録解除を確認するための架空資料です。計算結果ではありません。\n", encoding="utf-8-sig")
    env = store.upload(p["id"], env["revision"], "02_圧縮機_入力一覧_A.csv", csv_content, compressor["id"])
    baseline = env
    alternative = store.clone(p["id"], "【サンプル】100 kW級GT・低回転案 B")
    bp = alternative["project"]
    bp["code"] = "DEMO-GT-B"
    bp["purpose"] += "\n低回転案の変更点を基準案と比較する演習。"
    next(x for x in bp["requirements"] if x["name"] == "設計回転数").update(value="55000", source="演習用変更指示 B-01", note="変更案。性能・寸法の成立性は未検証。")
    bc = next(c for c in bp["components"] if c["kind"] == "compressor")
    for row in bc["parameters"]:
        if row["name"] == "インペラ出口外径":
            row.update(value="118", source="演習用変更指示 B-01")
        elif row["name"] == "断熱効率の目標":
            row.update(value="0.80", source="演習用変更指示 B-01")
    # A's attached CSV is intentionally retained as a reference copy; no implicit synchronization.
    bp["notes"].insert(0, {"id": uid(), "title": "B案の変更と添付資料の更新要否", "created_at": now(),
                           "body": "回転数60000→55000 rpm、インペラ外径110→118 mm、効率目標0.78→0.80を手入力で変更。\n添付CSVはA案時点のコピーを参照用に保持しています。現在のB案の設計値と同一ではありません。\n資料の版を確認し、必要なら新しいCSVを追加する運用を学ぶ例です。"})
    alternative = store.save(bp, alternative["revision"], "低回転案の回転数・外径・効率目標を変更")
    manifest = {"notice": NOTICE, "baseline_id": baseline["project"]["id"], "alternative_id": alternative["project"]["id"]}
    for label, env in (("A_基準案", baseline), ("B_低回転案", alternative)):
        (directory / f"{label}.json").write_text(json.dumps(store.export(env["project"]["id"]), ensure_ascii=False, indent=2), encoding="utf-8")
    (directory / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"created": True, "database": str(db_path), **manifest}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--directory", type=Path, default=ROOT / "samples")
    args = parser.parse_args()
    print(json.dumps(create_samples(args.directory), ensure_ascii=False))
