# 設計情報と計算モジュールの分離

## 構成

- `model.py`: schema_version=1 の設計情報、入力検証、部品構成テンプレート。物理計算は持たない。
- `store.py`: SQLite、更新競合の検出、変更履歴、ファイル、複製、JSON交換。
- `server.py`: 127.0.0.1 のみ待受するHTTP API。Host・Origin・起動ごとの操作トークンを確認。任意ファイルパスへのアクセスや任意コマンドの実行は公開しない。
- `static/`: 外部CDNを使わない日本語UI。保存APIの成功を確認して画面を更新する。
- `data/designs.sqlite3`: ユーザーデータ。projects/history/filesの3テーブル。

## 共通データ

Project: `schema_version, id, name, code, application, architecture, owner, purpose, status, requirements, components, attachments, links, notes, extensions`

Component: `id, name, kind, stage, status, owner, description, parameters`

Parameter: `id, name, value, unit, source, note`。値は文字列で、未設定は空文字。勝手な数値化・単位換算はしない。数値として使う段階で、計算モジュール側が定義・適用条件を検証する。

識別子はUUID。案件の複製では案件・添付実体に新しいIDを割り当て、部品・項目のIDを維持して比較に利用する。したがって部品参照キーは `(project_id, component_id)`。添付実体はDB内BLOBで、パス文字列ではない。

保存APIは `{project, revision}` を受け取り、現在のrevisionと一致した場合だけ新しいrevisionを作る。全履歴は入力スナップショットを保持。添付実体を履歴ごとに重複保存せず、IDで参照する。

JSON交換ファイルは `format: gas-turbine-manager, bundle_version: 1, project, revision, created_at, updated_at, exported_at, files`。filesは添付ID→Base64。取込前に形式・関連部品・サイズ・ファイル内容の存在を検証し、失敗時は全体をロールバックする。

## モジュール追加時の契約（将来設計）

現時点でプラグインローダーや計算実行APIは実装しない。`extensions` は名前空間付きJSON情報を保持する予約領域で、内容を実行しない。

将来の各計算モジュールは、次の情報を持つ入出力アダプターとして追加する。

1. module_id、module_version、入出力schema_version。
2. project_id、component_id、入力元revision、単位・座標・状態量定義。
3. 入力スナップショットとそのハッシュ、実行条件、実行ID、開始・終了日時。
4. 成功・失敗・未実行・更新必要の状態、結果値、成果物、計算ログ。
5. 管理情報への反映は、出典が残る明示的な取込操作として実装する。

入力変更後の結果を最新として扱わない。管理上の「確定」と計算・試験による検証状態は別に持つ。旧schemaは移行処理を用意してから変更する。
