# 設計情報と計算モジュールの分離

## 構成

- `model.py`: schema_version=1 の設計情報、入力検証、部品構成テンプレート。物理計算は持たない。
- `store.py`: SQLite、更新競合の検出、変更履歴、ファイル、複製、JSON交換。
- `results.py`: Cycle Studio と Blade Designer の成果物 JSON の検証、識別子・入力元・表示用の情報の抽出。
- `server.py`: 127.0.0.1 のみ待受するHTTP API。Host・Origin・起動ごとの操作トークンを確認。任意ファイルパスへのアクセスや任意コマンドの実行は公開しない。
- `static/`: 外部CDNを使わない日本語UI。保存APIの成功を確認して画面を更新する。
- `data/designs.sqlite3`: ユーザーデータ。`projects`、`history`、`files`、`design_results` の4テーブル。

## 共通データ

Project: `schema_version, id, name, code, application, architecture, owner, purpose, status, requirements, components, attachments, links, notes, extensions`

案件の任意フィールド `stations` と `station_layout` は評価断面と生成時の段構成を保持する。旧案件は構成の保存操作で GasTurb 15 発電用の番号へ移行する。`template_key` は段数変更でひな形行を照合する固定ID。部品断面から全機断面への参照はUUID。同じ案件内で断面コードと、冷却条件ごとの割り当て済み Cycle Studio 位置IDは一意。案件履歴・書出し・複製は断面と構成を保存する。

Component: `id, name, kind, stage, status, owner, description, parameters`

Parameter: `id, name, value, unit, source, note`。値は文字列で、未設定は空文字。勝手な数値化・単位換算はしない。数値として使う段階で、計算モジュール側が定義・適用条件を検証する。

識別子はUUID。案件の複製では案件・添付実体に新しいIDを割り当て、部品・項目のIDを維持して比較に利用する。したがって部品参照キーは `(project_id, component_id)`。添付実体はDB内BLOBで、パス文字列ではない。

保存APIは `{project, revision}` を受け取り、現在のrevisionと一致した場合だけ新しいrevisionを作る。全履歴は入力スナップショットを保持。添付実体を履歴ごとに重複保存せず、IDで参照する。

JSON交換ファイルは `format: gas-turbine-manager, bundle_version: 1, project, revision, created_at, updated_at, exported_at, files`。filesは添付ID→Base64。取込前に形式・関連部品・サイズ・ファイル内容の存在を検証し、失敗時は全体をロールバックする。

## 設計成果物

`design_results` は案件ID、入力元の設計版、部品ID、成果物の種類、出力元の識別情報、元ファイルを保存する。翼形状ではプロジェクトの元バイト列の SHA-256 と行IDの並びを `.handoff.json` の記録と照合する。既存DBでは起動時にテーブルを追加する。

成果物を登録するトランザクションで、現行版の競合と登録先の履歴スナップショットを確認する。同じ案件・種類・成果物識別子の再取込は登録済みの内容と対象部品を照合し、既存の紐付けを保持する。成果物の登録は `projects` と `history` を変更しない。

性能比較（`kind: "performance"`）は Cycle Studio の比較一式 schema 1 を原文のまま保持する。`POST /api/result-import` で `blade_ids` に `equal` と `75-percent` の翼形状登録IDを指定する。登録先の案件・版・部品と手渡し記録の配分を照合し、各案の翼形状SHA-256・サイクル実行ID・出典を記録する。`GET /api/result-file` の `part=equal` と `part=75-percent` で再計算ケースを取得できる。

翼形状取込で `replaces_blade_id` を指定すると、同じ案件・版・部品・配分の形状を明示的に差し替える。過去の成果物は保持し、一覧の表示時に差し替え前の形状を参照する性能比較を再評価対象にする。既存の翼形状レコードに配分情報がない場合は、保存済みの手渡し記録を参照する。

一覧は現行版との一致で再評価の要否を表示する。翼形状は同一案件・同一設計版で受け渡し記録の配分後サイクル実行IDと入力ハッシュに一致するサイクル結果を参照する。従来の設計JSON交換と案件複製の対象は設計データと添付ファイル。

## モジュール追加時の契約

現時点でプラグインローダーや計算実行APIは実装しない。`extensions` は名前空間付きJSON情報を保持する予約領域で、内容を実行しない。

将来の各計算モジュールは、次の情報を持つ入出力アダプターとして追加する。

1. module_id、module_version、入出力schema_version。
2. project_id、component_id、入力元revision、単位・座標・状態量定義。
3. 入力スナップショットとそのハッシュ、実行条件、実行ID、開始・終了日時。
4. 成功・失敗・未実行・更新必要の状態、結果値、成果物、計算ログ。
5. 管理情報への反映は、出典が残る明示的な取込操作として実装する。

入力変更後の結果を最新として扱わない。管理上の「確定」と計算・試験による検証状態は別に持つ。旧schemaは移行処理を用意してから変更する。
