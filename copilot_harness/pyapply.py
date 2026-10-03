"""git を使わない patch 適用エンジン (git apply 相当)。

- 検証済みの ParsedPatch (patch.py) をそのまま適用する
- コンテキスト行は完全一致 (git apply と同じく fuzz なし)。位置ずれ (offset) は
  記載行に最も近い一致箇所を採用する
- ファイルの改行コード (LF/CRLF)、文字コード (UTF-8 / UTF-8 BOM / CP932) を保持する
- 全ファイルの適用結果をメモリ上で作ってから書き込む (--check 相当は書き込まない)
"""

from __future__ import annotations

import codecs
import re
from dataclasses import dataclass, field
from pathlib import Path

from .patch import FilePatch, Hunk, ParsedPatch


@dataclass
class ApplyResult:
    ok: bool = True
    messages: list[str] = field(default_factory=list)
    outputs: dict[str, bytes | None] = field(default_factory=dict)  # None = 削除


def decode(data: bytes) -> tuple[str, str]:
    """(テキスト, 書き戻し用 encoding)。"""
    if data.startswith(codecs.BOM_UTF8):
        return data[3:].decode("utf-8"), "utf-8-sig"
    for enc in ("utf-8", "cp932"):
        try:
            return data.decode(enc), enc
        except UnicodeDecodeError:
            continue
    return data.decode("latin-1"), "latin-1"  # 任意のバイト列を往復可能


_LINE_RE = re.compile(r"[^\n]*\n|[^\n]+$")


def split_lines(text: str) -> list[str]:
    """改行コードを保持したまま \\n 単位で分割 (\\r\\n は行末に残る)。"""
    return _LINE_RE.findall(text)


def _body(line: str) -> str:
    if line.endswith("\n"):
        line = line[:-1]
    if line.endswith("\r"):
        line = line[:-1]
    return line


def _entries(h: Hunk) -> list[list]:
    """[tag, text, no_newline] の列。``\\ No newline`` は直前の行へ付ける。"""
    out: list[list] = []
    for ln in h.lines:
        if ln.startswith("\\"):
            if out:
                out[-1][2] = True
            continue
        out.append([ln[0], ln[1:], False])
    return out


def _find(bodies: list[str], old: list[str], expected: int, lo: int) -> int | None:
    """old と一致する位置のうち expected に最も近いもの (lo 以降)。"""
    n = len(old)
    hi = len(bodies) - n
    if n == 0:
        return min(max(expected, lo), len(bodies))
    if hi < lo:
        return None
    expected = min(max(expected, lo), hi)
    for d in range(0, max(expected - lo, hi - expected) + 1):
        for p in (expected - d, expected + d) if d else (expected,):
            if lo <= p <= hi and bodies[p:p + n] == old:
                return p
    return None


def _apply_file(fp: FilePatch, lines: list[str], eol: str) -> tuple[list[str] | None, str]:
    bodies = [_body(x) for x in lines]
    out: list[str] = []

    def push(line: str) -> None:
        if out and not out[-1].endswith("\n"):
            out[-1] += eol  # 末尾改行の無い行の後ろに行を足す場合
        out.append(line)

    cursor = 0
    for no, h in enumerate(sorted(fp.hunks, key=lambda x: x.old_start or 0), 1):
        ents = _entries(h)
        old = [t for tag, t, _ in ents if tag in (" ", "-")]
        start = h.old_start or 0
        expected = start - 1 if old else start
        pos = _find(bodies, old, expected, cursor)
        if pos is None:
            return None, f"{fp.path}: hunk #{no} (行 {start} 付近) のコンテキストが現在のファイルと一致しません"
        for ln in lines[cursor:pos]:
            push(ln)
        j = pos
        for tag, text, nonl in ents:
            if tag == " ":
                push(lines[j])
                j += 1
            elif tag == "-":
                j += 1
            else:
                push(text + ("" if nonl else eol))
        cursor = j
    for ln in lines[cursor:]:
        push(ln)
    return out, ""


def check(pp: ParsedPatch, root: Path, new_encoding: str = "utf-8", new_eol: str = "lf") -> ApplyResult:
    """git apply --check 相当。適用後の内容を outputs に保持する (書き込みはしない)。

    new_encoding / new_eol は新規作成ファイルに使う (VBA モジュールは cp932 + crlf)。
    """
    res = ApplyResult()
    nl = "\r\n" if str(new_eol).lower() == "crlf" else "\n"
    for fp in pp.files:
        target = root / fp.path
        if fp.is_new:
            if target.exists():
                res.messages.append(f"{fp.path}: 既に存在します")
                continue
            ents = [e for h in fp.hunks for e in _entries(h)]
            if any(tag != "+" for tag, _, _ in ents):
                res.messages.append(f"{fp.path}: 新規ファイルの hunk に + 以外の行があります")
                continue
            text = "".join(t + ("" if nonl else nl) for _, t, nonl in ents)
            try:
                res.outputs[fp.path] = text.encode(new_encoding)
            except (UnicodeEncodeError, LookupError) as e:
                res.messages.append(f"{fp.path}: 新規ファイルを {new_encoding} で保存できません ({e})")
            continue
        try:
            text, enc = decode(target.read_bytes())
        except OSError as e:
            res.messages.append(f"{fp.path}: 読み込めません ({e})")
            continue
        lines = split_lines(text)
        if fp.is_delete:
            old = [t for h in fp.hunks for tag, t, _ in _entries(h) if tag in (" ", "-")]
            if [_body(x) for x in lines] != old:
                res.messages.append(f"{fp.path}: 削除 patch の内容が現在のファイルと一致しません")
            else:
                res.outputs[fp.path] = None
            continue
        eol = "\r\n" if any(x.endswith("\r\n") for x in lines[:200]) else "\n"
        new_lines, err = _apply_file(fp, lines, eol)
        if new_lines is None:
            res.messages.append(err)
            continue
        try:
            res.outputs[fp.path] = "".join(new_lines).encode(enc)
        except UnicodeEncodeError as e:
            res.messages.append(f"{fp.path}: 追加行に {enc} で表現できない文字があります ({e.object[e.start:e.end]!r})")
    res.ok = not res.messages
    if res.ok:
        res.messages = [f"{fp.path}: OK" for fp in pp.files]
    return res


def write(res: ApplyResult, root: Path) -> None:
    for rel, data in res.outputs.items():
        target = root / rel
        if data is None:
            if target.exists():
                target.unlink()
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
