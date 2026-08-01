"""Tests for the sentence chunker.

    python -m unittest test_chunker -v
"""

import unittest

from chunker import chunks, sentence_spans


class SentenceSpans(unittest.TestCase):
    def spoken(self, text, **kw):
        return [t for _, _, t in chunks(text, **kw)]

    def test_splits_on_sentence_enders(self):
        text = "First one. Second one! Third one? Done."
        self.assertEqual(
            self.spoken(text),
            ["First one.", "Second one!", "Third one?", "Done."],
        )

    def test_spans_index_back_into_source(self):
        text = "Alpha. Beta gamma."
        for start, end, piece in chunks(text):
            self.assertEqual(text[start:end].strip(), piece)

    def test_spans_are_ordered_and_non_overlapping(self):
        text = "One. Two. Three.\n\nFour five six. Seven!"
        spans = sentence_spans(text)
        for (_, prev_end), (next_start, _) in zip(spans, spans[1:]):
            self.assertLessEqual(prev_end, next_start)

    def test_single_newline_is_a_wrap_not_a_break(self):
        text = "A hard wrapped\nsentence that continues."
        self.assertEqual(self.spoken(text), ["A hard wrapped\nsentence that continues."])

    def test_blank_line_breaks_a_paragraph(self):
        text = "No punctuation here\n\nSecond paragraph"
        self.assertEqual(self.spoken(text), ["No punctuation here", "Second paragraph"])

    def test_long_text_without_punctuation_breaks_at_a_word(self):
        text = " ".join(["word"] * 200)
        pieces = self.spoken(text, max_len=50)
        self.assertGreater(len(pieces), 1)
        for piece in pieces:
            self.assertLess(len(piece), 60)
            self.assertFalse(piece.startswith(" "))
            # Never split mid-word.
            self.assertTrue(all(w == "word" for w in piece.split()))

    def test_trailing_quote_stays_with_its_sentence(self):
        text = '"Stop that!" she said. Then left.'
        self.assertEqual(self.spoken(text), ['"Stop that!"', "she said.", "Then left."])

    def test_ellipsis_does_not_produce_empty_chunks(self):
        text = "Well... maybe. Sure!!!"
        self.assertEqual(self.spoken(text), ["Well...", "maybe.", "Sure!!!"])

    def test_covers_all_non_whitespace_characters(self):
        text = "  Leading space. Middle\n\n  Trailing.  "
        joined = "".join(t for _, _, t in chunks(text))
        self.assertEqual(
            "".join(joined.split()),
            "".join(text.split()),
        )

    def test_empty_and_whitespace_only(self):
        self.assertEqual(chunks(""), [])
        self.assertEqual(chunks("   \n\n\t  "), [])

    def test_unicode_survives(self):
        text = "Café résumé. 日本語のテキスト。Naïve."
        pieces = self.spoken(text)
        self.assertIn("Café résumé.", pieces)
        self.assertTrue(any("日本語" in p for p in pieces))


if __name__ == "__main__":
    unittest.main()
