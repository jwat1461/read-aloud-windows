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

CODE_FIXTURE = """def read_aloud(text):
    pieces = chunks(text)
    for start, end, piece in pieces:
        engine.speak(piece)
    return len(pieces)


if __name__ == "__main__":
    read_aloud(sys.argv[1])
"""

URL_FIXTURE = "https://github.com/jwat1461/read-aloud-windows/blob/main/README.md"

LIST_FIXTURE = """- fix the nightly build
- call the client back
- renew the venue booking
- file the annual return
- order more refreshments
- chase the refund
"""

# Ordinary prose that happens to contain a bullet, a URL and a line of code.
# None of the three may ever be chosen, but the text as a whole is summarizable.
MIXED_FIXTURE = """The overnight migration failed twice before it finally
finished, and nobody has been able to reproduce the error on staging. We are
blocked on the reporting rebuild until somebody signs off on the schema change.
- chase the client about the refund
The client has asked three separate times now and is threatening to walk away
from the contract entirely. See https://example.com/runbooks/migration for the
steps we followed on the night. It costs $4000 a month to keep both of these
environments alive while the whole thing drags on unresolved. The deadline was
Friday and it is already Tuesday afternoon with no decision. retries = retries
+ 1; Everything else in the release is ready and waiting for somebody to make a
call."""

NEGATION_FIXTURE = (
    "There are no errors in the latest overnight run at all. The build is not "
    "broken this time around either. The overnight build failed twice again "
    "last night. We shipped everything on schedule without any incident. "
    "Attendance was steady and the venue was perfectly fine. Everyone went "
    "home happy at the end of it."
)

# Two sections, word for word identical beneath their headings. Anything that
# separates them can only have come from the heading above.
HEADER_BULLETS = """## Waiting on others

- The reconciliation has not come back from finance yet at all.
- The revised quote from the vendor has still not arrived.

## Notes from the review

- The reconciliation has not come back from finance yet at all.
- The revised quote from the vendor has still not arrived.
"""

# The same sentence three times, under two neutral headings and one that scores.
# Identical text means identical pain, frequency and shape, and none of the three
# sits at an edge, so position cannot separate them either: the heading above is
# the only thing that differs. Two of the three fit in the summary, so which two
# get picked is a direct read-out of whether inheritance works.
_TWIN = (
    "The reconciliation from finance is still outstanding after several weeks "
    "of chasing and nobody can say when it will land."
)
HEADER_PROSE = f"""## Notes from the review

{_TWIN}

## Anything else

{_TWIN}

## Waiting on others

{_TWIN}

Refreshments continue to be donated by the usual people every single week.
"""

NO_HEADERS = """The migration ran overnight and failed twice before it finally
finished. We are blocked on the reporting rebuild until somebody signs off on
the schema. The client has asked three times now and is threatening to ask for
a refund. Nobody has been able to reproduce the error on staging at all. It
costs $4000 a month to keep both environments alive while this drags on. The
deadline was Friday and it is now Tuesday afternoon."""

SNAPSHOT = Path(__file__).with_name("test_summary_snapshot.json")
GOLDEN = Path(__file__).with_name("test_golden_pieces.json")


def words_in(text):
    return len(summarize._WORD.findall(text.lower()))


def sentences_of(text):
    return [piece for _s, _e, piece in reading.plan(text, summary=False).pieces]


_REAL_LOG = None


def setUpModule():
    """Point the log at a scratch file. Without this the suite would append
    thousands of lines to the user's real summary_log.jsonl."""
    global _REAL_LOG
    _REAL_LOG = summarize.LOG_PATH
    summarize.LOG_PATH = Path(tempfile.mkdtemp()) / "summary_log.jsonl"


def tearDownModule():
    summarize.LOG_PATH = _REAL_LOG


def fresh_rules():
    return summarize.load_rules(Path(tempfile.mkdtemp()) / "rules.json")


