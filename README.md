# Local Coding Harness for Microsoft Copilot (v0.2 PoC)

「無料版 Microsoft Copilot向け Local Coding Harness 仕様書 v0.2」の実装です。
Copilot Chat (無料版) を推論・diff 生成エンジンとして使い、ローカルの Python Harness が
Context 生成・patch 検証/適用・build・flash・実機 test・状態管理を担当します。
マイコン開発のほか、Python のローカルツール、Excel VBA マクロ、PowerShell / バッチ / JavaScript などの
便利ツールにも、用途別プロファイルで対応します。

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
| デスクトップ版 Excel (VBA のみ) | ブックへの取り込みとテスト用マクロの実行を自動化 | 手動モード (人が VBE で取り込み・テストし、`result` で結果を入力) |

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

### 用途別プロファイル

`init` はプロジェクト内のファイルから用途を自動判定し、設定・Copilot への役割 (AGENT_RULES)・STATE.md の見出しを切り替えます。
判定が違う場合は `init --profile <名前> --force` で作り直します (人が最初に 1 回行う操作です。Copilot は設定に関与しません)。

| プロファイル | 自動判定の手掛かり | build | flash | test (既定) |
|---|---|---|---|---|
| `mcu` | .ino / .c / .cpp / platformio.ini | `arduino-cli compile` など (必須) | `arduino-cli upload` など | TEST 行 (外部スクリプト / UART) |
| `python` | .py | `builtin:python-syntax` (内蔵の構文チェック) | なし | 終了コード (`python -m unittest discover -v`、pytest も可) |
| `vba` | .xlsm / .xlsb / .bas / .cls | `builtin:vba-build` (ブックへ取り込み) | なし | `builtin:vba-test` (テスト用マクロの TEST 行) |
| `tool` | .ps1 / .bat / .js / .ts / .cs など | 任意 (未設定なら省略) | なし | 終了コード (コマンドは自分で設定) |

`build.required: false` にすると、build コマンドが無くても先へ進みます。

### テストの判定方法 (`test.mode`)

| mode | 判定 |
|---|---|
| `command` | 出力の `TEST:<NAME>:<PASS\|FAIL>[:detail]` 行で判定 |
| `exitcode` | 終了コード 0 なら PASS。失敗時は pytest / unittest の失敗テスト名と例外をログから抽出し、Copilot に渡す |
| `uart` | Harness が UART を直接読み、TEST 行で判定 (マイコン) |
| `manual` | `run` は build まで行って停止。人がテストし、`result --pass` / `result --fail "内容"` / `result --paste` (TEST 行をコピー) で入力 |

## 使い方 (1 iteration で人が行う操作は「送信」と「コピー → paste」の 2 回)

```bash
cd project/.copilot-harness
python harness.py start "CAN RX timeoutを修正する" -k CAN,RX -c "動的メモリ禁止" -c "public API変更禁止"
python harness.py run          # 任意: 現状の build/flash/test 結果を最初の handoff に含める
```

1. `handoff/` の `STATE.md` / `SOURCE_CONTEXT.md` / `TEST_RESULT.md` を Copilot に添付し、`NEXT_PROMPT.txt` の内容を貼り付けて送信する
2. Copilot の回答の下にあるコピーボタンを押し、`python harness.py paste` を実行する

`paste` はクリップボードの回答を `inbox/copilot_response.txt` に保存し、そのまま apply → (PATCH なら) build → flash → test → 判定 → 次の handoff 生成まで行います。
NEED_CONTEXT の場合は handoff を作り直すので、1. に戻ります。
`TASK COMPLETE` が出たら `python harness.py diff` (または `git diff`) で変更をレビューし、人が commit します (Harness は commit しません)。

- クリップボードの内容が回答でない場合 (NEXT_PROMPT のまま、空、ACTION 行が無い) は保存せずに止まります
- 回答をファイルに保存する運用なら、`paste` の代わりに `apply --run`、または `watch` (保存を検知して自動処理) が使えます。
  `watch` と `paste` を同時に使う場合は `paste --save-only` にしてください
- Copilot の説明文 (REASON / SUMMARY / FINDING / SUSPECTS) は `handoff.response_language` (既定: 日本語) で書くよう NEXT_PROMPT で指示します

## 困ったとき (レビューで問題があった場合)

レビューでは `python harness.py status` の Phase と `python harness.py diff` の変更内容を確認します。

| 状況 | 操作 |
|---|---|
| Phase が `TEST_FAILED` / `BUILD_FAILED` | 失敗内容入りの handoff が新しい RUN_ID で作り直されているので、そのまま手順をもう一度 (3ファイル添付 → NEXT_PROMPT 送信 → コピー → `paste`) |
| `DONE` だが修正内容が NG | `rollback` → `note --finding "採用しない理由" --constraint "守ってほしい条件"` → `run` (戻した状態で測り直し、handoff を作り直す) → Copilot へ |
| `DONE` で OK、続けて追加修正したい | `note --objective "次にやってほしいこと"` → `prepare` (新しい RUN_ID) → Copilot へ。別の作業なら `start "新しいタスク"` |
| Phase が `FLASH_FAILED` | Copilot ではなく書込み環境の問題。USB 接続・COM ポート・シリアルモニタの開きっぱなしを確認して `run` |
| `HUMAN_REVIEW_REQUIRED` (変更量の上限超え) | `inbox/copilot_response.txt` を確認し、適用してよければ `apply --allow-large --reprocess --run` |
| `HUMAN_REVIEW_REQUIRED` (5 回で解決しない) | 方針を見直して `note` で情報・条件を追加 → `note --resume` (回数リセット) → `next` → Copilot へ |
| `paste` が RUN_ID 不一致で止まる | 古い handoff / 古い回答を使っている。最新の 3 ファイルで送り直す |
| `paste` が形式エラー・patch 検証・`git apply --check` で止まる | 理由入りの handoff が作り直されているので、そのまま Copilot へ送り直す (Copilot が理由を見て修正する) |

