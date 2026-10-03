"""init で生成するファイルのテンプレート (プロファイル別)。"""

from __future__ import annotations

import json

from . import profiles
from .profiles import TEMPLATE_MARKER  # noqa: F401  (互換のため再公開)

_HEADER = """\
# Local Coding Harness 設定 (v0.2)
# プロファイル: {title}
# パスはこのファイル (.copilot-harness/) からの相対、または project root からの相対。

"""

_SECURITY_HEAD = """\
security:
  # Copilot の patch が変更してよいパス
"""

_SECURITY_TAIL = """\
  # Copilot から変更不可 (Context にも含めない)
  forbidden_paths:
    - ".git/**"
    - ".copilot-harness/**"
    - ".env"
    - "*.key"
    - "*.pem"
    - "credential/**"
    - "secret/**"
    # バイナリ (Excel ブック / フォームの .frx) は patch しない
    - "*.xlsm"
    - "*.xlsb"
    - "*.xlsx"
    - "*.xlam"
    - "*.xls"
    - "*.frx"
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
  # auto: git リポジトリなら git apply、それ以外は内蔵エンジン (標準ライブラリのみ)。
  #       UTF-8 以外 (Shift-JIS など) のファイルを変更する patch は内蔵エンジンで適用する
  engine: auto
"""

_HANDOFF = """
handoff:
  # true: 静的情報は Copilot Notebook に置く / false: handoff に埋め込む
  use_notebook: false
  history_entries: 10
  # Copilot の説明文 (REASON / SUMMARY / FINDING / SUSPECTS) の言語。空にすると指定しない
  response_language: 日本語
"""

_RULES_COMMON = """\
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
"""


def config_yaml(profile: str = "mcu", workbook: str = "Book1.xlsm") -> str:
    p = profiles.get(profile)
    parts = [
        _HEADER.format(title=p.title),
        p.project_yaml,
        "\n",
        _SECURITY_HEAD,
        p.security_yaml,
        _SECURITY_TAIL,
        p.patch_yaml,
        "\n",
        p.pipeline_yaml,
    ]
    if p.extra_yaml:
        parts += ["\n", p.extra_yaml.replace("{workbook}", json.dumps(workbook, ensure_ascii=False))]
    parts.append(_HANDOFF)
    return "".join(parts)


def agent_rules(profile: str = "mcu") -> str:
    p = profiles.get(profile)
    extra = "\n".join(f"{r}\n" for r in p.extra_rules)
    return f"# AGENT RULES\n\n{p.persona}\n\n{_RULES_COMMON}\n{extra}"


def static_files(profile: str = "mcu") -> dict[str, str]:
    files = dict(profiles.get(profile).static)
    files["AGENT_RULES.md"] = agent_rules(profile)
    return files


# 互換: 既定 (mcu) プロファイルのテンプレート
CONFIG_YAML = config_yaml("mcu")
STATIC_FILES = static_files("mcu")

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