class Bypasses(unittest.TestCase):
    """Text that should be read exactly as it arrived."""

    def setUp(self):
        self.rules = fresh_rules()

    def test_three_sentences_are_left_alone(self):
        self.assertEqual(len(sentences_of(SHORT_THREE_SENTENCES)), 3)
        self.assertEqual(
            summarize.bypass_reason(SHORT_THREE_SENTENCES), "too few sentences"
        )
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
        self.assertEqual(summarize.bypass_reason(under), "too few words")

        over = under + filler
        self.assertGreaterEqual(words_in(over), 60)
        self.assertIsNone(summarize.bypass_reason(over), "did not engage at 60 words")

    def test_four_sentences_with_enough_words_are_summarized(self):
        text = CORPUS["escalation"]
        self.assertGreaterEqual(len(sentences_of(text)), 4)
        self.assertGreaterEqual(words_in(text), 60)
        self.assertIsNone(summarize.bypass_reason(text))
        self.assertLess(
            len(summarize.summarize(text, self.rules)), len(sentences_of(text))
        )

    def test_source_code_is_read_verbatim(self):
        self.assertTrue(summarize.looks_like_code(CODE_FIXTURE))
        self.assertEqual(summarize.bypass_reason(CODE_FIXTURE), "code")
        self.assertEqual(
            summarize.summarize(CODE_FIXTURE, self.rules), sentences_of(CODE_FIXTURE)
        )

    def test_a_lone_url_is_read_verbatim(self):
        self.assertTrue(summarize.is_single_url(URL_FIXTURE))
        self.assertEqual(summarize.bypass_reason(URL_FIXTURE), "url")
        self.assertEqual(
            summarize.summarize(URL_FIXTURE, self.rules), sentences_of(URL_FIXTURE)
        )

    def test_a_list_of_short_lines_is_read_verbatim(self):
        self.assertTrue(summarize.looks_like_list(LIST_FIXTURE))
        self.assertEqual(summarize.bypass_reason(LIST_FIXTURE), "list")
        self.assertEqual(
            summarize.summarize(LIST_FIXTURE, self.rules), sentences_of(LIST_FIXTURE)
        )

    def test_wrapped_prose_is_not_mistaken_for_a_list(self):
        """The corpus is hard-wrapped; none of it may trip the list detector."""
        for name, text in CORPUS.items():
            self.assertIsNone(summarize.bypass_reason(text), f"{name} was bypassed")


class Selection(unittest.TestCase):
    def setUp(self):
        self.rules = fresh_rules()

    def test_no_code_url_or_bullet_line_is_ever_chosen(self):
        self.assertIsNone(summarize.bypass_reason(MIXED_FIXTURE))
        picked = summarize.summarize(MIXED_FIXTURE, self.rules)
        self.assertTrue(picked)
        for sentence in picked:
            self.assertFalse(
                summarize.is_excluded_line(sentence), f"chose {sentence!r}"
            )
            self.assertNotIn("https://", sentence)
            self.assertNotIn("retries =", sentence)

    def test_the_mixed_fixture_really_does_contain_all_three(self):
        """Guard the guard: if the fixture stops holding them, the test above
        starts passing for the wrong reason."""
        sentences = sentences_of(MIXED_FIXTURE)
        excluded = [s for s in sentences if summarize.is_excluded_line(s)]
        self.assertGreaterEqual(len(excluded), 3, excluded)

    def test_k_counts_only_the_sentences_a_summary_may_draw_from(self):
        sentences = sentences_of(MIXED_FIXTURE)
        eligible = summarize.eligible_indexes(sentences)
        self.assertLess(len(eligible), len(sentences))
        self.assertEqual(
            len(summarize.summarize(MIXED_FIXTURE, self.rules)),
            summarize.target_count(len(eligible)),
        )

    def test_k_clamps_at_two_and_at_eight(self):
        self.assertEqual(summarize.target_count(1), 2)
        self.assertEqual(summarize.target_count(4), 2)
        self.assertEqual(summarize.target_count(10), 2)
        self.assertEqual(summarize.target_count(11), 3)
        self.assertEqual(summarize.target_count(40), 8)
        self.assertEqual(summarize.target_count(4000), 8)

    def test_k_is_honoured_on_a_real_fixture(self):
        text = " ".join(CORPUS.values())
        self.assertEqual(
            len(summarize.summarize(text, self.rules)),
            summarize.target_count(len(summarize.eligible_indexes(sentences_of(text)))),
        )

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

    def test_pain_carries_further_than_pleasantry(self):
        sentences = sentences_of(CORPUS["escalation"])
        scores = dict(zip(sentences, summarize.rank_sentences(sentences, self.rules)))
        self.assertGreater(
            scores[next(s for s in sentences if "refund" in s)],
            scores[next(s for s in sentences if "ready and waiting" in s)],
        )

    def test_a_negated_pain_word_does_not_shout(self):
        """no errors and not broken must both rank below a plain failure."""
        sentences = sentences_of(NEGATION_FIXTURE)
        scores = dict(zip(sentences, summarize.rank_sentences(sentences, self.rules)))

        failed = next(s for s in sentences if "failed twice" in s)
        no_errors = next(s for s in sentences if "no errors" in s)
        not_broken = next(s for s in sentences if "not broken" in s)

        self.assertGreater(scores[failed], scores[no_errors])
        self.assertGreater(scores[failed], scores[not_broken])

    def test_the_negation_window_is_what_does_it(self):
        """Shut the window and the negated sentence comes roaring back."""
        sentences = sentences_of(NEGATION_FIXTURE)
        no_errors = next(i for i, s in enumerate(sentences) if "no errors" in s)

        blind = dict(self.rules, negation_window=0)
        self.assertGreater(
            summarize.rank_sentences(sentences, blind)[no_errors],
            summarize.rank_sentences(sentences, self.rules)[no_errors],
        )


