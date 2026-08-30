"""Tests for summary mode: the summarizer, its rules file, and the promise that
turning the mode off leaves today's behaviour untouched.

No speech engine and no window: this suite is pure text, so it runs silent and
fast alongside `chunker` and `parity`.

    python -m unittest test_summary -v
"""

import json
import re
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

# Three sentences, but past 400 characters, so "too few sentences" is reached
# rather than being masked by the min_chars rule in front of it.
THREE_LONG_SENTENCES = (
    "The overnight build failed again this morning shortly after the scheduled "
    "deployment went out to the staging cluster, and the logs stop without an "
    "error. Nobody on the platform team has been able to reproduce the fault "
    "on their own machine, even running the identical commit and the same "
    "container image. We are blocked until somebody can say what actually "
    "changed in that release, because the diff looks empty from here."
)

# Comfortably past every size bypass, for asserting the boundary from above.
LONG_ENOUGH_PROSE = (
    "The reporting rebuild slipped again this week and nobody has told the "
    "client yet. The schema sign-off is still sitting with the data team and "
    "has not moved in nine days. We are paying for both environments while "
    "this drags on, which is money we agreed to stop spending. The deadline "
    "was Friday and it is already Tuesday afternoon. Somebody really needs to "
    "make a call today about what ships and what waits."
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

# What an agent actually sends: two complaints, three instructions, one aside.
# Reading somebody the problem and dropping the fix is the wrong half.
INSTRUCTIONS = """The migration failed twice overnight before it finally
finished. Open chrome://extensions and turn on Developer mode at the top right.
We are blocked on the reporting rebuild until somebody signs off on it. Click
Load unpacked and select the extension folder inside the project. The client has
asked three separate times now and wants a refund. Make sure the tray icon is
visible before you carry on with anything else."""

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
    _REAL_LOG = summarize.default_log_path
    scratch = Path(tempfile.mkdtemp()) / "summary_log.jsonl"
    summarize.default_log_path = lambda: scratch


def tearDownModule():
    summarize.default_log_path = _REAL_LOG


def fresh_rules():
    return summarize.load_rules(Path(tempfile.mkdtemp()) / "rules.json")


class Bypasses(unittest.TestCase):
    """Text that should be read exactly as it arrived."""

    def setUp(self):
        self.rules = fresh_rules()

    def test_under_four_hundred_characters_is_left_alone(self):
        """The headline rule: a brief of a paragraph is noise."""
        text = LONG_ENOUGH_PROSE
        self.assertGreaterEqual(len(text), 400)
        self.assertIsNone(summarize.bypass_reason(text))

        short = text[:390].rsplit(".", 1)[0] + "."
        self.assertLess(len(short), 400)
        self.assertEqual(summarize.bypass_reason(short), "too short")
        self.assertEqual(
            summarize.summarize(short, self.rules), sentences_of(short)
        )

    def test_three_sentences_are_left_alone(self):
        """Long enough to clear min_chars, so the sentence rule is the one
        under test rather than being masked by it."""
        self.assertEqual(len(sentences_of(THREE_LONG_SENTENCES)), 3)
        self.assertGreaterEqual(len(THREE_LONG_SENTENCES), 400)
        self.assertEqual(
            summarize.bypass_reason(THREE_LONG_SENTENCES), "too few sentences"
        )
        self.assertEqual(
            summarize.summarize(THREE_LONG_SENTENCES, self.rules),
            sentences_of(THREE_LONG_SENTENCES),
        )

    def test_fifty_nine_words_are_left_alone_and_sixty_are_not(self):
        """Long words, so the text clears 400 characters well before it clears
        60 words and the word rule is still reachable."""
        filler = (
            "Comprehensive infrastructure reconfiguration documentation "
            "requires substantial administrative coordination. "
        )
        under = ""
        while words_in(under + filler) <= 59:
            under += filler
        self.assertLessEqual(words_in(under), 59)
        self.assertGreaterEqual(len(sentences_of(under)), 4)
        self.assertGreaterEqual(len(under), 400, "min_chars would mask this")
        self.assertEqual(summarize.bypass_reason(under), "too few words")

        over = under + filler + filler
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

    def test_k_clamps_at_the_budget_floor_and_ceiling(self):
        """Defaults come from settings now: ratio 0.2, floor 3, ceiling 12."""
        self.assertEqual(summarize.target_count(1), 3)
        self.assertEqual(summarize.target_count(4), 3)
        self.assertEqual(summarize.target_count(15), 3)
        self.assertEqual(summarize.target_count(16), 4)
        self.assertEqual(summarize.target_count(40), 8)
        self.assertEqual(summarize.target_count(60), 12)
        self.assertEqual(summarize.target_count(4000), 12)

    def test_the_ceiling_no_longer_starves_a_long_document(self):
        """The reason the ceiling moved: 8 was pinning a 104-sentence read."""
        self.assertEqual(summarize.target_count(104), 12)

    def test_a_custom_budget_is_honoured_end_to_end(self):
        tight = summarize.Budget(ratio=0.5, min_sentences=1, max_sentences=2,
                                 min_chars=0)
        self.assertEqual(summarize.target_count(10, tight), 2)
        picks = summarize.summarize(CORPUS["escalation"], self.rules,
                                    budget=tight)
        self.assertEqual(len(picks), 2)

    def test_the_ratio_does_not_drift_on_floating_point(self):
        """15 * 0.2 is 3.0000000000000004 in binary; ceil() must not see that."""
        self.assertEqual(summarize.target_count(15), 3)
        for n in range(1, 400):
            budget = summarize.Budget(ratio=0.2, min_sentences=1,
                                      max_sentences=9999, min_chars=0)
            self.assertEqual(
                summarize.target_count(n, budget),
                -(-n // 5),
                f"drifted at n={n}",
            )

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

        # A budget of its own: this test is about the header weight, and a
        # floor high enough to select every candidate would hide the effect
        # behind a k that keeps them all regardless of score.
        budget = summarize.Budget(ratio=0.2, min_sentences=2, max_sentences=8,
                                  min_chars=0)

        def picked_indexes(rules):
            scores = summarize.rank_sentences(sentences, rules)
            eligible = summarize.eligible_indexes(sentences)
            keep = summarize.target_count(len(eligible), budget)
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


class ActionSignal(unittest.TestCase):
    """Instructions have to survive. A complaint plus its fix should surface the
    fix, and pain vocabulary scores an imperative at exactly zero."""

    def setUp(self):
        self.rules = fresh_rules()

    def test_an_imperative_opening_scores(self):
        for line in (
            "Open chrome://extensions and turn on Developer mode.",
            "Click Load unpacked and select the extension folder.",
            "Run the tests before you push anything.",
            "Restart the reader so it picks up the change.",
        ):
            self.assertGreater(summarize.action_score(line, self.rules), 0.0, line)

    def test_directive_phrases_and_numbered_steps_score(self):
        for line in (
            "You need to restart the reader before it picks up the change.",
            "Make sure the tray icon is visible before you continue.",
            "3. Press Ctrl+Alt+S to turn summary mode on.",
            "Step 2 opens the settings file in your editor.",
        ):
            self.assertGreater(summarize.action_score(line, self.rules), 0.0, line)

    def test_ordinary_prose_is_not_mistaken_for_an_instruction(self):
        """Only the opening verb counts. Counting every "run" and "set" made
        plain description look like a checklist."""
        for line in (
            "The attendance figures held steady across the whole quarter.",
            "Running the tests took about two minutes in total.",
            "The client has asked three times and wants a refund.",
            "It costs four thousand a month to run both environments.",
        ):
            self.assertEqual(summarize.action_score(line, self.rules), 0.0, line)

    def test_an_instruction_beats_a_sentence_carried_by_frequency_alone(self):
        """The 16% filler class in the real log: picks with no cue signal at
        all, chosen purely for repeating the document's vocabulary."""
        sentences = sentences_of(INSTRUCTIONS)
        _scores, breakdown = summarize.score_detail(sentences, self.rules)
        scores = summarize.rank_sentences(sentences, self.rules)

        instructions = [i for i, s in enumerate(breakdown) if s["action"] > 0]
        filler = [i for i, s in enumerate(breakdown)
                  if not any(s[p] for p in summarize.CUE_PARTS)]
        self.assertTrue(instructions, "fixture has no instructions")
        if filler:
            self.assertGreater(max(scores[i] for i in instructions),
                               max(scores[i] for i in filler))

    def test_an_instruction_reaches_the_summary(self):
        picked = summarize.summarize(INSTRUCTIONS, self.rules)
        self.assertTrue(
            any(summarize.action_score(p, self.rules) > 0 for p in picked),
            f"every instruction was cut: {picked}",
        )

    def test_turning_the_weight_off_puts_it_back_the_way_it_was(self):
        deaf = dict(self.rules, weights=dict(self.rules["weights"], action_word=0.0))
        sentences = sentences_of(INSTRUCTIONS)
        self.assertNotEqual(
            summarize.rank_sentences(sentences, self.rules),
            summarize.rank_sentences(sentences, deaf),
            "action_word changed nothing",
        )

    def test_the_action_score_is_logged_as_its_own_field(self):
        log = Path(tempfile.mkdtemp()) / "summary_log.jsonl"
        summarize.summarize(INSTRUCTIONS, self.rules, source="hotkey", log_path=log)
        record = json.loads(log.read_text("utf-8").splitlines()[0])
        for entry in record["picked"] + record["near_misses"]:
            self.assertIn("action", entry)


class NearMisses(unittest.TestCase):
    """What lost, and by how much."""

    def setUp(self):
        self.rules = fresh_rules()
        self.log = Path(tempfile.mkdtemp()) / "summary_log.jsonl"

    def _record(self, text=None):
        summarize.summarize(text or INSTRUCTIONS, self.rules,
                            source="hotkey", log_path=self.log)
        return json.loads(self.log.read_text("utf-8").splitlines()[-1])

    def test_the_losers_are_recorded_beside_the_winners(self):
        record = self._record()
        self.assertTrue(record["near_misses"])
        self.assertLessEqual(len(record["near_misses"]), summarize.NEAR_MISS_COUNT)

    def test_no_sentence_is_both_picked_and_a_near_miss(self):
        record = self._record()
        picked = {p["index"] for p in record["picked"]}
        missed = {p["index"] for p in record["near_misses"]}
        self.assertEqual(picked & missed, set())

    def test_every_near_miss_scored_below_every_pick(self):
        """Otherwise the margin means nothing."""
        record = self._record()
        worst_pick = min(p["score"] for p in record["picked"])
        best_miss = max(p["score"] for p in record["near_misses"])
        self.assertGreaterEqual(worst_pick, best_miss)

    def test_near_misses_come_in_descending_score_order(self):
        record = self._record()
        scores = [p["score"] for p in record["near_misses"]]
        self.assertEqual(scores, sorted(scores, reverse=True))

    def test_a_bypass_records_no_near_misses(self):
        record = self._record(CODE_FIXTURE)
        self.assertEqual(record["near_misses"], [])
        self.assertEqual(record["picked"], [])

    def test_near_miss_text_obeys_the_same_privacy_rule_as_picks(self):
        """The invariant is about the whole line, not one key: no sentence of
        the source may appear anywhere in it."""
        self._record()
        raw = self.log.read_text("utf-8")
        for sentence in sentences_of(INSTRUCTIONS):
            self.assertNotIn(sentence.strip(), raw)


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

    def test_an_older_file_is_merged_so_hand_tuning_survives(self):
        """The file exists to be tuned. Replacing it on a version bump threw
        that away; the words somebody added by hand have to come through."""
        path = Path(tempfile.mkdtemp()) / "summary_rules.json"
        stale = {
            "version": 1,
            "pain_words": ["no", "not", "error", "gearbox"],
            "weights": {"pain_word": 2.5, "textrank_blend": 0.4},
        }
        path.write_text(json.dumps(stale), "utf-8")

        rules = summarize.load_rules(path)
        self.assertIn("gearbox", rules["pain_words"], "a hand-added word was lost")
        self.assertEqual(rules["weights"]["pain_word"], 2.5, "a tuned weight was lost")
        self.assertIn("waiting", rules["pain_words"], "new defaults were not added")
        self.assertIn("action_words", rules)

        # One thing is repaired rather than carried forward: v1 listed these as
        # pain words and they are now negators. Kept, every negated sentence
        # would score as pain -- what the window exists to prevent.
        self.assertNotIn("no", rules["pain_words"])
        self.assertNotIn("not", rules["pain_words"])

        self.assertEqual(
            json.loads(path.read_text("utf-8"))["version"], summarize.RULES_VERSION
        )

    def test_the_upgrade_says_what_it_added(self):
        path = Path(tempfile.mkdtemp()) / "summary_rules.json"
        path.write_text(json.dumps({"version": 1, "pain_words": ["error"]}), "utf-8")
        summarize.load_rules(path)
        self.assertTrue(summarize.upgrade_notes, "an upgrade happened in silence")
        self.assertIn("summary_rules.json", summarize.upgrade_notes[0])

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
        self.assertIn("action_word", rules["weights"])
        self.assertTrue(rules["action_words"], "the action vocabulary is empty")
        # The user's own entries survive the upgrade.
        self.assertIn("failed", rules["pain_words"])

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
        "timestamp", "event", "source", "sentence_count", "word_count",
        "bypass_reason", "k", "picked", "near_misses",
    }
    SIGNALS = {
        "index", "score", "pain", "action", "negation_hits",
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

        for entry in record["picked"] + record["near_misses"]:
            self.assertEqual(set(entry), self.SIGNALS, "unexpected keys in an entry")
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
        fixtures = (SHORT_THREE_SENTENCES, THREE_LONG_SENTENCES, CODE_FIXTURE,
                    URL_FIXTURE, LIST_FIXTURE)
        for text in fixtures:
            summarize.summarize(text, self.rules, source="hotkey", log_path=self.log)

        records = self._lines()
        self.assertEqual(len(records), len(fixtures))
        self.assertEqual(
            [r["bypass_reason"] for r in records],
            ["too short", "too few sentences", "code", "url", "list"],
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


# A price list that survived sentence splitting: evenly spaced, every line
# carrying a figure. The number bonus makes these the top picks, and reading
# three arbitrary rows aloud is worse than reading the table.
TABLE_FIXTURE = (
    "The Q3 hardware refresh budget is broken down by site below. "
    "Fort Lauderdale ordered 42 units at $1,150 each in January. "
    "The rollout there finished ahead of the internal schedule. "
    "Charlotte ordered 38 units at $1,150 each in February. "
    "The rollout there finished ahead of the internal schedule. "
    "Nashville ordered 51 units at $1,150 each in March. "
    "The rollout there finished ahead of the internal schedule. "
    "Richmond ordered 47 units at $1,150 each in April. "
    "The rollout there finished ahead of the internal schedule. "
    "Totals will be reconciled once the last invoice clears."
)

# Ordinary prose that happens to quote two figures. Must not read as a table.
TWO_NUMBERS_PROSE = (
    "The reporting rebuild slipped again this week and nobody has told the "
    "client yet, which is the part that worries me most. The schema sign-off "
    "is still sitting with the data team and has not moved in nine days. "
    "We are paying $4,000 a month to keep both environments alive while this "
    "drags on, which is money we agreed to stop spending in June. Everyone "
    "involved agrees the current arrangement cannot continue much longer. "
    "The deadline was Friday and it is already Tuesday afternoon. Somebody "
    "needs to make a call today about what ships and what waits."
)


class BriefInterface(unittest.TestCase):
    """One scorer, reached through the interface both callers hold."""

    def setUp(self):
        self.rules = fresh_rules()
        self.log = Path(tempfile.mkdtemp()) / "summary_log.jsonl"

    def make(self, source="test", budget=None, clock=None):
        return summarize.ExtractiveSummarizer(
            log_path=self.log, source=source, rules=self.rules,
            budget=budget, clock=clock,
        )

    def test_the_shipped_summarizer_is_the_interface(self):
        self.assertTrue(issubclass(summarize.ExtractiveSummarizer,
                                   summarize.Summarizer))

    def test_a_result_reports_both_counts(self):
        text = CORPUS["escalation"]
        result = self.make().summarize(text)
        self.assertEqual(result.n_input, len(sentences_of(text)))
        self.assertEqual(result.n_output, len(result.sentences))
        self.assertLess(result.n_output, result.n_input)
        self.assertTrue(result.summarized)
        self.assertIsNone(result.bypass)

    def test_an_unknown_source_is_refused(self):
        """"unknown" was retired: a row that cannot say where it came from is
        untunable, so it must not be constructible."""
        with self.assertRaises(ValueError):
            self.make(source="unknown")
        for legal in summarize.SOURCES:
            self.make(source=legal)

    def test_output_preserves_document_order(self):
        text = CORPUS["escalation"]
        result = self.make().summarize(text)
        original = sentences_of(text)
        positions = [original.index(s) for s in result.sentences]
        self.assertEqual(positions, sorted(positions))

    def test_every_output_sentence_appears_verbatim_in_the_input(self):
        """The property that makes the privacy claim checkable: extractive
        means chosen, never written."""
        for name, text in CORPUS.items():
            result = self.make().summarize(text)
            original = sentences_of(text)
            for sentence in result.sentences:
                self.assertIn(sentence, original, f"{name} invented a sentence")

    def test_empty_and_whitespace_only_do_not_crash(self):
        for text in ("", "   ", "\n\n\t  \n"):
            result = self.make().summarize(text)
            self.assertEqual(result.bypass, "empty")
            self.assertEqual(result.sentences, [])
            self.assertEqual(result.n_input, 0)

    def test_a_short_passage_comes_back_untouched(self):
        short = "The build failed. Nobody knows why. We are blocked."
        result = self.make().summarize(short)
        self.assertEqual(result.bypass, "too short")
        self.assertEqual(result.sentences, sentences_of(short))

    def test_a_brief_that_saves_less_than_two_sentences_is_refused(self):
        """Not meaningfully shorter is noise too."""
        text = CORPUS["escalation"]
        sentences = sentences_of(text)
        # A floor high enough that k lands within one of the input count.
        budget = summarize.Budget(
            ratio=1.0, min_sentences=len(sentences) - 1,
            max_sentences=len(sentences), min_chars=0,
        )
        result = self.make(budget=budget).summarize(text)
        self.assertEqual(result.bypass, "not shorter")
        self.assertEqual(result.sentences, sentences)


class TableGuard(unittest.TestCase):
    """Evenly spaced numeric picks are a table read as prose."""

    def setUp(self):
        self.rules = fresh_rules()
        self.log = Path(tempfile.mkdtemp()) / "summary_log.jsonl"

    def make(self):
        return summarize.ExtractiveSummarizer(
            log_path=self.log, source="test", rules=self.rules
        )

    def test_a_strided_numeric_document_is_bypassed_as_a_table(self):
        result = self.make().summarize(TABLE_FIXTURE)
        self.assertEqual(result.bypass, "table")
        self.assertEqual(result.sentences, sentences_of(TABLE_FIXTURE))

    def test_ordinary_prose_with_two_numbers_is_still_summarized(self):
        result = self.make().summarize(TWO_NUMBERS_PROSE)
        self.assertIsNone(result.bypass, "the table guard fired on prose")
        self.assertLess(result.n_output, result.n_input)

    def test_the_guard_needs_both_conditions(self):
        numeric = [{"number": 0.7} for _ in range(6)]
        plain = [{"number": 0.0} for _ in range(6)]
        # Strided and numeric: a table.
        self.assertTrue(summarize.looks_like_table([0, 2, 4], numeric))
        # Strided but not numeric: prose that happens to be evenly spread.
        self.assertFalse(summarize.looks_like_table([0, 2, 4], plain))
        # Numeric but clustered: prose that quotes its figures together.
        self.assertFalse(summarize.looks_like_table([0, 1, 5], numeric))

    def test_two_picks_are_never_a_table(self):
        """One gap is always regular; two picks cannot establish a stride."""
        numeric = [{"number": 0.7} for _ in range(6)]
        self.assertFalse(summarize.looks_like_table([0, 3], numeric))


class Duplicates(unittest.TestCase):
    """The same text twice inside the window is not briefed twice."""

    def setUp(self):
        self.rules = fresh_rules()
        self.log = Path(tempfile.mkdtemp()) / "summary_log.jsonl"
        self.now = 1000.0

    def make(self, source="test"):
        return summarize.ExtractiveSummarizer(
            log_path=self.log, source=source, rules=self.rules,
            clock=lambda: self.now,
        )

    def test_a_repeat_inside_the_window_is_bypassed(self):
        scorer = self.make()
        first = scorer.summarize(CORPUS["escalation"])
        self.assertIsNone(first.bypass)

        self.now += 60
        second = scorer.summarize(CORPUS["escalation"])
        self.assertEqual(second.bypass, "duplicate")
        self.assertEqual(second.sentences, sentences_of(CORPUS["escalation"]))

    def test_after_the_window_it_is_briefed_again(self):
        scorer = self.make()
        first = scorer.summarize(CORPUS["escalation"])
        self.now += summarize.DUPLICATE_TTL_SECONDS + 1
        again = scorer.summarize(CORPUS["escalation"])
        self.assertIsNone(again.bypass, "the window did not expire")
        self.assertEqual(again.sentences, first.sentences)

    def test_a_different_text_is_not_a_duplicate(self):
        scorer = self.make()
        scorer.summarize(CORPUS["escalation"])
        other = scorer.summarize(CORPUS["meeting"])
        self.assertIsNone(other.bypass)

    def test_the_duplicate_is_logged_with_its_reason(self):
        scorer = self.make()
        scorer.summarize(CORPUS["escalation"])
        scorer.summarize(CORPUS["escalation"])
        rows = [json.loads(line)
                for line in self.log.read_text("utf-8").splitlines() if line.strip()]
        self.assertEqual([r["bypass_reason"] for r in rows], [None, "duplicate"])

    def test_the_window_does_not_grow_without_bound(self):
        scorer = self.make()
        for i in range(5):
            scorer.summarize(f"{CORPUS['escalation']} Variation {i}.")
            self.now += summarize.DUPLICATE_TTL_SECONDS + 1
        self.assertLessEqual(len(scorer._seen), 1, "stale entries were kept")

    def test_the_cache_holds_no_text(self):
        """A cache of what you have read is a reading history; this one is
        hashes only."""
        scorer = self.make()
        scorer.summarize(CORPUS["escalation"])
        for key in scorer._seen:
            self.assertRegex(key, r"^[0-9a-f]{64}$")


class BudgetFromSettings(unittest.TestCase):
    """The budget lives in settings.json, and both paths read it."""

    def test_defaults_match_the_settings_defaults(self):
        budget = summarize.Budget.from_settings(settings.BRIEF_DEFAULTS)
        self.assertEqual(budget.ratio, 0.2)
        self.assertEqual(budget.min_sentences, 3)
        self.assertEqual(budget.max_sentences, 12)
        self.assertEqual(budget.min_chars, 400)

    def test_a_settings_budget_changes_how_much_is_kept(self):
        rules = fresh_rules()
        log = Path(tempfile.mkdtemp()) / "summary_log.jsonl"
        wide = summarize.Budget.from_settings(
            {"ratio": 0.9, "min_sentences": 1, "max_sentences": 99, "min_chars": 0}
        )
        narrow = summarize.Budget.from_settings(
            {"ratio": 0.1, "min_sentences": 1, "max_sentences": 2, "min_chars": 0}
        )
        text = CORPUS["escalation"]
        many = summarize.ExtractiveSummarizer(
            log_path=log, source="test", rules=rules, budget=wide
        ).summarize(text)
        few = summarize.ExtractiveSummarizer(
            log_path=log, source="test", rules=rules, budget=narrow
        ).summarize(text)
        self.assertGreater(many.n_output, few.n_output)


class LogHygiene(unittest.TestCase):
    """The log is scores, not a transcript."""

    ALLOWED_STRINGS = {
        "hotkey", "queue", "app", "test", "summary", "weights", "extractive",
        "ollama", "url", "code", "list", "table", "duplicate", "empty",
        "too short", "too few sentences", "too few words", "nothing quotable",
        "not shorter",
    }

    def setUp(self):
        self.rules = fresh_rules()
        self.log = Path(tempfile.mkdtemp()) / "summary_log.jsonl"
        # This asserts the default contract, so it must not depend on whether
        # the machine running it has the opt-in text logging switched on.
        self._real = summarize._log_sentence_text
        summarize._log_sentence_text = lambda: False

    def tearDown(self):
        summarize._log_sentence_text = self._real

    def _rows(self):
        return [json.loads(line)
                for line in self.log.read_text("utf-8").splitlines() if line.strip()]

    def _values(self, value, found):
        """Every string *value* in the row. Keys are structure, not content:
        a key named "budget" is the schema, and the contract is that values
        carry nothing but numbers and the enumerated strings."""
        if isinstance(value, dict):
            for item in value.values():
                self._values(item, found)
        elif isinstance(value, list):
            for item in value:
                self._values(item, found)
        elif isinstance(value, str):
            found.add(value)
        return found

    def _keys(self, value, found):
        if isinstance(value, dict):
            for key, item in value.items():
                found.add(key)
                self._keys(item, found)
        elif isinstance(value, list):
            for item in value:
                self._keys(item, found)
        return found

    def test_no_row_carries_input_or_summary_text(self):
        scorer = summarize.ExtractiveSummarizer(
            log_path=self.log, source="hotkey", rules=self.rules
        )
        summarize.log_weight_set(self.log, "hotkey", rules=self.rules)
        for text in (CORPUS["escalation"], CORPUS["meeting"], CODE_FIXTURE,
                     URL_FIXTURE, TABLE_FIXTURE, SHORT_THREE_SENTENCES):
            scorer.summarize(text)
        scorer.summarize(CORPUS["escalation"])  # and a duplicate

        rows = self._rows()
        self.assertGreater(len(rows), 5)

        # No sentence of any fixture may appear anywhere in the log, and no
        # value may be a string the schema does not enumerate.
        corpus_sentences = set()
        for text in (CORPUS["escalation"], CORPUS["meeting"], TABLE_FIXTURE):
            for sentence in sentences_of(text):
                corpus_sentences.add(sentence.lower())

        for row in rows:
            for value in self._values(row, set()):
                if value in self.ALLOWED_STRINGS:
                    continue
                # Timestamps are the only other free-form string value.
                if re.fullmatch(r"[0-9T:.+\-]{10,40}Z?", value):
                    continue
                self.fail(f"unenumerated string value in log: {value[:40]!r}")

        # And nothing anywhere in the row -- key or value -- is a sentence or a
        # distinctive word out of the text that was scored.
        for row in rows:
            everything = self._values(row, set()) | self._keys(row, set())
            for token in everything:
                self.assertNotIn(
                    token.lower(), corpus_sentences,
                    f"log row leaked a sentence: {token[:30]!r}",
                )

    def test_every_row_names_a_legal_source(self):
        """A row emitted without an explicit source is a bug."""
        scorer = summarize.ExtractiveSummarizer(
            log_path=self.log, source="queue", rules=self.rules
        )
        summarize.log_weight_set(self.log, "queue", rules=self.rules)
        scorer.summarize(CORPUS["escalation"])
        scorer.summarize(SHORT_THREE_SENTENCES)
        for row in self._rows():
            self.assertIn("source", row, "row had no source")
            self.assertIn(row["source"], summarize.SOURCES)
            self.assertNotEqual(row["source"], "unknown")

    def test_near_misses_are_still_recorded(self):
        """The tuning data the amendment asked to keep exactly as it was."""
        scorer = summarize.ExtractiveSummarizer(
            log_path=self.log, source="test", rules=self.rules
        )
        scorer.summarize(CORPUS["escalation"])
        row = self._rows()[0]
        self.assertIn("near_misses", row)
        self.assertTrue(row["near_misses"])
        for entry in row["near_misses"]:
            self.assertIn("index", entry)
            self.assertIn("score", entry)
            self.assertNotIn("text", entry)


class StartupWeightLog(unittest.TestCase):
    """Which weights the rest of the log was scored with, written once."""

    def setUp(self):
        self.rules = fresh_rules()
        self.log = Path(tempfile.mkdtemp()) / "summary_log.jsonl"

    def test_the_weight_set_is_recorded(self):
        record = summarize.log_weight_set(self.log, "hotkey", rules=self.rules)
        self.assertEqual(record["event"], "weights")
        self.assertEqual(record["source"], "hotkey")
        self.assertEqual(
            set(record["weights"]), set(self.rules["weights"])
        )
        self.assertEqual(record["budget"]["max_sentences"], 12)

    def test_it_is_one_row_and_is_not_a_call(self):
        summarize.log_weight_set(self.log, "hotkey", rules=self.rules)
        rows = [json.loads(line)
                for line in self.log.read_text("utf-8").splitlines() if line.strip()]
        self.assertEqual(len(rows), 1)
        self.assertNotIn("picked", rows[0])
        self.assertNotIn("bypass_reason", rows[0])

    def test_scoring_rows_are_marked_apart_from_it(self):
        summarize.log_weight_set(self.log, "hotkey", rules=self.rules)
        summarize.ExtractiveSummarizer(
            log_path=self.log, source="hotkey", rules=self.rules
        ).summarize(CORPUS["escalation"])
        rows = [json.loads(line)
                for line in self.log.read_text("utf-8").splitlines() if line.strip()]
        self.assertEqual([r["event"] for r in rows], ["weights", "summary"])


class BriefSettingsFile(unittest.TestCase):
    """The budget section: absent, malformed, or hand-edited."""

    def setUp(self):
        self.real = settings.SETTINGS_PATH
        self.path = Path(tempfile.mkdtemp()) / "settings.json"
        settings.SETTINGS_PATH = self.path

    def tearDown(self):
        settings.SETTINGS_PATH = self.real

    def write(self, payload):
        self.path.write_text(json.dumps(payload), "utf-8")

    def test_a_missing_file_gives_the_defaults(self):
        values = settings.load()
        self.assertEqual(values["brief"], settings.BRIEF_DEFAULTS)
        self.assertEqual(settings.warnings, [])

    def test_a_missing_brief_section_gives_the_defaults(self):
        self.write({"voice": "Zira", "rate": 2})
        values = settings.load()
        self.assertEqual(values["brief"], settings.BRIEF_DEFAULTS)
        self.assertEqual(values["voice"], "Zira", "unrelated keys were lost")
        self.assertEqual(settings.warnings, [])

    def test_a_partial_brief_section_keeps_the_rest_of_the_defaults(self):
        self.write({"brief": {"max_sentences": 6}})
        brief = settings.load()["brief"]
        self.assertEqual(brief["max_sentences"], 6)
        self.assertEqual(brief["ratio"], settings.BRIEF_DEFAULTS["ratio"])
        self.assertEqual(brief["min_chars"], settings.BRIEF_DEFAULTS["min_chars"])
        self.assertEqual(settings.warnings, [])

    def test_a_malformed_value_falls_back_for_that_key_alone(self):
        self.write({"brief": {"ratio": "banana", "max_sentences": 7}})
        brief = settings.load()["brief"]
        self.assertEqual(brief["ratio"], settings.BRIEF_DEFAULTS["ratio"],
                         "the bad key did not fall back")
        self.assertEqual(brief["max_sentences"], 7,
                         "a good key was thrown away with the bad one")
        self.assertTrue(any("ratio" in w for w in settings.warnings),
                        "the rejection was not logged")

    def test_an_out_of_range_ratio_is_rejected(self):
        for bad in (0, -0.5, 1.5):
            self.write({"brief": {"ratio": bad}})
            brief = settings.load()["brief"]
            self.assertEqual(brief["ratio"], settings.BRIEF_DEFAULTS["ratio"],
                             f"ratio {bad} was accepted")
            self.assertTrue(settings.warnings)

    def test_a_negative_sentence_count_is_rejected(self):
        self.write({"brief": {"min_sentences": 0, "max_sentences": -4}})
        brief = settings.load()["brief"]
        self.assertEqual(brief["min_sentences"],
                         settings.BRIEF_DEFAULTS["min_sentences"])
        self.assertEqual(brief["max_sentences"],
                         settings.BRIEF_DEFAULTS["max_sentences"])
        self.assertEqual(len(settings.warnings), 2)

    def test_an_inverted_pair_is_straightened_out(self):
        self.write({"brief": {"min_sentences": 9, "max_sentences": 4}})
        brief = settings.load()["brief"]
        self.assertGreaterEqual(brief["max_sentences"], brief["min_sentences"])
        self.assertTrue(settings.warnings)

    def test_a_brief_section_of_the_wrong_shape_does_not_crash(self):
        for junk in ("nonsense", 12, [1, 2, 3]):
            self.write({"brief": junk})
            brief = settings.load()["brief"]
            self.assertEqual(brief, settings.BRIEF_DEFAULTS)
            self.assertTrue(settings.warnings)

    def test_a_file_that_is_not_json_at_all_falls_back(self):
        self.path.write_text("{ this is not json", "utf-8")
        self.assertEqual(settings.load()["brief"], settings.BRIEF_DEFAULTS)

    def test_zero_min_chars_is_allowed(self):
        """Legitimate: it turns the short-text bypass off."""
        self.write({"brief": {"min_chars": 0}})
        self.assertEqual(settings.load()["brief"]["min_chars"], 0)
        self.assertEqual(settings.warnings, [])

    def test_saving_does_not_disturb_the_brief_section(self):
        self.write({"brief": {"max_sentences": 5}, "voice": "Zira"})
        values = settings.load()
        settings.save(values)
        again = settings.load()
        self.assertEqual(again["brief"]["max_sentences"], 5)
        self.assertEqual(again["voice"], "Zira")


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
