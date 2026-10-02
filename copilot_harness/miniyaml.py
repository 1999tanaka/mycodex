"""PyYAML が無い環境用の YAML サブセットパーサ (標準ライブラリのみ)。

config.yaml で使う範囲をサポートする:
  - ブロック形式の mapping / sequence (``- item``, ``- key: value``)
  - flow 形式の ``[a, b]`` / ``{}`` / ``{k: v}``
  - plain / 'single' / "double" quoted scalar、行末コメント
  - block scalar ``>`` ``|`` (chomping ``-`` ``+``)
  - plain scalar の複数行継続
  - 型: null, bool (true/false/yes/no/on/off), int, float — PyYAML (YAML 1.1) と同じ解釈

anchor / alias / tag / 複数ドキュメントは未サポート (エラーにする)。
"""

from __future__ import annotations

import re
from typing import Any


class MiniYAMLError(ValueError):
    pass


_NULL = {"", "~", "null", "Null", "NULL"}
_TRUE = {"true", "True", "TRUE", "yes", "Yes", "YES", "on", "On", "ON"}
_FALSE = {"false", "False", "FALSE", "no", "No", "NO", "off", "Off", "OFF"}
_INT_RE = re.compile(r"^[-+]?(0|[1-9][0-9_]*)$")
_OCT_RE = re.compile(r"^[-+]?0[0-7_]+$")
_HEX_RE = re.compile(r"^[-+]?0x[0-9a-fA-F_]+$")
_FLOAT_RE = re.compile(r"^[-+]?([0-9][0-9_]*)?\.[0-9_]*([eE][-+][0-9]+)?$")
_INF_RE = re.compile(r"^[-+]?\.(inf|Inf|INF)$")
_NAN_RE = re.compile(r"^\.(nan|NaN|NAN)$")


def resolve_plain(s: str) -> Any:
    if s in _NULL:
        return None
    if s in _TRUE:
        return True
    if s in _FALSE:
        return False
    if _INT_RE.match(s):
        return int(s.replace("_", ""))
    if _HEX_RE.match(s):
        return int(s.replace("_", ""), 16)
    if _OCT_RE.match(s):
        return int(s.replace("_", ""), 8)
    if _FLOAT_RE.match(s) and any(c.isdigit() for c in s):
        return float(s.replace("_", ""))
    if _INF_RE.match(s):
        return float("-inf") if s.startswith("-") else float("inf")
    if _NAN_RE.match(s):
        return float("nan")
    return s