class HeaderInheritance(unittest.TestCase):
    """A heading says what the lines under it are, so it lends them its score."""

    def setUp(self):
        self.rules = fresh_rules()

    def test_the_three_header_shapes_are_recognised(self):
        for header in ("## Waiting on others", "# Blocked", "**Overdue**",
                       "__Still to do__", "Waiting on others:", "Blocked:"):
            self.assertTrue(summarize.is_header(header), header)

    def test_ordinary_sentences_are_not_headers(self):
        for line in (
            "The build failed again this morning.",
            "It adds auto-read clipboard: with it on, every copy is read aloud.",
            "- chase the client about the refund",
            "There is a colon in this sentence: and then it carries on at length.",
        ):
            self.assertFalse(summarize.is_header(line), line)

    def test_a_heading_glued_to_its_first_line_is_not_treated_as_a_header(self):
        """Without a blank line the chunker keeps them in one piece. Calling
        that piece a header would bar real content from the summary and hand
        its score to the section below."""
        merged = "## Waiting on others\n- The reconciliation has not come back."
        self.assertFalse(summarize.is_header(merged))
        self.assertFalse(summarize.is_excluded_line(merged))

    def test_headers_are_never_selected(self):
        sentences = sentences_of(HEADER_PROSE)
        headers = [s for s in sentences if summarize.is_header(s)]
        self.assertGreaterEqual(len(headers), 3, "fixture lost its headings")
        for header in headers:
            self.assertTrue(summarize.is_excluded_line(header))
        for picked in summarize.summarize(HEADER_PROSE, self.rules):
            self.assertFalse(summarize.is_header(picked), picked)

    def test_a_scoring_header_lends_to_everything_until_the_next_one(self):
        sentences = sentences_of(HEADER_PROSE)
        bonuses = summarize.header_bonuses(sentences, self.rules)

        waiting = next(i for i, s in enumerate(sentences)
                       if summarize.is_header(s) and "Waiting" in s)
        following = [i for i in range(waiting + 1, len(sentences))
                     if not summarize.is_header(sentences[i])]
        self.assertGreaterEqual(len(following), 2, sentences)
        for index in following:
            self.assertGreater(bonuses[index], 0.0, sentences[index])

        # The heading itself inherits nothing, and a neutral one lends nothing.
        self.assertEqual(bonuses[waiting], 0.0)
        neutral = next(i for i, s in enumerate(sentences)
                       if summarize.is_header(s) and "Anything else" in s)
        after_neutral = next(i for i in range(neutral + 1, len(sentences))
                             if not summarize.is_header(sentences[i]))
        self.assertEqual(bonuses[after_neutral], 0.0, "a neutral header lent a bonus")

    def test_the_bonus_is_the_header_score_times_the_weight(self):
        sentences = ["## Waiting on others", "Finance have not sent it through."]
        score, _ = summarize._pain_score(sentences[0], self.rules)
        self.assertGreater(score, 0.0, "the header scored nothing")
        self.assertAlmostEqual(
            summarize.header_bonuses(sentences, self.rules)[1],
            score * self.rules["weights"]["header_weight"],
        )

    def test_identical_bullets_outrank_their_twins_under_a_neutral_header(self):
        """Word for word the same, so only the heading above can separate them.

        Bullets stay ineligible for *selection* -- that rule has not moved --
        so this is about rank, which is what the bonus changes.
        """
        sentences = sentences_of(HEADER_BULLETS)
        scores = summarize.rank_sentences(sentences, self.rules)

        pairs = {}
        for index, sentence in enumerate(sentences):
            if not summarize.is_header(sentence):
                pairs.setdefault(sentence.strip(), []).append(index)
        twinned = {text: idx for text, idx in pairs.items() if len(idx) == 2}
        self.assertGreaterEqual(len(twinned), 2, f"fixture lost its twins: {pairs}")

        for text, (under_waiting, under_notes) in twinned.items():
            self.assertGreater(
                scores[under_waiting], scores[under_notes],
                f"the heading made no difference to: {text}",
            )

    def test_identical_sentences_tie_exactly_without_the_bonus(self):
        """The premise the next test rests on: strip the bonus and the three
        twins are indistinguishable, so anything that separates them later can
        only have come from their headings."""
        sentences = sentences_of(HEADER_PROSE)
        twins = [i for i, s in enumerate(sentences) if "reconciliation" in s]
        self.assertEqual(len(twins), 3, sentences)

        blind = dict(
            self.rules, weights=dict(self.rules["weights"], header_weight=0.0)
        )
        scores = summarize.rank_sentences(sentences, blind)
        for other in twins[1:]:
            self.assertAlmostEqual(scores[twins[0]], scores[other], places=9)

    def test_the_bonus_changes_which_sentence_is_picked(self):
        sentences = sentences_of(HEADER_PROSE)
        twins = [i for i, s in enumerate(sentences) if "reconciliation" in s]
        blind = dict(
            self.rules, weights=dict(self.rules["weights"], header_weight=0.0)
        )

        def picked_indexes(rules):
            scores = summarize.rank_sentences(sentences, rules)
            eligible = summarize.eligible_indexes(sentences)
            keep = summarize.target_count(len(eligible))
            order = sorted(eligible, key=lambda i: (-scores[i], i))
            return sorted(order[:keep])

        without = picked_indexes(blind)
        with_bonus = picked_indexes(self.rules)
        self.assertNotEqual(with_bonus, without, "header_weight changed nothing")

        # Tied, the earlier twins win on index; the heading promotes the last.
        self.assertNotIn(twins[2], without)
        self.assertIn(twins[2], with_bonus, "the waiting section was not promoted")

    def test_a_document_with_no_headers_is_scored_exactly_as_before(self):
        sentences = sentences_of(NO_HEADERS)
        self.assertFalse(any(summarize.is_header(s) for s in sentences))

        _scores, breakdown = summarize.score_detail(sentences, self.rules)
        self.assertTrue(all(s["header_bonus"] == 0.0 for s in breakdown))

        # And the weight is then inert: any value gives the same answer.
        for weight in (0.0, 0.5, 5.0):
            tuned = dict(
                self.rules, weights=dict(self.rules["weights"], header_weight=weight)
            )
            self.assertEqual(
                summarize.rank_sentences(sentences, tuned),
                summarize.rank_sentences(sentences, self.rules),
                f"header_weight={weight} moved a headerless document",
            )

    def test_a_phrase_in_the_vocabulary_is_matched_as_a_phrase(self):
        pain = frozenset(self.rules["pain_words"])
        self.assertIn("waiting on", self.rules["pain_words"])
        self.assertEqual(
            summarize._pain_positions(summarize._words("we are waiting on finance"), pain),
            [2],
        )
        self.assertEqual(
            summarize._pain_positions(summarize._words("nothing at all here"), pain), []
        )

    def test_the_new_status_words_are_in_the_defaults(self):
        for word in ("waiting", "waiting on", "blocked", "owed", "pending", "overdue"):
            self.assertIn(word, summarize.DEFAULT_RULES["pain_words"], word)

    def test_a_negated_header_lends_nothing(self):
        sentences = ["Nothing blocked:", "Everything came back on time this week."]
        self.assertTrue(summarize.is_header(sentences[0]))
        self.assertEqual(summarize.header_bonuses(sentences, self.rules)[1], 0.0)

    def test_the_bonus_is_logged_as_its_own_field(self):
        log = Path(tempfile.mkdtemp()) / "summary_log.jsonl"
        summarize.summarize(HEADER_PROSE, self.rules, source="hotkey", log_path=log)
        record = json.loads(log.read_text("utf-8").splitlines()[0])

        self.assertTrue(record["picked"])
        for entry in record["picked"]:
            self.assertIn("header_bonus", entry)
        self.assertTrue(
            any(e["header_bonus"] > 0 for e in record["picked"]),
            "nothing inherited anything, so the field proves nothing",
        )


