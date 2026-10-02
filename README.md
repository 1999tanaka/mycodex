# Local Coding Harness for Microsoft Copilot (v0.2 PoC)

「無料版 Microsoft Copilot向け Local Coding Harness 仕様書 v0.2」の実装です。
Copilot Chat (無料版) を推論・diff 生成エンジンとして使い、ローカルの Python Harness が
Context 生成・patch 検証/適用・build・flash・実機 test・状態管理を担当します。

```text
Copilot = Brain   /   Python Harness = Memory + Hands + Tools   /   Human = Bridge + Final Authority
```

Copilot API・Copilot Studio・OneDrive 同期・Notebook は使いません (Notebook は任意)。

## 必要環境

**必須は Python 3.11 以降のみ**です。標準ライブラリだけで全機能が動作します。

| 任意 | 有る場合 | 無い場合 |
|---|---|---|
| PyYAML | config.yaml を PyYAML で読む | 内蔵 YAML パーサ (config.yaml で使う構文に対応。anchor/alias/tag/複数ドキュメントは非対応) |
| git | `git apply --check` / `git apply`、`git diff` で変更中ファイル検出 | 内蔵 patch エンジン、タスク開始時スナップショットとの比較で変更検出 |
| pyserial | UART を pyserial で読む | 内蔵シリアル実装 (Windows: Win32 API / Linux・macOS: termios) で読む |

`python harness.py status` の `Environment:` 行で、どちらで動いているか確認できます。

## 導入

```bash
python harness.py init --project C:\path\to\your_project
```

(任意パッケージを入れる場合: `pip install -r requirements.txt`)

対象プロジェクトに `.copilot-harness/` が作られ、Harness 本体 (`harness.py` + `copilot_harness/`) もコピーされます。

```text
project/
├─ src/  include/  test/  tools/  main.ino
└─ .copilot-harness/
    ├─ harness.py, copilot_harness/   Harness 本体
    ├─ config.yaml                    build / flash / test コマンド、許可パス、制限値
    ├─ static/                        PROJECT / ARCHITECTURE / HARDWARE / CODING_RULES / AGENT_RULES
    ├─ handoff/                       Harness → Copilot (STATE.md, SOURCE_CONTEXT.md, TEST_RESULT.md, NEXT_PROMPT.txt)
    ├─ inbox/copilot_response.txt     Copilot → Harness
    ├─ logs/<RUN_ID>/                 build.log, flash.log, test.log, git_apply.log
    ├─ history/<RUN_ID>/              各 RUN の handoff スナップショット、Copilot 回答、patch
    ├─ backup/<RUN_ID>/               patch 適用前のファイル (rollback 用)
    └─ state.json                     Harness 内部 State
```

`config.yaml` の `build.command` / `flash.command` / `test.command` を環境に合わせて編集してください
(例: `arduino-cli compile ...`, `platformio run`, `STM32_Programmer_CLI ...`)。

## 使い方 (1 iteration で人が行う操作は 2 回)

```bash
cd project/.copilot-harness
python harness.py start "CAN RX timeoutを修正する" -k CAN,RX -c "動的メモリ禁止" -c "public API変更禁止"
python harness.py run          # 任意: 現状の build/flash/test 結果を最初の handoff に含める
python harness.py watch        # inbox を監視して apply → run を自動実行
```

1. `handoff/` の `STATE.md` / `SOURCE_CONTEXT.md` / `TEST_RESULT.md` を Copilot に添付し、`NEXT_PROMPT.txt` の内容を貼り付ける
2. Copilot の回答全文を `inbox/copilot_response.txt` に保存する

あとは Harness が処理します (watch を使わない場合は `python harness.py apply --run`)。
`TASK COMPLETE` が出たら `python harness.py diff` (または `git diff`) で変更をレビューし、人が commit します (Harness は commit しません)。

## コマンド

