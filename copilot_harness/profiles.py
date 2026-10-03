"""用途別プロファイル (mcu / python / vba / tool)。

init 時に config.yaml と static/ のテンプレートを用途に合わせて生成し、
実行時は STATE.md / TEST_RESULT.md の見出しを切り替える。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

TEMPLATE_MARKER = "<!-- harness:template -->"


@dataclass
class Profile:
    name: str
    title: str
    persona: str
    extra_rules: list[str]
    project_yaml: str
    security_yaml: str
    pipeline_yaml: str
    extra_yaml: str = ""
    patch_yaml: str = ""
    static: dict[str, str] = field(default_factory=dict)
    test_label: str = "TEST"
    log_label: str = "TEST LOG"


def _static(project: str, architecture: str, rules: str, extra: dict[str, str] | None = None) -> dict[str, str]:
    note = "(記入したら 1 行目の harness:template 行を削除してください。削除するまで handoff には含まれません)\n\n"
    out = {
        "PROJECT.md": f"{TEMPLATE_MARKER}\n# PROJECT\n\n{note}{project}",
        "ARCHITECTURE.md": f"{TEMPLATE_MARKER}\n# ARCHITECTURE\n\n{architecture}",
        "CODING_RULES.md": f"{TEMPLATE_MARKER}\n# CODING RULES\n\n{rules}",
    }
    for name, body in (extra or {}).items():
        out[name] = f"{TEMPLATE_MARKER}\n# {name[:-3]}\n\n{body}"
    return out


# ---------------------------------------------------------------- mcu
MCU = Profile(
    name="mcu",
    title="マイコン (組み込み C/C++ / Arduino)",
    persona="あなたは組み込みソフトウェア開発支援Agentです。",
    extra_rules=["存在しないAPI、関数、レジスタを\n推測して作らないでください。"],
    project_yaml="""\
project:
  root: ..
  profile: mcu
  languages:
    - cpp
    - ino
  # include 解決に使うディレクトリ
  include_dirs:
    - include
    - src
""",
    security_yaml="""\
  allowed_paths:
    - src
    - include
    - test
    - "*.ino"
""",
    pipeline_yaml="""\
# build / flash / test は事前定義されたコマンドのみ実行する (shell は使わない)
build:
  command: >
    arduino-cli compile
    --fqbn arduino:avr:uno
    .
  timeout: 120

flash:
  # 空にすると SKIPPED 扱い。書込みツールの例:
  #   Arduino (AVR/ESP32/RP2040): arduino-cli upload --fqbn <fqbn> -p COM4 .
  #   PlatformIO               : pio run -t upload
  #   STM32 (ST-LINK)          : STM32_Programmer_CLI -c port=SWD -w build/app.elf -v -rst
  #   J-Link                   : JLink.exe -device <MCU> -if SWD -speed 4000 -autoconnect 1 -CommanderScript tools/flash.jlink
  command: >
    arduino-cli upload
    --fqbn arduino:avr:uno
    -p COM4
    .
  timeout: 60

test:
  # command: 外部スクリプトの標準出力から TEST:<NAME>:<PASS|FAIL>[:detail] を解析
  # uart:    Harness が直接 UART を読む (pyserial 不要。ポート一覧: python harness.py ports)
  # manual:  人がテストし、結果を python harness.py result で入力
  mode: command
  command: >
    python tools/hil_test.py
    --port COM5
  timeout: 120
  # 成功判定に必須のテスト名 (空なら出力された全テストが PASS であること)
  required: []
  log_excerpt_lines: 40
  uart:
    port: COM5
    baudrate: 115200
    timeout: 30
    end_marker: "TEST:END"
    boot_marker: ""
    # ポートを開いた時の DTR/RTS (true = ON)。Arduino Uno 等は DTR ON で自動リセットされる
    dtr: true
    rts: true
    # 開いた直後のリセット: none / dtr (Arduino AVR 等) / rts (ESP32 等)
    reset: none
    # 開いた後に MCU へ送る文字列 (例: "RUN\\n")。空なら送らない
    send: ""
    # flash 直後の USB 再接続待ち (秒)
    open_retry: 5
    # auto (pyserial があれば使用) / builtin (標準ライブラリのみ) / pyserial
    backend: auto