class Determinism(unittest.TestCase):
    def setUp(self):
        self.rules = fresh_rules()

    def test_the_same_text_summarizes_the_same_way_every_time(self):
        text = " ".join(CORPUS.values()) + " " + CORPUS["escalation"]
        sentences = sentences_of(text)
        self.assertGreaterEqual(len(sentences), 40, "fixture is too small to matter")

        first = summarize.summarize(text, self.rules)
        for run in range(19):
            self.assertEqual(
                summarize.summarize(text, self.rules), first, f"run {run + 2} differed"
            )

    def test_scores_do_not_depend_on_set_or_dict_ordering(self):
        sentences = sentences_of(CORPUS["escalation"])
        shuffled = dict(
            self.rules,
            pain_words=list(reversed(self.rules["pain_words"])),
            negations=list(reversed(self.rules["negations"])),
            weights=dict(reversed(list(self.rules["weights"].items()))),
        )
        self.assertEqual(
            summarize.rank_sentences(sentences, self.rules),
            summarize.rank_sentences(sentences, shuffled),
        )

    def test_ties_break_by_original_index_and_stay_broken(self):
        """Identical sentences score identically; the earlier one always wins."""
        twin = "The deadline slipped again."
        text = (
            "The overnight migration failed twice before it finally finished. "
            + twin
            + " Nobody has been able to reproduce the error on staging at all. "
            + twin
            + " Everything else in the release is ready and waiting for a call."
            " We are blocked on the rebuild until somebody signs the schema off."
        )
        sentences = sentences_of(text)
        scores = summarize.rank_sentences(sentences, self.rules)
        twins = [i for i, s in enumerate(sentences) if s == twin]
        self.assertEqual(len(twins), 2, sentences)
        self.assertAlmostEqual(scores[twins[0]], scores[twins[1]])

        for _run in range(20):
            fresh = summarize.rank_sentences(sentences, self.rules)
            order = sorted(range(len(sentences)), key=lambda i: (-fresh[i], i))
            self.assertLess(
                order.index(twins[0]), order.index(twins[1]), "tie-break flipped"
            )


