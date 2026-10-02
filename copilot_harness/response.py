"""Copilot 応答 (inbox/copilot_response.txt) の解析。

Copilot の出力はすべて Untrusted Input として扱う。ここでは構文解析のみを行い、
パス・diff の安全性検証は patch.py / workflow 側で行う。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

PATCH = "PATCH"
NEED_CONTEXT = "NEED_CONTEXT"

SECTION_KEYS = (
    "ACTION", "RUN_ID", "FILES", "REASON", "SUMMARY", "FINDING", "SUSPECTS",
    "NEXT_OBJECTIVE", "KEYWORDS",
)
HEADER_RE = re.compile(r"^(" + "|".join(SECTION_KEYS) + r")\s*[:：]\s*(.*)$", re.IGNORECASE)
LIST_ITEM_RE = re.compile(r"^\s*(?:[-*+•]|\d+[.)])\s+(.*\S)\s*$")
RUN_ID_RE = re.compile(r"\b(\d{8}-\d{4})\b")
PATCH_BLOCK_RE = re.compile(r"^[ \t>]*BEGIN_PATCH[ \t]*$\n?(.*?)^[ \t>]*END_PATCH[ \t]*$", re.MULTILINE | re.DOTALL)
FENCE_RE = re.compile(r"^\s*(```|~~~)")


@dataclass
class CopilotResponse:
    action: str | None = None
    run_id: str | None = None
    files: list[str] = field(default_factory=list)
    patch: str = ""
    patch_blocks: int = 0
    reason: str = ""
    summary: str = ""
    finding: str = ""
    suspects: list[str] = field(default_factory=list)
    next_objective: str = ""
    keywords: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


def normalize(text: str) -> str:
    text = text.lstrip("﻿").replace("\r\n", "\n").replace("\r", "\n")
    # Web UI からのコピーで混入しやすい不可視文字
    for ch in ("​", "‌", "‍", "⁠", "﻿"):
        text = text.replace(ch, "")
    return text.replace(" ", " ")


def _strip_fences(block: str) -> str:
    lines = block.split("\n")
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    if lines and FENCE_RE.match(lines[0]):
        lines.pop(0)
    if lines and FENCE_RE.match(lines[-1]):
        lines.pop()
    return "\n".join(lines) + "\n" if lines else ""


def _clean_header_line(line: str) -> str:
    # Markdown 装飾 (**ACTION:**, `RUN_ID`, __X__, # 見出し, > 引用) を除去。語中の _ は残す
    s = re.sub(r"[*`]", "", line).strip()
    s = re.sub(r"(?<![A-Za-z0-9])_+|_+(?![A-Za-z0-9])", "", s)
    return s.lstrip("#> ").strip()


def _list_items(lines: list[str]) -> list[str]:
    items: list[str] = []
    for ln in lines:
        if not ln.strip() or FENCE_RE.match(ln):
            continue
        m = LIST_ITEM_RE.match(ln)
        items.append((m.group(1) if m else ln).strip())
    return items


def _path_item(item: str) -> str:
    s = item.strip().strip("`'\"")
    for sep in (" — ", " – ", " - ", " (", "（", "：", ": "):
        if sep in s:
            s = s.split(sep, 1)[0]
    s = s.strip().strip("`'\"")
    return s.split()[0] if s.split() else ""


_PATHISH_RE = re.compile(r"^[\w.\-+/\\:]+$")


def _pathish(p: str) -> bool:
    return bool(p) and bool(_PATHISH_RE.match(p)) and ("." in p or "/" in p)


def parse_response(raw: str) -> CopilotResponse:
    res = CopilotResponse()
    text = normalize(raw)
    if not text.strip():
        res.errors.append("応答が空です。")
        return res

    # 1. BEGIN_PATCH 〜 END_PATCH のみを patch として抽出
    blocks = [_strip_fences(m.group(1)) for m in PATCH_BLOCK_RE.finditer(text)]
    res.patch_blocks = len(blocks)
    res.patch = "".join(b for b in blocks if b.strip())
    rest = PATCH_BLOCK_RE.sub("\n", text)
    if re.search(r"^[ \t>]*BEGIN_PATCH", rest, re.MULTILINE):
        res.errors.append("BEGIN_PATCH に対応する END_PATCH がありません (回答が途中で切れている可能性)。")

    # 2. セクション解析
    sections: dict[str, list[str]] = {}
    actions: list[str] = []
    current: str | None = None
    for line in rest.split("\n"):
        m = HEADER_RE.match(_clean_header_line(line))
        if m:
            current = m.group(1).upper()
            value = m.group(2).strip()
            if current == "ACTION":
                actions.append(value)
            sections.setdefault(current, [])
            if value:
                sections[current].append(value)
            continue
        if current:
            sections[current].append(line)

    # ACTION
    norm_actions = []
    for a in actions:
        word = re.sub(r"[^A-Z_ ]", "", a.upper()).strip().replace(" ", "_")
        if word.startswith("NEED_CONTEXT") or word.startswith("NEEDCONTEXT"):
            norm_actions.append(NEED_CONTEXT)
        elif word.startswith("PATCH"):
            norm_actions.append(PATCH)
        elif word:
            res.errors.append(f"不明な ACTION: {a}")
    uniq = list(dict.fromkeys(norm_actions))
    if len(uniq) > 1:
        res.errors.append(f"ACTION が複数あります: {', '.join(uniq)}")
    elif uniq:
        res.action = uniq[0]
    elif not res.errors:
        res.errors.append("ACTION 行が見つかりません (ACTION: PATCH / ACTION: NEED_CONTEXT)。")

    # RUN_ID
    rid = " ".join(sections.get("RUN_ID", []))
    m = RUN_ID_RE.search(rid)
    res.run_id = m.group(1) if m else None

    res.files = [p for p in (_path_item(i) for i in _list_items(sections.get("FILES", []))) if _pathish(p)]
    res.suspects = _list_items(sections.get("SUSPECTS", []))
    kw: list[str] = []
    for item in _list_items(sections.get("KEYWORDS", [])):
        kw += [k.strip().strip("`") for k in re.split(r"[,、]", item) if k.strip()]
    res.keywords = kw
    for key, attr in (("REASON", "reason"), ("SUMMARY", "summary"), ("FINDING", "finding"), ("NEXT_OBJECTIVE", "next_objective")):
        setattr(res, attr, "\n".join(sections.get(key, [])).strip())

    # 3. ACTION 別の整合性
    if res.action == PATCH:
        if not res.patch_blocks:
            res.errors.append("ACTION: PATCH ですが BEGIN_PATCH / END_PATCH ブロックがありません。")
        elif not res.patch.strip():
            res.errors.append("PATCH ブロックが空です。")
    elif res.action == NEED_CONTEXT:
        if res.patch_blocks:
            res.warnings.append("NEED_CONTEXT 応答に PATCH ブロックが含まれていますが無視します。")
            res.patch = ""
        if not res.files and not res.keywords:
            res.errors.append("NEED_CONTEXT ですが FILES が空です。")
    return res