| コマンド | 内容 |
|---|---|
| `init [--project DIR]` | `.copilot-harness/` を初期化 |
| `start "<task>" [-k KW] [-c 制約]` | タスク開始 (State 初期化) → `prepare` |
| `prepare` | git status/diff 取得、repository scan、関連 source 選定、handoff 生成 (新 RUN_ID) |
| `apply [--run]` | 回答を解析。PATCH: 抽出→検証→`git apply --check`→適用 / NEED_CONTEXT: 要求ファイルを context に追加 |
| `build` / `flash` / `test` | 各ステージを個別実行 (flash は BUILD PASS、test は FLASH PASS が前提) |
| `run` | build → flash → test → 成功判定 → STATE/TEST_RESULT/NEXT_PROMPT 生成 |
| `next` | 現在状態から `handoff/` を再生成 |
| `status` | 現在状態を表示 |
| `diff` | このタスクで Harness が適用した変更を unified diff で表示 (git 不要) |
| `ports` | シリアルポート一覧 (USB-UART の種類も表示。ポートは開かない) |
| `monitor [-p COM5] [-s 秒] [-u TEST:END]` | UART ログを表示し `logs/monitor/` に保存 |
| `watch [--no-run]` | `inbox/copilot_response.txt` の保存を検知して自動処理 |
| `rollback [RUN_ID]` | Harness が適用した patch を backup から戻す |
| `note` | 人が STATE を更新 (`-v` VERIFIED, `--finding`, `--suspect`, `--objective`, `--resume` など) |

## Copilot 応答プロトコル

```text
ACTION: NEED_CONTEXT          ACTION: PATCH
RUN_ID: 20261002-0017         RUN_ID: 20261002-0017
FILES:                        FILES:
- src/interrupt.cpp           - src/can.cpp
REASON:                       BEGIN_PATCH
...                           --- a/src/can.cpp
                              +++ b/src/can.cpp
                              @@ -17,5 +17,6 @@
                              ...
                              END_PATCH
                              SUMMARY: ...
```