class RulesFile(unittest.TestCase):
    def setUp(self):
        self.rules = fresh_rules()

    def test_a_missing_rules_file_is_recreated_with_defaults(self):
        path = Path(tempfile.mkdtemp()) / "nested" / "summary_rules.json"
        self.assertFalse(path.exists())

        rules = summarize.load_rules(path)
        self.assertTrue(path.exists(), "defaults were not written out")
        self.assertEqual(rules["weights"], summarize.DEFAULT_RULES["weights"])
        self.assertEqual(rules["pain_words"], summarize.DEFAULT_RULES["pain_words"])
        self.assertEqual(rules["negations"], summarize.DEFAULT_RULES["negations"])
        self.assertEqual(
            rules["negation_window"], summarize.DEFAULT_RULES["negation_window"]
        )
        self.assertEqual(json.loads(path.read_text("utf-8"))["weights"], rules["weights"])

    def test_a_rules_file_from_an_older_build_is_replaced_not_trusted(self):
        """Version 1 listed "no" and "not" as pain words. Version 2 treats them
        as negators, so keeping the old list would score every negated sentence
        as pain -- the exact thing the negation window exists to stop."""
        path = Path(tempfile.mkdtemp()) / "summary_rules.json"
        stale = {
            "pain_words": ["no", "not", "error", "mything"],
            "weights": {"pain_word": 1.0, "textrank_blend": 0.4},
        }
        path.write_text(json.dumps(stale), "utf-8")

        rules = summarize.load_rules(path)
        self.assertEqual(rules["pain_words"], summarize.DEFAULT_RULES["pain_words"])
        self.assertNotIn("no", rules["pain_words"])
        self.assertNotIn("not", rules["pain_words"])
        self.assertIn("negations", rules)
        self.assertEqual(rules["negation_window"], 3)
        self.assertEqual(
            json.loads(path.read_text("utf-8"))["version"], summarize.RULES_VERSION
        )

        kept = path.with_suffix(".v1.json")
        self.assertTrue(kept.exists(), "a tuned file was destroyed without a copy")
        self.assertEqual(json.loads(kept.read_text("utf-8")), stale)

    def test_a_version_two_file_is_migrated_for_the_header_weight(self):
        """v2 had no header_weight and none of the status vocabulary. Reading it
        back would leave header inheritance silently switched off."""
        path = Path(tempfile.mkdtemp()) / "summary_rules.json"
        v2 = {
            "version": 2,
            "pain_words": ["error", "failed"],
            "negations": ["not", "no"],
            "negation_window": 3,
            "weights": {"pain_word": 1.0, "cue_blend": 0.6, "luhn_blend": 0.4},
        }
        path.write_text(json.dumps(v2), "utf-8")

        rules = summarize.load_rules(path)
        self.assertEqual(rules["version"], summarize.RULES_VERSION)
        self.assertIn("header_weight", rules["weights"])
        self.assertEqual(rules["weights"]["header_weight"], 0.5)
        self.assertIn("waiting on", rules["pain_words"])
        self.assertTrue(path.with_suffix(".v1.json").exists(), "no copy was kept")

    def test_a_current_rules_file_is_left_alone(self):
        path = Path(tempfile.mkdtemp()) / "summary_rules.json"
        summarize.load_rules(path)                      # writes current defaults
        tuned = json.loads(path.read_text("utf-8"))
        tuned["pain_words"] = tuned["pain_words"] + ["gearbox"]
        path.write_text(json.dumps(tuned), "utf-8")

        self.assertIn("gearbox", summarize.load_rules(path)["pain_words"])
        self.assertFalse(path.with_suffix(".v1.json").exists())

    def test_a_corrupt_rules_file_falls_back_instead_of_crashing(self):
        path = Path(tempfile.mkdtemp()) / "summary_rules.json"
        path.write_text("{not json at all", "utf-8")
        self.assertEqual(
            summarize.load_rules(path)["weights"], summarize.DEFAULT_RULES["weights"]
        )

    def test_editing_a_weight_changes_the_ranking(self):
        sentences = sentences_of(CORPUS["meeting"])
        indifferent = dict(
            self.rules, weights=dict(self.rules["weights"], pain_word=0.0)
        )
        attentive = dict(self.rules, weights=dict(self.rules["weights"], pain_word=6.0))
        self.assertNotEqual(
            summarize.rank_sentences(sentences, indifferent),
            summarize.rank_sentences(sentences, attentive),
            "the pain_word weight did nothing",
        )

    def test_a_new_pain_word_promotes_the_sentence_that_uses_it(self):
        """Rank, not score: cue scores are normalised, so whatever is top scores
        1.0 however far ahead it is. Only the ordering is meaningful."""
        sentences = sentences_of(CORPUS["plain"])
        target = next(i for i, s in enumerate(sentences) if "weeding" in s)

        def rank_of(rules):
            scores = summarize.rank_sentences(sentences, rules)
            order = sorted(range(len(sentences)), key=lambda i: (-scores[i], i))
            return order.index(target)

        tuned = dict(self.rules, pain_words=self.rules["pain_words"] + ["weeding"])
        self.assertLess(
            rank_of(tuned), rank_of(self.rules), "a word added to the file did nothing"
        )

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