""",
    static=_static(
        "- 製品/目的:\n- 対象 MCU / ボード:\n- 開発環境 (arduino-cli / PlatformIO / STM32CubeIDE など):\n",
        "- モジュール構成:\n- 割り込み / タスク構成:\n- 主要データフロー:\n",
        "- 動的メモリ確保禁止\n- ISR 内でのブロッキング処理禁止\n- public API の変更禁止\n",
        {"HARDWARE.md": "- MCU:\n- クロック:\n- ピンアサイン:\n- 通信 (UART / CAN / I2C / SPI) 設定:\n"},
    ),
    test_label="HARDWARE TEST",
    log_label="UART LOG",
)

# ---------------------------------------------------------------- python
PYTHON = Profile(
    name="python",
    title="Python のローカルツール",
    persona="あなたは Python で書かれたローカル業務ツールの開発支援Agentです。",
    extra_rules=[
        "存在しないライブラリ、関数、引数を\n推測して作らないでください。",
        "標準ライブラリで実現できる場合は\n新しい外部パッケージを追加しないでください。",
    ],
    project_yaml="""\
project:
  root: ..
  profile: python
  languages:
    - python
  # import 解決に使うディレクトリ
  include_dirs:
    - src
    - .
""",
    security_yaml="""\
  allowed_paths:
    - src
    - tests
    - test
    - "*.py"
""",
    pipeline_yaml="""\
# build / test は事前定義されたコマンドのみ実行する (shell は使わない)
build:
  # builtin:python-syntax = Harness 内蔵の構文チェック (全 .py をコンパイルのみ)
  command: builtin:python-syntax
  required: true
  timeout: 120

flash:
  # Python ツールでは使わない (空 = SKIPPED)
  command: ""

test:
  # exitcode: 終了コード 0 なら PASS。pytest / unittest の失敗テスト名をログから抽出
  # command : 出力の TEST:<NAME>:<PASS|FAIL> 行で判定
  # manual  : 人がテストし、結果を python harness.py result で入力
  mode: exitcode
  # pytest を使う場合: python -m pytest -q
  command: python -m unittest discover -v
  timeout: 300
  required: []
  log_excerpt_lines: 60
""",
    static=_static(
        "- ツールの目的:\n- 実行環境 (Windows / Python バージョン):\n- 利用パッケージ:\n- 入出力 (ファイル形式など):\n",
        "- モジュール構成:\n- 主要な処理の流れ:\n",
        "- PEP 8 に従う\n- 新しい外部パッケージは追加しない\n- 例外は握りつぶさない\n",
    ),
)

# ---------------------------------------------------------------- vba
VBA = Profile(
    name="vba",
    title="Excel VBA マクロ",
    persona="あなたは Excel VBA マクロの開発支援Agentです。",
    extra_rules=[
        "存在しない Excel のオブジェクト、メソッド、プロパティを\n推測して作らないでください。",
        "モジュール先頭の Attribute 行と VERSION / BEGIN ～ END ブロックは\n変更しないでください。",
        "Option Explicit を維持してください。",
    ],
    project_yaml="""\
project:
  root: ..
  profile: vba
  languages:
    - vba
  include_dirs: []
""",
    security_yaml="""\
  allowed_paths:
    - src/vba
""",
    extra_yaml="""\
# Excel VBA 連携 (PowerShell + Excel COM。追加ソフト不要)
# 前提: デスクトップ版 Excel (ライセンス版) と、トラストセンターの
#       「VBA プロジェクト オブジェクト モデルへのアクセスを信頼する」が ON であること
vba:
  # 対象ブック (project root からの相対パス)
  workbook: {workbook}
  # モジュールを書き出すフォルダ (Copilot はここの .bas / .cls / .frm を修正する)
  src: src/vba
  # テスト用マクロ (TEST:<NAME>:<PASS|FAIL> 行を返す Function)
  test_macro: HarnessTests.RunAll
  timeout: 180
""",
    patch_yaml="""\
  # VBA のモジュールファイルは Shift-JIS (CP932) + CRLF
  new_file_encoding: cp932
  new_file_eol: crlf
""",
    pipeline_yaml="""\
# build: src/vba のモジュールをブックへ取り込んで保存 (取り込み前にブックをバックアップ)
# test : テスト用マクロを実行して TEST 行を判定
# Excel の自動操作を使わない場合 (手動モード):
#   build.command: ""  /  build.required: false  /  test.mode: manual
build:
  command: builtin:vba-build
  required: true
  timeout: 180

flash:
  command: ""

test:
  # command: TEST 行で判定 / manual: 人が VBE でテストし python harness.py result で入力
  mode: command
  command: builtin:vba-test
  timeout: 180
  required: []
  log_excerpt_lines: 60
