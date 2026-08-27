"""Tests for summary mode: the summarizer, its rules file, and the promise that
turning the mode off leaves today's behaviour untouched.

No speech engine and no window: this suite is pure text, so it runs silent and
fast alongside `chunker` and `parity`.

    python -m unittest test_summary -v
"""

import json
import socket
import tempfile
import unittest
from pathlib import Path

import reading
import settings
import summarize

# --------------------------------------------------------------------- corpus

CORPUS = {
    "escalation": """
        The migration ran overnight and failed twice before it finished. We are
        blocked on the reporting rebuild until someone signs off on the schema.
        The client has asked three times now and is threatening to ask for a
        refund. Nobody has been able to reproduce the error on staging. It costs
        $4000 a month to keep both environments alive while this drags on. The
        deadline was Friday and it is now Tuesday. Can we get a decision today?
        Everything else in the release is ready and waiting.
    """,
    "meeting": """
        Attendance was steady through the quarter. The Tuesday group has grown a
        little and the Thursday one has held level. Two people asked about
        transport, which we still cannot help with. The venue put its rate up in
        March and the increase is not sustainable past the summer. We are late
        filing the annual return and the deadline has already passed once. The
        treasurer is away until the end of the month. Refreshments continue to
        be donated. We should decide about the venue before September.
    """,
    "changelog": """
        This release adds a tray icon with voice, speed and volume controls. It
        fixes DOM offset clamping in the extension. It fixes TTS error storms
        that could fill the log. Stale timers no longer fire after a window is
        closed. The clipboard is now restored after a synthetic copy. Sentence
        highlighting scrolls into view. Nothing in this release changes the
        settings file format. The parity suite is unchanged.
    """,
    "plain": """
        The garden looks well this year. The roses came back after the frost and
        the hedge has thickened up nicely. We moved the bench to the far corner
        where it catches the afternoon. The path needs weeding but it can wait.
        Next spring we might put in a second bed along the fence. There is a
        blackbird nesting in the ivy again. It sings from the shed roof most
        evenings. The apples should be ready by the end of the month.
    """,
}

SHORT_THREE_SENTENCES = (
    "The build failed again this morning after the overnight deploy went out. "
    "Nobody on the team has been able to reproduce it on their own machine. "
    "We are blocked until someone can say what actually changed in that release."
)


def words_in(text):
    return len(summarize._WORD.findall(text.lower()))


def sentences_of(text):
    return [piece for _s, _e, piece in reading.plan(text).pieces]