class ScoreLog(unittest.TestCase):
    """One line per call, saying why -- without keeping the text."""

    KEYS = {
        "timestamp", "source", "sentence_count", "word_count",
        "bypass_reason", "k", "picked",
    }
    SIGNALS = {
        "index", "score", "pain", "negation_hits",
        "question", "number", "position", "header_bonus", "frequency",
    }

    def setUp(self):
        self.rules = fresh_rules()
        self.log = Path(tempfile.mkdtemp()) / "summary_log.jsonl"

    def _lines(self):
        return [
            json.loads(line)
            for line in self.log.read_text("utf-8").splitlines()
            if line.strip()
        ]

    def test_one_call_writes_exactly_one_line_with_no_sentence_text(self):
        summarize.summarize(
            CORPUS["escalation"], self.rules, source="hotkey", log_path=self.log
        )
        lines = self._lines()
        self.assertEqual(len(lines), 1, lines)

        record = lines[0]
        self.assertEqual(set(record), self.KEYS)
        self.assertEqual(record["source"], "hotkey")
        self.assertIsNone(record["bypass_reason"])
        self.assertEqual(
            record["sentence_count"], len(sentences_of(CORPUS["escalation"]))
        )
        self.assertEqual(record["word_count"], words_in(CORPUS["escalation"]))
        self.assertEqual(record["k"], len(record["picked"]))

        for entry in record["picked"]:
            self.assertEqual(set(entry), self.SIGNALS, "unexpected keys in a pick")
            self.assertNotIn("text", entry)

        # The strongest form of the promise: no sentence of the source appears
        # anywhere in the line, however the record might be shaped.
        raw = self.log.read_text("utf-8")
        for sentence in sentences_of(CORPUS["escalation"]):
            self.assertNotIn(sentence.strip(), raw, "the log kept the text")

    def test_each_call_adds_exactly_one_more_line(self):
        for count in range(1, 4):
            summarize.summarize(
                CORPUS["meeting"], self.rules, source="queue", log_path=self.log
            )
            self.assertEqual(len(self._lines()), count)

    def test_a_bypass_is_logged_too_and_says_which_rule_fired(self):
        for text in (SHORT_THREE_SENTENCES, CODE_FIXTURE, URL_FIXTURE, LIST_FIXTURE):
            summarize.summarize(text, self.rules, source="hotkey", log_path=self.log)

        records = self._lines()
        self.assertEqual(len(records), 4)
        self.assertEqual(
            [r["bypass_reason"] for r in records],
            ["too few sentences", "code", "url", "list"],
        )
        for record in records:
            self.assertIsNone(record["k"])
            self.assertEqual(record["picked"], [])

    def test_the_signals_add_up_to_what_was_scored(self):
        """The log has to be worth tuning against, so the numbers in it must be
        the numbers that actually decided the outcome."""
        summarize.summarize(
            CORPUS["escalation"], self.rules, source="hotkey", log_path=self.log
        )
        record = self._lines()[0]

        sentences = sentences_of(CORPUS["escalation"])
        scores, breakdown = summarize.score_detail(sentences, self.rules)
        for entry in record["picked"]:
            index = entry["index"]
            self.assertAlmostEqual(entry["score"], scores[index], places=5)
            self.assertAlmostEqual(entry["pain"], breakdown[index]["pain"], places=5)
            self.assertEqual(entry["negation_hits"], breakdown[index]["negation_hits"])
            self.assertAlmostEqual(
                entry["frequency"], breakdown[index]["frequency"], places=5
            )

    def test_negation_hits_counts_what_the_window_suppressed(self):
        sentences = sentences_of(NEGATION_FIXTURE)
        _scores, breakdown = summarize.score_detail(sentences, self.rules)
        no_errors = next(i for i, s in enumerate(sentences) if "no errors" in s)
        self.assertGreater(
            breakdown[no_errors]["negation_hits"], 0, "nothing was suppressed"
        )
        self.assertEqual(breakdown[no_errors]["pain"], 0.0)

    def test_sentence_text_appears_only_when_it_is_asked_for(self):
        sentences = sentences_of(CORPUS["escalation"])
        signals = [{"pain": 1.0, "negation_hits": 0, "question": 0.0,
                    "number": 0.0, "position": 0.0, "frequency": 0.5}]

        without = summarize._record(
            CORPUS["escalation"], sentences, None, [0], [0.5], signals, 2,
            "hotkey", with_text=False,
        )
        self.assertNotIn("text", without["picked"][0])

        with_text = summarize._record(
            CORPUS["escalation"], sentences, None, [0], [0.5], signals, 2,
            "hotkey", with_text=True,
        )
        self.assertEqual(with_text["picked"][0]["text"], sentences[0])

    def test_the_default_setting_keeps_text_out(self):
        self.assertFalse(settings.DEFAULTS["log_sentence_text"])
        stored = settings.load()
        try:
            settings.save(dict(stored, log_sentence_text=False))
            self.assertFalse(summarize._log_sentence_text())
            settings.save(dict(stored, log_sentence_text=True))
            self.assertTrue(summarize._log_sentence_text())
        finally:
            settings.save(stored)

    def test_the_log_rotates_at_five_megabytes(self):
        rotated = self.log.with_name("summary_log.1.jsonl")
        self.log.write_text("x" * summarize.LOG_MAX_BYTES, "utf-8")
        self.assertGreaterEqual(self.log.stat().st_size, summarize.LOG_MAX_BYTES)

        summarize.summarize(
            CORPUS["escalation"], self.rules, source="hotkey", log_path=self.log
        )
        self.assertTrue(rotated.exists(), "nothing was rotated")
        self.assertEqual(len(self._lines()), 1, "the new file should start fresh")
        self.assertGreaterEqual(rotated.stat().st_size, summarize.LOG_MAX_BYTES)

    def test_the_cap_is_five_megabytes(self):
        self.assertEqual(summarize.LOG_MAX_BYTES, 5 * 1024 * 1024)

    def test_a_log_that_cannot_be_written_does_not_break_the_summary(self):
        """A summary must not fail over its own diary."""
        unwritable = Path(tempfile.mkdtemp()) / "summary_log.jsonl"
        unwritable.mkdir()  # a directory where the file should be
        picked = summarize.summarize(
            CORPUS["escalation"], self.rules, source="hotkey", log_path=unwritable
        )
        self.assertTrue(picked)


