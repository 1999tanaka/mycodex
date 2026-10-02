"""共通ユーティリティ。"""

from __future__ import annotations

import fnmatch
import hashlib
import re
from datetime import datetime
from pathlib import Path


class HarnessError(Exception):
    """利用者に表示して処理を中断するエラー。"""


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def read_text(path: Path) -> str:
    """UTF-8 (BOM有無) → CP932 の順で読み込む。改行は保持する。"""
    data = path.read_bytes()
    for enc in ("utf-8-sig", "cp932"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def rel_posix(path: Path, root: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()


def _norm(p: str) -> str:
    return p.replace("\\", "/").strip().strip("/").lower()


def _has_glob(p: str) -> bool:
    return any(c in p for c in "*?[")


def allow_match(rel: str, pattern: str) -> bool:
    """許可パス判定 (ルート基準で固定)。

    - ``src`` / ``src/**`` : src 配下すべて
    - ``*.ino``            : glob (``*`` は ``/`` も跨ぐ)
    - ``main.ino``         : 完全一致
    """
    r, p = _norm(rel), _norm(pattern)
    if not p:
        return False
    if p.endswith("/**"):
        base = p[:-3]
        if _has_glob(base):
            return fnmatch.fnmatchcase(r, p) or fnmatch.fnmatchcase(r, base)
        return r == base or r.startswith(base + "/")
    if _has_glob(p):
        return fnmatch.fnmatchcase(r, p)
    return r == p or r.startswith(p + "/")


def deny_match(rel: str, pattern: str) -> bool:
    """禁止/除外パス判定。

    ``/`` を含まないパターンはパスのどの要素にも適用する
    (例: ``.env`` は ``src/.env`` にも一致、``*secret*`` は ``secret/x.c`` にも一致)。
    """
    r, p = _norm(rel), _norm(pattern)
    if not p:
        return False
    if "/" in p:
        return allow_match(r, p)
    parts = r.split("/")
    if _has_glob(p):
        return any(fnmatch.fnmatchcase(part, p) for part in parts)
    return p in parts


def first_match(rel: str, patterns: list[str], matcher) -> str | None:
    for pat in patterns:
        if matcher(rel, pat):
            return pat
    return None


_WS = re.compile(r"\s+")


def one_line(text: str, limit: int = 160) -> str:
    s = _WS.sub(" ", text or "").strip()
    return s if len(s) <= limit else s[: limit - 1] + "…"


def dedupe(items):
    seen = set()
    out = []
    for it in items:
        if it not in seen:
            seen.add(it)
            out.append(it)
    return out