- `rollback` すると STATE.md に「人の判断でロールバック」と過去の試行 (HISTORY) が残り、NEXT_PROMPT の「同じ修正を繰り返さない」指示と合わせて、Copilot に別の方法を考えさせます
- Copilot は同じチャットで続けても新しいチャットでも構いません。Harness は会話履歴に頼らないので、**毎回最新の 3 ファイルを添付する**ことだけ守ってください
- 人が確認済みの事項は `note -v "CAN クロック設定 OK"` のように登録すると、Copilot が同じ確認を提案しなくなります

## コマンド

| コマンド | 内容 |
|---|---|
| `init [--project DIR] [--profile auto\|mcu\|python\|vba\|tool]` | `.copilot-harness/` を初期化 (既定は用途を自動判定) |
| `start "<task>" [-k KW] [-c 制約]` | タスク開始 (State 初期化) → `prepare` |
| `prepare` | git status/diff 取得、repository scan、関連 source 選定、handoff 生成 (新 RUN_ID) |
| `paste [--save-only] [--no-run]` | クリップボードの Copilot の回答を inbox に保存し、apply → (PATCH なら) run まで実行 |
| `apply [--run]` | 回答を解析。PATCH: 抽出→検証→`git apply --check`→適用 / NEED_CONTEXT: 要求ファイルを context に追加 |
| `build` / `flash` / `test` | 各ステージを個別実行 (flash は BUILD PASS、test は FLASH PASS が前提) |
| `run` | build → flash → test → 成功判定 → STATE/TEST_RESULT/NEXT_PROMPT 生成 |
| `result [--paste\|--file F\|--pass\|--fail "内容"]` | 手動テストの結果を入力して判定 (`test.mode: manual`) |
| `vba-export` | Excel ブックの VBA モジュールを `src/vba` へ書き出す (VBA プロファイル) |
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

## Excel VBA で使う

VBA のコードはブック (.xlsm) の中にあり、そのままでは diff を当てられないため、
モジュールをテキスト (`src/vba/*.bas` / `*.cls` / `*.frm`) に書き出し、それを正本として Copilot に修正させます。
ブック自体は patch 対象外 (禁止対象) です。

**前提 (自動モード)**: デスクトップ版 Excel (ライセンス版) と、Excel の
[ファイル] → [オプション] → [トラスト センター] → [トラスト センターの設定] → [マクロの設定] にある
「VBA プロジェクト オブジェクト モデルへのアクセスを信頼する」が ON であること。
セキュリティ設定なので、ON にするかは組織のルールに従って判断してください。

```bash
python harness.py init --project C:\path\to\book_folder
```
```bash
python harness.py vba-export
```

1. `init` でプロファイル `vba` が選ばれ、フォルダ内のブックが `vba.workbook` に設定されます
2. `vba-export` でモジュールを `src/vba` に書き出します (Excel 上で直接編集した後も、毎回書き出し直してください)
3. テスト用モジュールのひな形 `.copilot-harness/copilot_harness/assets/HarnessTests.bas` を `src/vba` にコピーし、
   `RunAll` にテストを書きます (`TEST:<名前>:<PASS|FAIL>[:詳細]` 行を返す Function)
4. 以降は通常どおり `start` → Copilot → `paste`。`build` でブックをバックアップ (`.copilot-harness/backup/vba/`) してから
   モジュールを取り込んで保存し、`test` で `RunAll` を実行して判定します

- Shift-JIS のモジュールは内蔵 patch エンジンで文字コードを保ったまま修正し、Copilot が作る新規モジュールも Shift-JIS + CRLF で保存します
- シート / ThisWorkbook のモジュールはコード部分だけを置き換えます
- Excel がダイアログ (コンパイルエラーや MsgBox) で止まった場合は `vba.timeout` 秒で強制終了し、build / test を FAIL にします
- ブックを Excel で開いたままだと build は失敗します (閉じてから実行)

**手動モード (Excel の自動操作を使わない)**: config.yaml を `build.command: ""` / `build.required: false` / `test.mode: manual` にします。
Copilot の修正後、人が VBE の [ファイル] → [ファイルのインポート] で `src/vba` のモジュールを取り込み、
イミディエイト ウィンドウで `HarnessTests.PrintAll` を実行して、表示された TEST 行をコピーし `python harness.py result --paste` を実行します。

> 注: Excel を自動操作する部分 (`vba-export` / `builtin:vba-build` / `builtin:vba-test`) は、
> 開発環境にライセンス版 Excel が無かったため実機では未検証です (構文チェックと、Excel を使わない処理のテストのみ実施)。

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
