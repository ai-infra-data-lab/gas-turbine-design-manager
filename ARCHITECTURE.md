# 設計情報と計算モジュールの分離

## 構成

- `model.py`: schema_version=1 の設計情報、入力検証、部品構成テンプレート。物理計算は持たない。
- `store.py`: SQLite、更新競合の検出、変更履歴、ファイル、複製、JSON交換。
- `results.py`: Cycle Studio と Blade Designer の成果物 JSON の検証、識別子・入力元・表示用の情報の抽出。
- `server.py`: 127.0.0.1 のみ待受するHTTP API。Host・Origin・起動ごとの操作トークンを確認。任意ファイルパスへのアクセスや任意コマンドの実行は公開しない。
- `static/`: 外部CDNを使わない日本語UI。保存APIの成功を確認して画面を更新する。
- `mbse.py`: 運転モード、機能割当、接続、要求の関係、検証ケースの入力検証と判定に必要な依存関係。
- `data/designs.sqlite3`: 設計情報・履歴・添付、成果物・詳細一式、検証の判定履歴を保存。

## 共通データ

Project: `schema_version, id, name, code, application, architecture, owner, purpose, status, requirements, components, attachments, links, notes, extensions`

案件の任意フィールド `stations` と `station_layout` は評価断面と生成時の段構成を保持する。旧案件は構成の保存操作で GasTurb 15 発電用の番号へ移行する。`template_key` は段数変更でひな形行を照合する固定ID。部品断面から全機断面への参照はUUID。同じ案件内で断面コードと、冷却条件ごとの割り当て済み Cycle Studio 位置IDは一意。案件履歴・書出し・複製は断面と構成を保存する。

Component: `id, name, kind, stage, status, owner, description, parameters`

Parameter: `id, name, value, unit, source, note`。値は文字列で、未設定は空文字。勝手な数値化・単位換算はしない。数値として使う段階で、計算モジュール側が定義・適用条件を検証する。

識別子はUUID。案件の複製では案件・添付実体に新しいIDを割り当て、部品・項目のIDを維持して比較に利用する。したがって部品参照キーは `(project_id, component_id)`。添付実体はDB内BLOBで、パス文字列ではない。

保存APIは `{project, revision}` を受け取り、現在のrevisionと一致した場合だけ新しいrevisionを作る。全履歴は入力スナップショットを保持。添付実体を履歴ごとに重複保存せず、IDで参照する。

JSON交換ファイルは `format: gas-turbine-manager, bundle_version: 1, project, revision, created_at, updated_at, exported_at, files`。filesは添付ID→Base64。取込前に形式・関連部品・サイズ・ファイル内容の存在を検証し、失敗時は全体をロールバックする。

## 要求・検証

任意の `project.mbse` に運転モード、機能、機器間接続、要求IDに対応する関連付け、検証ケースを保持する。元の要求値は `requirements` の同じ行を使う。参照切れ・循環した親要求・重複番号を保存時に検出する。案件の改訂、復元、JSON交換ではモデル定義を保持する。

判定履歴は `mbse_evidence` に追記し、設計版は進めない。判定時点の要求、関連設計、運転モード、検証手順から依存内容を比較し、影響する判定を再検証対象にする。判定条件だけを変えた場合は、関連設計と運転条件が一致する既存の解析結果を再利用できる。

`GET /api/mbse-evidence` は案件の判定履歴と現行の状態、絞り込みに使う部品・設計値IDを返す。`POST /api/mbse-evidence` は現行版の競合を確認して、確認済みの運転条件と根拠に判定を関連付ける。定格出力と冷間給油の指標はそれぞれ保存済みの冷却後サイクル、給油詳細JSONから取得する。`GET /api/mbse-file` からSHA-256に一致する元バイト列を取り出せる。

## 設計成果物

