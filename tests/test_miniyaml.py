"""内蔵 YAML パーサ: PyYAML と同じ結果になること (PyYAML があれば差分比較)。"""

import unittest

from helpers import DEMO

from copilot_harness import miniyaml
from copilot_harness.templates import CONFIG_YAML

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None

CASES = {
    "scalars": (
        "a: 1\nb: -2.5\nc: yes\nd: off\ne: ~\nf:\ng: \"x # y\"\nh: 'it''s'\ni: 0x1F\nj: 010\nk: 1e3\nl: .5\n"
        "t: True\nn: Null\ns: 1_000\nv: 1.2.3\nver: 3.11\nport: COM5\n",
        {"a": 1, "b": -2.5, "c": True, "d": False, "e": None, "f": None, "g": "x # y", "h": "it's",
         "i": 31, "j": 8, "k": "1e3", "l": 0.5, "t": True, "n": None, "s": 1000, "v": "1.2.3",
         "ver": 3.11, "port": "COM5"},
    ),
    "collections": (
        "list:\n- a\n- b  # c\n- \"q\"\nnested:\n  x:\n    - 1\n    - [2, 'x, y']\n  y: {}\n  z: {p: 1, q: [a, b]}\nnext: 1\n",
        {"list": ["a", "b", "q"], "nested": {"x": [1, [2, "x, y"]], "y": {}, "z": {"p": 1, "q": ["a", "b"]}}, "next": 1},
    ),
    "block_scalars": (
        "cmd: >\n  arduino-cli compile\n  --fqbn x\n\n  last\nlit: |\n  line1\n    indented\n  line3\n"
        "strip: |-\n  s\nkeep: >+\n  k\n\nafter: 1\n",
        {"cmd": "arduino-cli compile --fqbn x\nlast\n", "lit": "line1\n  indented\nline3\n", "strip": "s",
         "keep": "k\n\n", "after": 1},
    ),
    "list_of_maps": (
        "items:\n  - name: a\n    v: 1\n  - name: b\n    v: [x]\n  -\n    deep: true\n  - - p\n    - q\n",
        {"items": [{"name": "a", "v": 1}, {"name": "b", "v": ["x"]}, {"deep": True}, ["p", "q"]]},
    ),
    "plain_multiline_and_paths": (
        "---\nplain: this is\n  continued\n  text\nurl: http://x/a#b\nwin: C:\\tools\\a.exe\n"
        "k2: \"esc\\tq\\u00e9\"\nk3: value # comment\n",
        {"plain": "this is continued text", "url": "http://x/a#b", "win": "C:\\tools\\a.exe",
         "k2": "esc\tq\u00e9", "k3": "value"},
    ),
}


class MiniYamlTest(unittest.TestCase):
    def test_cases(self):
        for name, (text, expected) in CASES.items():
            with self.subTest(name=name):
                self.assertEqual(miniyaml.load(text), expected)
                if yaml is not None:
                    self.assertEqual(yaml.safe_load(text), expected)

    def test_shipped_configs_match_pyyaml(self):
        docs = [CONFIG_YAML, (DEMO / "harness_config.yaml").read_text(encoding="utf-8")]
        for text in docs:
            mini = miniyaml.load(text)
            self.assertIsInstance(mini, dict)
            self.assertIn("build", mini)
            if yaml is not None:
                self.assertEqual(mini, yaml.safe_load(text))

    def test_empty_and_comment_only(self):
        self.assertIsNone(miniyaml.load(""))
        self.assertIsNone(miniyaml.load("# comment\n\n"))

    def test_unsupported_or_invalid(self):
        for text in ("a: &x 1\nb: *x\n", "a: !!str 1\n", "a: 1\n---\nb: 2\n", "a:\n\tb: 1\n",
                     "a: 1\n  b: 2\n", "a: [1, 2\n", "a: \"open\n", "just text\n  more: 1\n"):
            with self.subTest(text=text):
                with self.assertRaises(miniyaml.MiniYAMLError):
                    miniyaml.load(text)


if __name__ == "__main__":
    unittest.main()