def _indent_of(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def _is_blank(line: str) -> bool:
    s = line.strip()
    return not s or s.startswith("#")


def _strip_comment(s: str) -> str:
    """引用符の外にある `` #`` 以降を除去する。"""
    quote = None
    for i, c in enumerate(s):
        if quote:
            if c == quote:
                quote = None
        elif c in "'\"" and (i == 0 or s[i - 1] in " \t[{,:"):
            quote = c
        elif c == "#" and (i == 0 or s[i - 1] in " \t"):
            return s[:i].rstrip()
    return s.rstrip()


def _split_key(s: str) -> tuple[str, str] | None:
    """``key: value`` を分割する。mapping 行でなければ None。"""
    if s[:1] in "'\"":
        q = s[0]
        end = s.find(q, 1)
        while q == "'" and end != -1 and s[end + 1:end + 2] == "'":
            end = s.find(q, end + 2)
        if end == -1:
            return None
        rest = s[end + 1:].lstrip()
        if rest == ":" or rest.startswith(": "):
            return _quoted(s[:end + 1], 0)[0], rest[1:].strip()
        return None
    m = re.match(r"^([^\[\]{}#,][^#]*?)\s*:(\s+|$)(.*)$", s)
    if not m or m.group(1).startswith(("- ", "? ")):
        return None
    return m.group(1).strip(), m.group(3).strip()


def _quoted(s: str, lineno: int) -> tuple[str, str]:
    """先頭の引用符付き文字列を解析し (値, 残り) を返す。"""
    q = s[0]
    out: list[str] = []
    i = 1
    while i < len(s):
        c = s[i]
        if q == "'":
            if c == "'":
                if s[i + 1:i + 2] == "'":
                    out.append("'")
                    i += 2
                    continue
                return "".join(out), s[i + 1:]
            out.append(c)
        else:
            if c == "\\" and i + 1 < len(s):
                esc = s[i + 1]
                table = {"n": "\n", "t": "\t", "r": "\r", "0": "\0", "\\": "\\", '"': '"', "/": "/", " ": " ", "e": "\x1b"}
                if esc in table:
                    out.append(table[esc])
                    i += 2
                    continue
                if esc in "xuU":
                    n = {"x": 2, "u": 4, "U": 8}[esc]
                    out.append(chr(int(s[i + 2:i + 2 + n], 16)))
                    i += 2 + n
                    continue
                raise MiniYAMLError(f"line {lineno}: 未対応のエスケープ \\{esc}")
            if c == '"':
                return "".join(out), s[i + 1:]
            out.append(c)
        i += 1
    raise MiniYAMLError(f"line {lineno}: 引用符が閉じていません")


def _split_flow(body: str, lineno: int) -> list[str]:
    items, depth, quote, cur = [], 0, None, []
    for c in body:
        if quote:
            cur.append(c)
            if c == quote:
                quote = None
            continue
        if c in "'\"":
            quote = c
        elif c in "[{":
            depth += 1
        elif c in "]}":
            depth -= 1
        elif c == "," and depth == 0:
            items.append("".join(cur).strip())
            cur = []
            continue
        cur.append(c)
    if quote or depth:
        raise MiniYAMLError(f"line {lineno}: flow 形式が閉じていません")
    last = "".join(cur).strip()
    if last:
        items.append(last)
    return items


def parse_scalar(raw: str, lineno: int = 0) -> Any:
    s = raw.strip()
    if not s:
        return None
    if s[0] in "&*!%@`":
        raise MiniYAMLError(f"line {lineno}: anchor / alias / tag 等は未対応です: {s} (PyYAML を入れると使えます)")
    if s[0] in "'\"":
        value, rest = _quoted(s, lineno)
        rest = rest.strip()
        if rest and not rest.startswith("#"):
            raise MiniYAMLError(f"line {lineno}: 引用符の後に余分な文字があります: {s}")
        return value
    if s[0] == "[":
        s = _strip_comment(s)
        if not s.endswith("]"):
            raise MiniYAMLError(f"line {lineno}: ']' がありません: {s}")
        return [parse_scalar(x, lineno) for x in _split_flow(s[1:-1], lineno)]
    if s[0] == "{":
        s = _strip_comment(s)
        if not s.endswith("}"):
            raise MiniYAMLError(f"line {lineno}: '}}' がありません: {s}")
        out = {}
        for item in _split_flow(s[1:-1], lineno):
            kv = _split_key(item)
            if kv is None:
                raise MiniYAMLError(f"line {lineno}: flow mapping の要素が不正です: {item}")
            out[kv[0]] = parse_scalar(kv[1], lineno)
        return out
    return resolve_plain(_strip_comment(s))


class _Parser:
    def __init__(self, text: str):
        text = text.lstrip("﻿").replace("\r\n", "\n").replace("\r", "\n")
        self.lines = text.split("\n")
        for no, ln in enumerate(self.lines, 1):
            if ln.strip() and "\t" in ln[:len(ln) - len(ln.lstrip(" \t"))]:
                raise MiniYAMLError(f"line {no}: インデントにタブは使えません")
        self.i = 0

    # -------------------------------------------------- 行操作
    def eof(self) -> bool:
        return self.i >= len(self.lines)

    def skip_blank(self) -> None:
        while not self.eof() and _is_blank(self.lines[self.i]):
            self.i += 1

    def cur(self) -> str:
        return self.lines[self.i]

    # -------------------------------------------------- 文書
    def document(self) -> Any:
        self.skip_blank()
        if not self.eof() and self.cur().strip() in ("---",):
            self.i += 1
            self.skip_blank()
        if self.eof():
            return None
        value = self.block(_indent_of(self.cur()))
        self.skip_blank()
        if not self.eof():
            ln = self.cur().strip()
            if ln in ("---", "..."):
                raise MiniYAMLError(f"line {self.i + 1}: 複数ドキュメントは未対応です")
            raise MiniYAMLError(f"line {self.i + 1}: インデントが不正です: {ln}")
        return value

    def block(self, indent: int) -> Any:
        content = self.cur().strip()
        if content == "-" or content.startswith("- "):
            return self.sequence(indent)
        if _split_key(_strip_comment(content)) is not None:
            return self.mapping(indent)
        # ブロック位置に単独の scalar
        lineno = self.i + 1
        self.i += 1
        return self.plain_continuation(content, indent - 1, lineno)

    def mapping(self, indent: int) -> dict:
        out: dict = {}
        while True:
            self.skip_blank()
            if self.eof():
                return out
            ind = _indent_of(self.cur())
            if ind < indent:
                return out
            lineno = self.i + 1
            content = self.cur().strip()
            if ind > indent:
                raise MiniYAMLError(f"line {lineno}: インデントが不正です: {content}")
            if content == "-" or content.startswith("- "):
                return out
            if content in ("---", "..."):
                raise MiniYAMLError(f"line {lineno}: 複数ドキュメントは未対応です")
            kv = _split_key(_strip_comment(content) if not content.startswith(("'", '"')) else content)
            if kv is None:
                raise MiniYAMLError(f"line {lineno}: 'key: value' 形式ではありません: {content}")
            key, rest = kv
            if key in out:
                pass  # PyYAML 同様、後勝ち
            self.i += 1
            out[key] = self.value_after_key(rest, ind, lineno)

    def value_after_key(self, rest: str, ind: int, lineno: int) -> Any:
        if rest[:1] in (">", "|") and re.match(r"^[>|][-+0-9]*\s*(#.*)?$", rest):
            return self.block_scalar(rest, ind)
        if rest and not rest.startswith("#"):
            if rest[0] in "'\"[{":
                return parse_scalar(rest, lineno)
            return self.plain_continuation(rest, ind, lineno)
        self.skip_blank()
        if self.eof():
            return None
        nind = _indent_of(self.cur())
        ncontent = self.cur().strip()
        if nind > ind:
            return self.block(nind)
        if nind == ind and (ncontent == "-" or ncontent.startswith("- ")):
            return self.sequence(ind)  # key と同じ深さの sequence
        return None

    def plain_continuation(self, first: str, ind: int, lineno: int) -> Any:
        """plain scalar の複数行継続 (空白 1 個で連結)。"""
        if first[:1] in "&*!%@`":
            return parse_scalar(first, lineno)
        parts = [_strip_comment(first)]
        if " #" in first or first.startswith("#"):
            return resolve_plain(parts[0])
        while not self.eof():
            ln = self.cur()
            if _is_blank(ln) or _indent_of(ln) <= ind:
                break
            s = ln.strip()
            if s.startswith("- ") or _split_key(_strip_comment(s)) is not None:
                raise MiniYAMLError(f"line {self.i + 1}: インデントが不正です: {s}")
            parts.append(_strip_comment(s))
            self.i += 1
        return resolve_plain(" ".join(p for p in parts if p))

    def sequence(self, indent: int) -> list:
        out: list = []
        while True:
            self.skip_blank()
            if self.eof():
                return out
            line = self.cur()
            ind = _indent_of(line)
            content = line.strip()
            if ind < indent or not (content == "-" or content.startswith("- ")):
                if ind > indent:
                    raise MiniYAMLError(f"line {self.i + 1}: インデントが不正です: {content}")
                return out
            if ind > indent:
                raise MiniYAMLError(f"line {self.i + 1}: インデントが不正です: {content}")
            lineno = self.i + 1
            rest = content[1:].lstrip()
            col = ind + (len(content) - len(rest))
            if not rest or rest.startswith("#"):
                self.i += 1
                self.skip_blank()
                if not self.eof() and _indent_of(self.cur()) > ind:
                    out.append(self.block(_indent_of(self.cur())))
                else:
                    out.append(None)
            elif rest.startswith("- ") or rest == "-" or (_split_key(_strip_comment(rest)) is not None and rest[0] not in "[{"):
                # "- key: value" / "- - x" → 同じ行を item の位置から解析し直す
                self.lines[self.i] = " " * col + rest
                out.append(self.block(col))
            elif rest[:1] in (">", "|") and re.match(r"^[>|][-+0-9]*\s*(#.*)?$", rest):
                self.i += 1
                out.append(self.block_scalar(rest, ind))
            else:
                self.i += 1
                if rest[0] in "'\"[{":
                    out.append(parse_scalar(rest, lineno))
                else:
                    out.append(self.plain_continuation(rest, ind, lineno))

    def block_scalar(self, header: str, parent_indent: int) -> str:
        m = re.match(r"^([>|])([-+]?)([0-9]?)([-+]?)", header)
        style, chomp = m.group(1), (m.group(2) or m.group(4))
        explicit = int(m.group(3)) if m.group(3) else 0
        raw: list[str] = []
        block_indent = parent_indent + explicit if explicit else None
        while not self.eof():
            ln = self.cur()
            if not ln.strip():
                raw.append("")
                self.i += 1
                continue
            ind = _indent_of(ln)
            if ind <= parent_indent:
                break
            if block_indent is None:
                block_indent = ind
            if ind < block_indent:
                break
            raw.append(ln[block_indent:])
            self.i += 1
        # 末尾の空行は chomping で扱う
        trailing = 0
        while raw and raw[-1] == "":
            raw.pop()
            trailing += 1
        if style == "|":
            body = "\n".join(raw)
        else:
            body = ""
            prev_more = False
            for idx, ln in enumerate(raw):
                more = ln.startswith((" ", "\t"))
                if idx == 0:
                    body = ln
                elif ln == "":
                    body += "\n"
                elif raw[idx - 1] == "":
                    body += ln
                elif more or prev_more:
                    body += "\n" + ln
                else:
                    body += " " + ln
                prev_more = more
        if not raw:
            return ""
        if chomp == "-":
            return body
        if chomp == "+":
            return body + "\n" + "\n" * trailing
        return body + "\n"


def load(text: str) -> Any:
    return _Parser(text).document()
