"""デモ用の疑似 build (arduino-cli compile の代わり)。

括弧の対応と、ヘッダで宣言されていない can_hw_* / nvic_* 関数の呼び出しを検査し、
gcc 形式 (path:line:col: error: ...) でエラーを出力する。
"""

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def declared_functions() -> set[str]:
    names = set()
    for h in (ROOT / "include").glob("*.h"):
        text = re.sub(r"//[^\n]*|/\*.*?\*/", "", h.read_text(encoding="utf-8"), flags=re.S)
        names.update(re.findall(r"^\s*[\w\s*]+?\b(\w+)\s*\(.*\)\s*;", text, flags=re.M))
    return names


def main() -> int:
    errors = 0
    declared = declared_functions()
    sources = sorted([*ROOT.glob("*.ino"), *(ROOT / "src").glob("*.cpp")])
    for src in sources:
        rel = src.relative_to(ROOT).as_posix()
        text = src.read_text(encoding="utf-8")
        depth = 0
        for no, line in enumerate(text.splitlines(), 1):
            code = line.split("//")[0]
            depth += code.count("{") - code.count("}")
            if depth < 0:
                print(f"{rel}:{no}:1: error: expected declaration before '}}' token")
                errors += 1
                depth = 0
            for name in re.findall(r"\b((?:can_hw|nvic)_\w+)\s*\(", code):
                if name not in declared:
                    print(f"{rel}:{no}:5: error: '{name}' was not declared in this scope")
                    errors += 1
        if depth != 0:
            print(f"{rel}:{len(text.splitlines())}:1: error: expected '}}' at end of input")
            errors += 1
        print(f"Compiling {rel} ...")
    if errors:
        print(f"compilation terminated with {errors} error(s).")
        return 1
    print("Sketch uses 9412 bytes (29%) of program storage space.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