- patch は `BEGIN_PATCH`〜`END_PATCH` の間のみ抽出 (内側の ```` ```diff ```` フェンスは除去)
- `**ACTION:**` などの Markdown 装飾、CRLF、不可視文字は正規化
- 任意で `FINDING:` / `SUSPECTS:` / `NEXT_OBJECTIVE:` を返すと STATE.md に引き継ぐ (VERIFIED は人のみ更新可)

## MCU テストプロトコル

テストスクリプト (または `test.mode: uart` で Harness が直接読む UART) の出力から次の形式を解析します。

```text
TEST:ADC:PASS:value=2015
TEST:CAN_RX:FAIL:RX_TIMEOUT
TEST:END
```

`test.required` に必須テスト名を書くと、出力されなかったテストは `MISSING` として FAIL になります。

## マイコンへの書込みと UART ログ (pyserial 不要)

書込みは `flash.command` の外部ツールで行います (Harness 自身はシリアル通信しません)。

```yaml
flash:
  command: arduino-cli upload --fqbn arduino:avr:uno -p COM4 .   # STM32_Programmer_CLI / pio run -t upload なども可
test:
  mode: uart          # Harness が直接 UART を読む
  required: [UART, ADC, CAN_TX, CAN_RX]
  uart:
    port: COM4        # python harness.py ports で確認
    baudrate: 115200
    timeout: 30       # TEST:END が来なければ timeout で FAIL
    end_marker: "TEST:END"
    reset: none       # none / dtr (Arduino AVR 等) / rts (ESP32 等)
    send: ""          # 開いた後に送る文字列 (例: "RUN\n" でテスト開始させる)
    open_retry: 5     # flash 直後の USB 再接続待ち
```

`python harness.py run` で build → flash → UART 受信 → TEST 行の判定まで行い、
UART ログ全体を `logs/<RUN_ID>/test.log`、抜粋を `TEST_RESULT.md` に書きます。
テスト以外でログだけ取りたい場合は `python harness.py monitor` を使います。

注意:
- 書込みと UART が同じ COM ポートの場合でも、Harness は flash 完了後にポートを開くので競合しません。
  ただし Arduino IDE のシリアルモニタ等が開いていると書込みも UART も失敗します (アクセス拒否)
- Arduino Uno/Nano 等は、ポートを開くと (DTR ON) 自動リセットされ、起動直後からログを取れます
- 自動リセットの無いボード (ST-LINK VCP など) は flash 後すぐ動き出すため、最初の数行を取り逃がす場合があります。
  ファームウェア側でテスト結果を周期的に出力するか、`send` で受け取ったコマンドを合図にテストを始める形にしてください

## 安全設計

- Copilot の出力はすべて Untrusted Input。shell コマンドは実行せず、build/flash/test は `config.yaml` のコマンドのみ (shell 不使用)
- patch 検証: 構文 → パス (絶対パス・`..`・ドライブ・symlink・Windows 予約名を拒否) → 許可パス → 禁止対象 (`.git/**`, `.env`, `*.key`, `*.pem`, `credential/**`, `secret/**`, `.copilot-harness/**`) → 変更ファイル数/行数 → `git apply --check` → backup → 適用
- rename / binary / file mode 変更は拒否。制限超過は `HUMAN_REVIEW_REQUIRED` で停止
- Context Builder は `.env`, `*.key`, `*.pem`, `password*`, `credential*`, `secret*`, `private*` を必ず除外
- `MAX_ITERATIONS` 回で成功しなければ `HUMAN_REVIEW_REQUIRED`。成功判定は Harness が行う (Copilot にはさせない)
- git 操作は status / diff / apply のみ。commit・push はしない

## git / PyYAML が無い場合の動作

- **patch 適用**: `patch.engine: auto` (既定) は git があれば `git apply`、無ければ内蔵エンジン。
  内蔵エンジンは git apply と同じくコンテキスト完全一致 (fuzz なし)・位置ずれは最も近い一致箇所を採用し、
  全ファイルを検査してから書き込みます。改行コード (LF/CRLF) と文字コード (UTF-8/BOM/CP932) を保持します。
  `engine: git` / `engine: python` で固定もできます
- **変更中ファイル (P1)**: git repository なら `git diff`、そうでなければ `start` 時に記録したソースファイルの
  スナップショット (`baseline.json`) との比較。**git 無しでは `start` より前に行った変更は検出できません**
  (必要なら `context.pinned_files` やキーワードで指定してください)
- **レビュー**: `python harness.py diff` が backup と現在の内容を比較して表示します
- 環境変数 `HARNESS_NO_GIT=1` で、git がインストールされていても git を使わないモードにできます

## Context 選択

`P0` NEED_CONTEXT 要求・pinned → `P1` git diff 変更中 → `P2` build error に現れたファイル →
`P3` test failure (ログ中のパス、失敗テスト名のキーワード) → `P4` ユーザーキーワード →
`P5` `#include` 依存 → `P6` 同名 header/source。手掛かりが無い場合は `.ino` / `main.*` を入れます。
件数・文字数の上限 (`context.max_files` など) を超えたファイルは `OMITTED FILES` に列挙されます。

## RUN_ID

`YYYYMMDD-NNNN` 形式。`prepare` と PATCH 適用時 (コードが変わった時) に新しい RUN_ID を発行します。
NEED_CONTEXT は同じ RUN_ID のまま handoff を再生成します。
回答の RUN_ID が現在と異なる場合は古い回答として拒否し、同じ回答の二重適用も防ぎます。

## 仕様に対する補足 (実装上の工夫)

- Copilot の diff でありがちな問題を吸収: `@@ ... @@` のように行番号の無い hunk は対象ファイルの内容から位置を特定、
  hunk の行数は再計算、空のコンテキスト行の先頭スペース欠落を補正、CRLF のファイルには CRLF の patch を生成
- `git apply --check` 失敗や検証エラーは STATE.md の `CONTEXT NOTES` に書かれ、次の handoff で Copilot に伝わる
- STATE.md の `HISTORY` に過去の試行と結果を残し、Copilot が会話履歴を忘れても同じ修正を繰り返さないようにする
- `handoff.use_notebook: false` (既定) の場合、記入済みの `static/*.md` を SOURCE_CONTEXT.md に、AGENT_RULES を NEXT_PROMPT に埋め込む
  (テンプレートの `<!-- harness:template -->` 行を消すと記入済み扱い)

## デモ (実機・Copilot 不要)

```bash
python examples/run_demo.py
```

`examples/arduino_can_demo` (CAN RX 割り込み未許可のバグ入り) を一時ディレクトリにコピーし、
baseline 実行 (CAN_RX FAIL) → NEED_CONTEXT → PATCH → build/flash/test (シミュレーション) → TASK COMPLETE まで実行します。

## テスト

```bash
python -m unittest discover -s tests
# または
python -m pytest tests
```

## v0.2 で実装していないもの (仕様 51)

Copilot UI / ブラウザ自動操作、OCR、完全無人実行、AI による任意 shell 実行・自動 commit・自動 push、Notebook 自動更新。
