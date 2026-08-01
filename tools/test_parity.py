"""The Python and JavaScript chunkers must produce identical output.

The desktop app chunks with app/chunker.py and the extension chunks with
extension/chunker.js. If they drift, the same text is split into different
sentences depending on where you read it. This runs both over one corpus and
compares span-for-span.

    python tools/test_parity.py          (or: python -m unittest tools.test_parity)
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "app"))

from chunker import chunks  # noqa: E402

BRIDGE = ROOT / "tools" / "js_chunk.mjs"

CORPUS: list[tuple[str, int | None]] = [
    ("", None),
    ("   \n\n\t  ", None),
    ("Hello.", None),
    ("First one. Second one! Third one? Done.", None),
    ("No trailing punctuation", None),
    ("A hard wrapped\nsentence that continues.", None),
    ("No punctuation here\n\nSecond paragraph", None),
    ("Para one.\n\n\nPara two.", None),
    ('"Stop that!" she said. Then left.', None),
    ("Well... maybe. Sure!!! Right?!", None),
    ("Dr. Smith went to Washington. He arrived at 5 p.m. sharp.", None),
    ("Visit https://example.com/a.b.c today. Then leave.", None),
    ("Café résumé. 日本語のテキスト。Naïve façade.", None),
    ("Numbers 3.14159 and 2.71828 are constants. Yes.", None),
    ("Tabs\tand\tspaces   everywhere.   Next one.", None),
    ("  Leading space. Middle\n\n  Trailing.  ", None),
    ("Line one.\r\nLine two.\r\n\r\nPara two.", None),
    ("A" * 500, None),
    (" ".join(["word"] * 200), 50),
    (" ".join(["word"] * 60), 17),
    ("Short. " * 80, 40),
    ("one,two,three;four:five", None),
    ("Ends with an ellipsis…", None),
    ("Mixed… enders?! Really. Yes!", None),
    ("(Parenthetical sentence.) Next up.", None),
    ("He said 'ok.' Then nothing.", None),
    ("\n\n\nLeading blank lines.", None),
    ("Trailing blank lines.\n\n\n", None),
    ("Emoji 🎧 sentence one. Emoji 🔊 sentence two.", None),
    ("A. B. C. D. E.", None),
]


def js_chunks(corpus):
    node = shutil.which("node")
    if node is None:
        raise unittest.SkipTest("node is not installed; skipping parity check")

    payload = [{"text": text, "maxLen": max_len} for text, max_len in corpus]
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "corpus.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        result = subprocess.run(
            [node, str(BRIDGE), str(path)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )
    if result.returncode != 0:
        raise AssertionError(f"js bridge failed:\n{result.stderr}")
    return json.loads(result.stdout)


def utf16_slice(text: str, start: int, end: int) -> str:
    """Slice `text` using UTF-16 code-unit offsets, the way JavaScript indexes."""
    units = text.encode("utf-16-le")
    return units[start * 2 : end * 2].decode("utf-16-le", errors="replace")


class ChunkerParity(unittest.TestCase):
    """
    The two runtimes cannot share offsets: Python indexes by code point (and Tk
    agrees), while JavaScript indexes by UTF-16 code unit (as do DOM ranges).
    Astral characters such as emoji therefore shift the numbers apart by design.

    What must match is the *sentences* — the text that actually gets spoken.
    Each side's offsets are then checked for self-consistency in its own units.
    """

    @classmethod
    def setUpClass(cls):
        cls.js = js_chunks(CORPUS)

    def python_chunks(self, text, max_len):
        return chunks(text, max_len) if max_len else chunks(text)

    def test_same_number_of_cases(self):
        self.assertEqual(len(self.js), len(CORPUS))

    def test_both_runtimes_produce_the_same_sentences(self):
        mismatches = []
        for i, (text, max_len) in enumerate(CORPUS):
            py = [t for _s, _e, t in self.python_chunks(text, max_len)]
            js = [t for _s, _e, t in self.js[i]]
            if py != js:
                mismatches.append(
                    f"\ncase {i}: {text[:60]!r}\n  python: {py}\n  js:     {js}"
                )
        self.assertEqual(mismatches, [], "".join(mismatches))

    def test_offsets_agree_whenever_the_text_is_all_bmp(self):
        """Where no astral characters appear, the two index spaces coincide."""
        for i, (text, max_len) in enumerate(CORPUS):
            if any(ord(ch) > 0xFFFF for ch in text):
                continue
            py = [[s, e, t] for s, e, t in self.python_chunks(text, max_len)]
            self.assertEqual(py, self.js[i], f"case {i}: {text[:60]!r}")

    def test_js_spans_are_valid_utf16_offsets(self):
        for i, (text, _max_len) in enumerate(CORPUS):
            for start, end, piece in self.js[i]:
                self.assertEqual(
                    utf16_slice(text, start, end).strip(),
                    piece,
                    f"case {i}: JS span [{start},{end}) does not match its text",
                )

    def test_python_spans_are_valid_codepoint_offsets(self):
        for i, (text, max_len) in enumerate(CORPUS):
            for start, end, piece in self.python_chunks(text, max_len):
                self.assertEqual(
                    text[start:end].strip(),
                    piece,
                    f"case {i}: Python span [{start},{end}) does not match its text",
                )


if __name__ == "__main__":
    unittest.main()