class Snapshot(unittest.TestCase):
    """What the summarizer picks on the fixed corpus, recorded and compared.

    Same guarantee as a syrupy snapshot, without adding pytest and a
    site-packages dependency to a project whose selling point is needing
    neither. Regenerate deliberately with `python tools/resnapshot.py`.
    """

    def test_the_corpus_picks_match_the_snapshot(self):
        recorded = json.loads(SNAPSHOT.read_text("utf-8"))
        golden = json.loads(GOLDEN.read_text("utf-8"))
        self.assertEqual(sorted(recorded), sorted(golden), "snapshot corpus drifted")

        rules = fresh_rules()
        for name in sorted(golden):
            text = golden[name]["text"]
            self.assertEqual(
                summarize.bypass_reason(text), recorded[name]["bypass"], name
            )
            self.assertEqual(
                summarize.summarize(text, rules),
                recorded[name]["picked"],
                f"{name}: the summarizer now picks different sentences",
            )


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

    def test_summary_spans_point_into_the_summary_it_returns(self):
        on = reading.plan(CORPUS["meeting"], summary=True)
        self.assertTrue(on.summarized)
        for start, end, piece in on.pieces:
            self.assertEqual(on.text[start:end], piece)

    def test_a_bypass_is_indistinguishable_from_the_mode_being_off(self):
        """The caller must not be able to tell summary mode was on, or the
        desktop app would offer a Source pane with nothing behind it and the
        reader would speak a cue for text it never trimmed."""
        for name, text in (
            ("short", SHORT_THREE_SENTENCES),
            ("code", CODE_FIXTURE),
            ("url", URL_FIXTURE),
            ("list", LIST_FIXTURE),
        ):
            on = reading.plan(text, summary=True)
            off = reading.plan(text, summary=False)
            self.assertFalse(on.summarized, name)
            self.assertEqual(on.text, off.text, name)
            self.assertEqual(on.pieces, off.pieces, name)

    def test_the_stored_setting_is_the_fallback_when_none_is_passed(self):
        stored = settings.load()
        try:
            settings.save(dict(stored, summary_mode=False))
            self.assertFalse(reading.plan(CORPUS["escalation"]).summarized)
            settings.save(dict(stored, summary_mode=True))
            self.assertTrue(reading.plan(CORPUS["escalation"]).summarized)
        finally:
            settings.save(stored)

    def test_summary_mode_survives_a_settings_reload(self):
        stored = settings.load()
        try:
            settings.save(dict(stored, summary_mode=True))
            self.assertTrue(settings.load()["summary_mode"])
            settings.save(dict(stored, summary_mode=False))
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

    def test_the_corpus_still_chunks_to_the_golden_recording(self):
        golden = json.loads(GOLDEN.read_text("utf-8"))
        self.assertEqual(len(golden), 12, "the golden corpus should hold 12 entries")

        for name, expected in sorted(golden.items()):
            plan = reading.plan(expected["text"], summary=False)
            self.assertEqual(plan.sentences, expected["pieces"], f"{name} drifted")
            self.assertEqual(
                [[s, e] for s, e, _p in plan.pieces], expected["spans"], f"{name} spans"
            )
            self.assertFalse(plan.summarized)
            self.assertEqual(plan.text, expected["text"])
            self.assertEqual(plan.source, expected["text"])

    def test_the_plan_reports_spans_into_the_text_it_returns(self):
        for entry in json.loads(GOLDEN.read_text("utf-8")).values():
            plan = reading.plan(entry["text"], summary=False)
            for start, end, piece in plan.pieces:
                self.assertEqual(plan.text[start:end], piece)

    def test_an_offset_shifts_the_spans_without_touching_the_sentences(self):
        text = CORPUS["changelog"]
        flat = reading.plan(text, summary=False)
        shifted = reading.plan(text, offset=100, summary=False)
        self.assertEqual(flat.sentences, shifted.sentences)
        self.assertEqual(
            [(s + 100, e + 100) for s, e, _p in flat.pieces],
            [(s, e) for s, e, _p in shifted.pieces],
        )


if __name__ == "__main__":
    unittest.main()