`design_results` は案件ID、入力元の設計版、部品ID、成果物の種類、出力元の識別情報、元ファイルを保存する。翼形状ではプロジェクトの元バイト列の SHA-256 と行IDの並びを `.handoff.json` の記録と照合する。既存DBでは起動時にテーブルを追加する。

成果物を登録するトランザクションで、現行版の競合と登録先の履歴スナップショットを確認する。同じ案件・種類・成果物識別子の再取込は登録済みの内容と対象部品を照合し、既存の紐付けを保持する。成果物の登録は `projects` と `history` を変更しない。

性能比較（`kind: "performance"`）は Cycle Studio の比較一式 schema 1 を原文のまま保持する。`POST /api/result-import` で `blade_ids` に `equal` と `75-percent` の翼形状登録IDを指定する。登録先の案件・版・部品と手渡し記録の配分を照合し、各案の翼形状SHA-256・サイクル実行ID・出典を記録する。`GET /api/result-file` の `part=equal` と `part=75-percent` で再計算ケースを取得できる。

翼形状取込で `replaces_blade_id` を指定すると、同じ案件・版・部品・配分の形状を明示的に差し替える。過去の成果物は保持し、一覧の表示時に差し替え前の形状を参照する性能比較を再評価対象にする。既存の翼形状レコードに配分情報がない場合は、保存済みの手渡し記録を参照する。

一覧は現行版との一致で再評価の要否を表示する。翼形状は同一案件・同一設計版で受け渡し記録の配分後サイクル実行IDと入力ハッシュに一致するサイクル結果を参照する。従来の設計JSON交換と案件複製の対象は設計データと添付ファイル。

## モジュール追加時の契約

冷却連携レポートは `kind: "cooling"` で登録する。設計版のタービン断面コードをUUIDに対応づけ、排出側の絶対静圧、供給側の絶対全圧、冷却絶対全温と流量を保存する。元のサイクル実行IDと入力ハッシュを同一設計版のサイクル成果物と照合できる。定格通しケースの要約は `kind: "suite"` で登録する。要約には各計算レポートのファイル名とSHA-256を含む。

通しケースの詳細レポートは `POST /api/result-import` の任意項目 `suite_files` に `[{name, data}]` 形式で指定する。各 `data` は元JSONバイトのBase64。要約とすべての詳細レポートを照合して `suite_bundles` にZIPとして保存する。要約登録済みのケースへの原本追加と新規一括登録は、それぞれ一つのトランザクションで確定する。`GET /api/result-file` の `part=bundle` から取得できる。

`GET /api/suite-comparison?id=…&reference=…&candidate=…` は同じ案件の異なる2件を選び、保存済みの冷却後サイクル・冷却・軸力レポートから運転点と比較結果を抽出する。ケースの設計版と、同じ基準サイクルに紐づく翼形状の差し替え状況は一覧の登録情報を照会する。

評価断面照合は `kind: "station-review"` で登録する。入力元の版の断面コードを断面IDに対応づける。全圧損失の値は同じ出典種別の入口・出口の絶対全圧から照合する。遠心の形状は同じ設計版のプロジェクトSHA-256・サイクル実行ID・入力ハッシュで照合し、差し替え済みの形状を参照する報告は再評価対象とする。

現時点でプラグインローダーや計算実行APIは実装しない。`extensions` は名前空間付きJSON情報を保持する予約領域で、内容を実行しない。

将来の各計算モジュールは、次の情報を持つ入出力アダプターとして追加する。

1. module_id、module_version、入出力schema_version。
2. project_id、component_id、入力元revision、単位・座標・状態量定義。
3. 入力スナップショットとそのハッシュ、実行条件、実行ID、開始・終了日時。
4. 成功・失敗・未実行・更新必要の状態、結果値、成果物、計算ログ。
5. 管理情報への反映は、出典が残る明示的な取込操作として実装する。

入力変更後の結果を最新として扱わない。管理上の「確定」と計算・試験による検証状態は別に持つ。旧schemaは移行処理を用意してから変更する。