""",
    static=_static(
        "- ブック名と用途:\n- シート構成:\n- 利用者 / 実行タイミング:\n",
        "- モジュール構成 (標準モジュール / クラス / フォーム):\n- 主要な処理の流れ:\n",
        "- Option Explicit 必須\n- エラー処理は On Error GoTo で明示する\n- シート名・セル番地の直書きを増やさない\n",
    ),
)

# ---------------------------------------------------------------- tool
TOOL = Profile(
    name="tool",
    title="ローカル便利ツール (PowerShell / バッチ / JavaScript / C# など)",
    persona="あなたはローカルで動く業務ツール (スクリプト) の開発支援Agentです。",
    extra_rules=["存在しないコマンド、関数、引数を\n推測して作らないでください。"],
    project_yaml="""\
project:
  root: ..
  profile: tool
  languages:
    - powershell
    - batch
    - javascript
    - typescript
    - csharp
    - python
  include_dirs:
    - src
    - lib
""",
    security_yaml="""\
  allowed_paths:
    - src
    - lib
    - scripts
    - tools
    - tests
    - test
    - "*.ps1"
    - "*.psm1"
    - "*.bat"
    - "*.cmd"
    - "*.js"
    - "*.mjs"
    - "*.ts"
    - "*.cs"
    - "*.py"
""",
    pipeline_yaml="""\
# build / test は事前定義されたコマンドのみ実行する (shell は使わない)
build:
  # 例: dotnet build / npm run build。不要なら空のまま (SKIPPED)
  command: ""
  required: false
  timeout: 300

flash:
  command: ""

test:
  # exitcode: 終了コード 0 なら PASS / command: TEST 行で判定 / manual: 人がテストして result で入力
  mode: exitcode
  # 例: powershell -NoProfile -ExecutionPolicy Bypass -File tests/run_tests.ps1
  #     npm test / python -m pytest -q
  command: ""
  timeout: 300
  required: []
  log_excerpt_lines: 60
""",
    static=_static(
        "- ツールの目的:\n- 実行環境 (Windows / PowerShell / Node.js のバージョンなど):\n- 入出力:\n",
        "- ファイル構成:\n- 主要な処理の流れ:\n",
        "- 既存の書き方に合わせる\n- 管理者権限が必要な処理を追加しない\n",
    ),
)

PROFILES = {p.name: p for p in (MCU, PYTHON, VBA, TOOL)}

WORKBOOK_EXTS = (".xlsm", ".xlsb", ".xlam", ".xls")


def get(name: str | None) -> Profile:
    return PROFILES.get(str(name or "mcu").lower(), MCU)


def detect(root: Path) -> tuple[str, str]:
    """プロジェクト内のファイルから用途を推定する。(profile名, 理由)"""
    skip = {".git", ".copilot-harness", "node_modules", ".venv", "venv", "__pycache__", "build", ".pio"}
    counts: dict[str, int] = {}
    seen = 0
    for p in root.rglob("*"):
        if any(part in skip for part in p.relative_to(root).parts):
            continue
        if p.is_file():
            counts[p.suffix.lower()] = counts.get(p.suffix.lower(), 0) + 1
            seen += 1
            if seen > 5000:
                break

    def n(*exts: str) -> int:
        return sum(counts.get(e, 0) for e in exts)

    if n(*WORKBOOK_EXTS, ".bas", ".cls", ".frm"):
        return "vba", "Excel ブック / VBA モジュール (.xlsm / .bas / .cls) があります"
    if n(".ino", ".c", ".cpp", ".h", ".hpp") and (n(".ino") or (root / "platformio.ini").exists() or n(".c", ".cpp") >= n(".py")):
        return "mcu", "C/C++ / Arduino のソースがあります"
    if n(".py") and n(".py") >= n(".ps1", ".bat", ".cmd", ".js", ".ts", ".cs"):
        return "python", "Python のソースがあります"
    if n(".ps1", ".psm1", ".bat", ".cmd", ".js", ".mjs", ".ts", ".cs"):
        return "tool", "PowerShell / バッチ / JavaScript などのスクリプトがあります"
    return "tool", "判定できる手掛かりが無いため汎用 (tool) にしました"


def find_workbook(root: Path) -> str:
    books = sorted(p for p in root.iterdir() if p.is_file() and p.suffix.lower() in WORKBOOK_EXTS) if root.exists() else []
    return books[0].name if books else "Book1.xlsm"
