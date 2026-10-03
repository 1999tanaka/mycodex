"""クリップボードのテキスト読み取り (標準ライブラリのみ)。

- Windows : ctypes で Win32 API (OpenClipboard / GetClipboardData(CF_UNICODETEXT))
- macOS   : pbpaste
- Linux   : wl-paste / xclip / xsel のいずれか
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import time


class ClipboardError(Exception):
    pass


def read_clipboard() -> str:
    if sys.platform == "win32":
        return _read_windows()
    if sys.platform == "darwin":
        return _run(["pbpaste"])
    for cmd in (["wl-paste", "--no-newline"], ["xclip", "-selection", "clipboard", "-o"], ["xsel", "--clipboard", "--output"]):
        if shutil.which(cmd[0]):
            return _run(cmd)
    raise ClipboardError("クリップボードを読めません (wl-paste / xclip / xsel のいずれかをインストールしてください)")


def _run(cmd: list[str]) -> str:
    try:
        cp = subprocess.run(cmd, capture_output=True, timeout=10)
    except (OSError, subprocess.SubprocessError) as e:
        raise ClipboardError(f"{cmd[0]} の実行に失敗しました: {e}") from e
    if cp.returncode != 0:
        raise ClipboardError(f"{cmd[0]} がエラーを返しました: {cp.stderr.decode(errors='replace').strip()}")
    return cp.stdout.decode("utf-8", errors="replace")


def _read_windows() -> str:
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    user32.OpenClipboard.argtypes = [wintypes.HWND]
    user32.OpenClipboard.restype = wintypes.BOOL
    user32.CloseClipboard.restype = wintypes.BOOL
    user32.IsClipboardFormatAvailable.argtypes = [wintypes.UINT]
    user32.IsClipboardFormatAvailable.restype = wintypes.BOOL
    user32.GetClipboardData.argtypes = [wintypes.UINT]
    user32.GetClipboardData.restype = wintypes.HANDLE
    kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalLock.restype = wintypes.LPVOID
    kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
    cf_unicodetext = 13

    for _ in range(20):  # 他のアプリが一瞬開いていることがあるので再試行
        if user32.OpenClipboard(None):
            break
        time.sleep(0.05)
    else:
        raise ClipboardError("クリップボードを開けません (他のアプリが使用中)")
    try:
        if not user32.IsClipboardFormatAvailable(cf_unicodetext):
            return ""
        handle = user32.GetClipboardData(cf_unicodetext)
        if not handle:
            raise ClipboardError("クリップボードのテキストを取得できません")
        ptr = kernel32.GlobalLock(handle)
        if not ptr:
            raise ClipboardError("クリップボードのメモリをロックできません")
        try:
            return ctypes.wstring_at(ptr)
        finally:
            kernel32.GlobalUnlock(handle)
    finally:
        user32.CloseClipboard()