class Summarizer(unittest.TestCase):
    def setUp(self):
        self.rules = summarize.load_rules(Path(tempfile.mkdtemp()) / "rules.json")

    # ------------------------------------------------------------ bypassing

    def test_three_sentences_are_left_alone(self):
        self.assertEqual(len(sentences_of(SHORT_THREE_SENTENCES)), 3)
        self.assertFalse(summarize.should_summarize(SHORT_THREE_SENTENCES))
        self.assertEqual(
            summarize.summarize(SHORT_THREE_SENTENCES, self.rules),
            sentences_of(SHORT_THREE_SENTENCES),
        )

    def test_fifty_nine_words_are_left_alone_and_sixty_are_not(self):
        filler = "The report is late and the client is asking about it again. "
        under = ""
        while words_in(under + filler) <= 59:
            under += filler
        self.assertLessEqual(words_in(under), 59)
        self.assertGreaterEqual(len(sentences_of(under)), 4)
        self.assertFalse(summarize.should_summarize(under), "bypass missed at 59 words")

        over = under + filler
        self.assertGreaterEqual(words_in(over), 60)
        self.assertTrue(summarize.should_summarize(over), "did not engage at 60 words")

    def test_four_sentences_with_enough_words_are_summarized(self):
        text = CORPUS["escalation"]
        self.assertGreaterEqual(len(sentences_of(text)), 4)
        self.assertGreaterEqual(words_in(text), 60)
        self.assertTrue(summarize.should_summarize(text))
        self.assertLess(
            len(summarize.summarize(text, self.rules)), len(sentences_of(text))
        )

    # --------------------------------------------------------- determinism

    def test_the_same_text_summarizes_the_same_way_every_time(self):
        text = " ".join(CORPUS.values()) + " " + CORPUS["escalation"]
        sentences = sentences_of(text)
        self.assertGreaterEqual(len(sentences), 30, "fixture is too small to matter")

        first = summarize.summarize(text, self.rules)
        for run in range(19):
            self.assertEqual(
                summarize.summarize(text, self.rules), first, f"run {run + 2} differed"
            )

    def test_scores_do_not_depend_on_set_or_dict_ordering(self):
        """Same rules, rebuilt in a different order, must rank identically."""
        sentences = sentences_of(CORPUS["escalation"])
        shuffled = {
            "pain_words": list(reversed(self.rules["pain_words"])),
            "weights": dict(reversed(list(self.rules["weights"].items()))),
        }
        self.assertEqual(
            summarize.rank_sentences(sentences, self.rules),
            summarize.rank_sentences(sentences, shuffled),
        )

    # ---------------------------------------------------------------- shape

    def test_the_summary_keeps_the_original_order(self):
        for name, text in CORPUS.items():
            sentences = sentences_of(text)
            picked = summarize.summarize(text, self.rules)
            positions = [sentences.index(p) for p in picked]
            self.assertEqual(positions, sorted(positions), f"{name} came back shuffled")

    def test_every_summary_sentence_came_from_the_source(self):
        """Extractive means extractive: nothing is invented."""
        for name, text in CORPUS.items():
            sentences = sentences_of(text)
            for picked in summarize.summarize(text, self.rules):
                self.assertIn(picked, sentences, f"{name} produced a new sentence")

    def test_k_clamps_at_two_and_at_eight(self):
        self.assertEqual(summarize.target_count(1), 2)
        self.assertEqual(summarize.target_count(4), 2)
        self.assertEqual(summarize.target_count(10), 2)
        self.assertEqual(summarize.target_count(11), 3)
        self.assertEqual(summarize.target_count(40), 8)
        self.assertEqual(summarize.target_count(4000), 8)

    def test_k_is_honoured_on_a_real_fixture(self):
        text = " ".join(CORPUS.values())
        sentences = sentences_of(text)
        self.assertEqual(
            len(summarize.summarize(text, self.rules)),
            summarize.target_count(len(sentences)),
        )

    def test_pain_carries_further_than_pleasantry(self):
        """The point of the thing: trouble outranks the weather."""
        text = CORPUS["escalation"]
        sentences = sentences_of(text)
        scores = dict(zip(sentences, summarize.rank_sentences(sentences, self.rules)))
        painful = next(s for s in sentences if "refund" in s)
        bland = next(s for s in sentences if "ready and waiting" in s)
        self.assertGreater(scores[painful], scores[bland])

    # ----------------------------------------------------------- rules file

    def test_a_missing_rules_file_is_recreated_with_defaults(self):
        path = Path(tempfile.mkdtemp()) / "nested" / "summary_rules.json"
        self.assertFalse(path.exists())

        rules = summarize.load_rules(path)
        self.assertTrue(path.exists(), "defaults were not written out")
        self.assertEqual(rules["pain_words"], summarize.DEFAULT_RULES["pain_words"])
        self.assertEqual(rules["weights"], summarize.DEFAULT_RULES["weights"])
        self.assertEqual(json.loads(path.read_text("utf-8"))["weights"], rules["weights"])

    def test_a_corrupt_rules_file_falls_back_instead_of_crashing(self):
        path = Path(tempfile.mkdtemp()) / "summary_rules.json"
        path.write_text("{not json at all", "utf-8")
        self.assertEqual(
            summarize.load_rules(path)["weights"], summarize.DEFAULT_RULES["weights"]
        )

    def test_editing_a_weight_changes_the_ranking(self):
        text = CORPUS["meeting"]
        sentences = sentences_of(text)

        indifferent = {
            "pain_words": list(self.rules["pain_words"]),
            "weights": dict(self.rules["weights"], pain_word=0.0),
        }
        attentive = {
            "pain_words": list(self.rules["pain_words"]),
            "weights": dict(self.rules["weights"], pain_word=6.0),
        }
        self.assertNotEqual(
            summarize.rank_sentences(sentences, indifferent),
            summarize.rank_sentences(sentences, attentive),
            "the pain_word weight did nothing",
        )

    def test_a_new_pain_word_promotes_the_sentence_that_uses_it(self):
        """Rank, not score. Cue scores are normalised, so whatever is top scores
        1.0 however far ahead it is; only the ordering is meaningful."""
        sentences = sentences_of(CORPUS["plain"])
        target = next(i for i, s in enumerate(sentences) if "weeding" in s)
        self.assertNotIn(
            "weeding",
            " ".join(summarize.summarize(CORPUS["plain"], self.rules)),
            "fixture broken: the sentence is already being picked",
        )

        def rank_of(rules):
            scores = summarize.rank_sentences(sentences, rules)
            order = sorted(range(len(sentences)), key=lambda i: (-scores[i], i))
            return order.index(target)

        tuned = {
            "pain_words": self.rules["pain_words"] + ["weeding"],
            "weights": dict(self.rules["weights"]),
        }
        self.assertLess(
            rank_of(tuned), rank_of(self.rules), "a word added to the file did nothing"
        )

    # -------------------------------------------------------------- offline

    def test_the_extractive_path_opens_no_socket(self):
        """The app's promise: no network calls. Not fewer — none."""
        opened = []

        class Tripwire(socket.socket):
            def __init__(self, *args, **kwargs):
                opened.append(args)
                raise AssertionError("summary mode opened a socket")

        real = socket.socket
        socket.socket = Tripwire
        try:
            for text in CORPUS.values():
                summarize.summarize(text, self.rules)
            summarize.load_rules(Path(tempfile.mkdtemp()) / "rules.json")
        finally:
            socket.socket = real
        self.assertEqual(opened, [])


