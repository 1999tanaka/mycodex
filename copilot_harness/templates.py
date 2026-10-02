"""init で生成するファイルのテンプレート。"""

CONFIG_YAML = """\
# Local Coding Harness 設定 (v0.2)
# パスはこのファイル (.copilot-harness/) からの相対、または project root からの相対。

project:
  root: ..
  languages:
    - cpp
    - ino
  # include 解決に使うディレクトリ
  include_dirs:
    - include
    - src

security:
  # Copilot の patch が変更してよいパス
  allowed_paths:
    - src
    - include
    - test
    - "*.ino"
  # Copilot から変更不可 (Context にも含めない)
  forbidden_paths:
    - ".git/**"
    - ".copilot-harness/**"
    - ".env"
    - "*.key"
    - "*.pem"
    - "credential/**"
    - "secret/**"
  # Context Builder から必ず除外 (patch でも変更不可)
  excluded_patterns:
    - ".env"
    - "*.key"
    - "*.pem"
    - "password*"
    - "credential*"
    - "*secret*"
    - "private*"

limits:
  max_changed_files: 5
  max_added_lines: 500
  max_deleted_lines: 500
  max_iterations: 5

context:
  # 常に関連キーワードとして扱う語 (start --keywords でタスク毎にも指定可)
  keywords: []
  # 常に SOURCE_CONTEXT.md に含めるファイル
  pinned_files: []
  max_files: 12
  max_file_chars: 40000
  max_total_chars: 150000
  include_repo_map: true

patch:
  # auto: git があれば git apply、無ければ内蔵エンジン (標準ライブラリのみ) / git / python
  engine: auto

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

handoff:
  # true: 静的情報は Copilot Notebook に置く / false: handoff に埋め込む
  use_notebook: false
  history_entries: 10
"""

TEMPLATE_MARKER = "<!-- harness:template -->"

STATIC_FILES = {
    "PROJECT.md": f"""{TEMPLATE_MARKER}
# PROJECT

(記入したら 1 行目の harness:template 行を削除してください。削除するまで handoff には含まれません)

- 製品/目的:
- 対象 MCU / ボード:
- 開発環境 (arduino-cli / PlatformIO / STM32CubeIDE など):
""",
    "ARCHITECTURE.md": f"""{TEMPLATE_MARKER}
# ARCHITECTURE

- モジュール構成:
- 割り込み / タスク構成:
- 主要データフロー:
""",
    "HARDWARE.md": f"""{TEMPLATE_MARKER}
# HARDWARE

- MCU:
- クロック:
- ピンアサイン:
- 通信 (UART / CAN / I2C / SPI) 設定:
""",
    "CODING_RULES.md": f"""{TEMPLATE_MARKER}
# CODING RULES

- 動的メモリ確保禁止
- ISR 内でのブロッキング処理禁止
- public API の変更禁止
""",
    "AGENT_RULES.md": """# AGENT RULES

あなたは組み込みソフトウェア開発支援Agentです。

STATE.mdを現在状態の唯一の正として扱ってください。

VERIFIEDに記載された内容を
再度確認するよう提案しないでください。

SOURCE_CONTEXT.md内の

# FILE: path

を実ファイル名として扱ってください。

コード変更は最小変更を優先してください。

不足情報を推測しないでください。

不足情報がある場合は
ACTION: NEED_CONTEXT
で必要ファイルを要求してください。

修正可能な場合は
ACTION: PATCH
でunified diffを出力してください。

存在しないAPI、関数、レジスタを
推測して作らないでください。
""",
}

HARNESS_GITIGNORE = """\
# Harness の作業ファイル (commit 不要)
state.json
baseline.json
REPO_MAP.md
handoff/
inbox/
logs/
history/
backup/
__pycache__/
"""