class PlanWithSummaryMode(unittest.TestCase):
    """The one hook. Nothing else in the app is allowed to summarize."""

    def test_summary_off_is_byte_identical_to_no_summary_mode_at_all(self):
        for name, text in CORPUS.items():
            off = reading.plan(text, summary=False)
            self.assertFalse(off.summarized, name)
            self.assertEqual(off.text, text)
            self.assertEqual(off.source, text)
            self.assertEqual(off.sentences, sentences_of(text), name)

    def test_summary_on_replaces_the_text_but_keeps_the_source(self):
        text = CORPUS["escalation"]
        on = reading.plan(text, summary=True)
        self.assertTrue(on.summarized)
        self.assertEqual(on.source, text)
        self.assertNotEqual(on.text, text)
        self.assertLess(len(on.sentences), len(sentences_of(text)))
        self.assertEqual(on.sentences, summarize.summarize(text))

    def test_summary_spans_point_into_the_summary_it_returns(self):
        on = reading.plan(CORPUS["meeting"], summary=True)
        self.assertTrue(on.summarized)
        for start, end, piece in on.pieces:
            self.assertEqual(on.text[start:end], piece)

    def test_a_bypass_is_indistinguishable_from_the_mode_being_off(self):
        """Short text: the caller must not be able to tell summary mode was on,
        or the desktop app would offer a Source pane with nothing behind it."""
        on = reading.plan(SHORT_THREE_SENTENCES, summary=True)
        off = reading.plan(SHORT_THREE_SENTENCES, summary=False)
        self.assertFalse(on.summarized)
        self.assertEqual(on.text, off.text)
        self.assertEqual(on.pieces, off.pieces)

    def test_the_stored_setting_is_the_fallback_when_none_is_passed(self):
        stored = settings.load()
        try:
            saved = dict(stored)
            saved["summary_mode"] = False
            settings.save(saved)
            self.assertFalse(reading.plan(CORPUS["escalation"]).summarized)

            saved["summary_mode"] = True
            settings.save(saved)
            self.assertTrue(reading.plan(CORPUS["escalation"]).summarized)
        finally:
            settings.save(stored)

    def test_summary_mode_survives_a_settings_reload(self):
        stored = settings.load()
        try:
            saved = dict(stored)
            saved["summary_mode"] = True
            settings.save(saved)
            self.assertTrue(settings.load()["summary_mode"])

            saved["summary_mode"] = False
            settings.save(saved)
            self.assertFalse(settings.load()["summary_mode"])
        finally:
            settings.save(stored)

    def test_summary_mode_defaults_to_off(self):
        self.assertFalse(settings.DEFAULTS["summary_mode"])


class ReadingPlanUnchanged(unittest.TestCase):
    """Summary mode off must leave the engine seeing exactly what it saw before.

    The golden file records what `chunker` produced for the corpus before any of
    this existed; the plan has to still match it, sentence for sentence.
    """

    GOLDEN = Path(__file__).with_name("test_golden_pieces.json")

    def test_the_corpus_still_chunks_to_the_golden_recording(self):
        golden = json.loads(self.GOLDEN.read_text("utf-8"))
        self.assertEqual(len(golden), 12, "the golden corpus should hold 12 entries")

        for name, expected in sorted(golden.items()):
            plan = reading.plan(expected["text"])
            self.assertEqual(plan.sentences, expected["pieces"], f"{name} drifted")
            self.assertEqual(
                [[s, e] for s, e, _p in plan.pieces], expected["spans"], f"{name} spans"
            )
            self.assertFalse(plan.summarized)
            self.assertEqual(plan.text, expected["text"])
            self.assertEqual(plan.source, expected["text"])

    def test_the_plan_reports_spans_into_the_text_it_returns(self):
        for entry in json.loads(self.GOLDEN.read_text("utf-8")).values():
            plan = reading.plan(entry["text"])
            for start, end, piece in plan.pieces:
                self.assertEqual(plan.text[start:end], piece)

    def test_an_offset_shifts_the_spans_without_touching_the_sentences(self):
        text = CORPUS["changelog"]
        flat = reading.plan(text)
        shifted = reading.plan(text, offset=100)
        self.assertEqual(flat.sentences, shifted.sentences)
        self.assertEqual(
            [(s + 100, e + 100) for s, e, _p in flat.pieces],
            [(s, e) for s, e, _p in shifted.pieces],
        )


if __name__ == "__main__":
    unittest.main()
