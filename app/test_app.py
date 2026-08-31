"""Functional tests that drive the real Tk window.

A window flashes on screen while these run. Speech happens at low volume.

    python -m unittest test_app -v
"""

import time
import unittest

import reading
import tts_app
from tts_app import ReadAloudApp

SAMPLE = (
    "The first sentence is short. The second sentence is a little longer than "
    "the first one.\n\nAnd this is a third, in its own paragraph."
)


# Past the 4-sentence / 60-word bypass, so summary mode actually engages.
LONG_SAMPLE = (
    "The migration failed twice overnight before it finally finished. We are "
    "blocked on the reporting rebuild until someone signs off on the schema. "
    "The client has asked three separate times now and is threatening to ask "
    "for a refund. Nobody has been able to reproduce the error on staging at "
    "all. It costs $4000 a month to keep both environments alive while this "
    "drags on. The deadline was Friday and it is already Tuesday afternoon. "
    "Can we please get a decision today? Everything else in the release is "
    "ready and waiting."
)


_REAL_LOG = None
_REAL_SETTINGS = None


def setUpModule():
    """Keep the score log out of the user's real %APPDATA% while testing."""
    global _REAL_LOG, _REAL_SETTINGS
    import tempfile
    from pathlib import Path as _Path
    import summarize as _s
    import json
    import settings
    _REAL_LOG = _s.default_log_path
    scratch = _Path(tempfile.mkdtemp()) / "summary_log.jsonl"
    _s.default_log_path = lambda: scratch

    # The suite drives the real app, and changing voice, speed or volume
    # persists through settings.save() to the shared file the installed copy
    # reads. A test run must never leave somebody's reader muted, so the whole
    # suite writes to a scratch settings file instead of %APPDATA%.
    _REAL_SETTINGS = settings.SETTINGS_PATH
    scratch_settings = _Path(tempfile.mkdtemp()) / "settings.json"
    scratch_settings.write_text(
        json.dumps({k: v for k, v in settings.DEFAULTS.items()}), "utf-8"
    )
    settings.SETTINGS_PATH = scratch_settings


def tearDownModule():
    import summarize as _s
    import settings
    _s.default_log_path = _REAL_LOG
    settings.SETTINGS_PATH = _REAL_SETTINGS


def pump(app, seconds, until=None):
    """Run the Tk event loop for a while, stopping early if `until` becomes true."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        app.update()
        if until is not None and until():
            return True
        time.sleep(0.02)
    return until() if until else False


class SummaryPane(unittest.TestCase):
    """Summary mode in the desktop window: what the reading pane shows, and
    that the original never goes anywhere."""

    def setUp(self):
        self.app = ReadAloudApp()
        self.app.update()
        pump(self.app, 10, lambda: bool(self.app.voice_box.cget("values")))
        self.app.volume_var.set(12)
        self.app._on_volume_change()
        self.app.rate_var.set(8)
        self.app._on_rate_change()
        self.app.settings["summary_mode"] = False
        self._fail_on_swallowed_callbacks()
        self.app.update()

    def tearDown(self):
        self.app.settings["summary_mode"] = False
        self.app._on_close()
        self._assert_no_swallowed_callbacks()

    def _fail_on_swallowed_callbacks(self):
        """Make Tk stop eating exceptions.

        An exception raised inside an after() callback goes to
        report_callback_exception, which prints it and carries on. In this suite
        that turned a stale test double into a queue that simply stopped
        draining, with nothing in the failure to say why. Collected here and
        asserted in tearDown, so the next one is a named failure instead.
        """
        self.callback_errors = []
        self.app.report_callback_exception = (
            lambda exc, value, tb: self.callback_errors.append((exc, value))
        )

    def _assert_no_swallowed_callbacks(self):
        if getattr(self, "callback_errors", None):
            exc, value = self.callback_errors[0]
            raise AssertionError(
                f"{len(self.callback_errors)} exception(s) were raised inside Tk "
                f"callbacks and swallowed; first was {exc.__name__}: {value}"
            )

    def _pane(self):
        return self.app.text.get("1.0", "end-1c")

    def test_the_pane_shows_the_summary_and_the_source_holds_the_original(self):
        self.app.settings["summary_mode"] = True
        self.app._set_text(LONG_SAMPLE)
        self.app.update()

        self.app.read()
        self.app.update()

        pane = self._pane()
        self.assertNotEqual(pane, LONG_SAMPLE, "the pane still shows the original")
        self.assertEqual(self.app._source, LONG_SAMPLE)
        self.assertEqual(self.app.source_box.get("1.0", "end-1c"), LONG_SAMPLE)

        full = reading.plan(LONG_SAMPLE, summary=False).sentences
        for _s, _e, piece in self.app.pieces:
            self.assertIn(piece, full, "a sentence was invented")
        self.app.stop()

    def test_the_summary_spans_line_up_with_the_pane_for_highlighting(self):
        self.app.settings["summary_mode"] = True
        self.app._set_text(LONG_SAMPLE)
        self.app.update()
        self.app.read()
        self.app.update()

        pane = self._pane()
        for start, end, piece in self.app.pieces:
            self.assertEqual(pane[start:end], piece, "highlight would land wrong")
        self.app.stop()

    def test_the_source_section_starts_collapsed_and_opens_on_request(self):
        self.app.settings["summary_mode"] = True
        self.app._set_text(LONG_SAMPLE)
        self.app.update()
        self.app.read()
        self.app.update()
        self.app.stop()

        self.assertFalse(self.app.source_open)
        self.assertFalse(self.app.source_box.winfo_ismapped())
        self.assertIn("Source", self.app.source_btn.cget("text"))

        self.app.toggle_source()
        self.app.update()
        self.assertTrue(self.app.source_open)
        self.assertTrue(self.app.source_box.winfo_ismapped())

        self.app.toggle_source()
        self.app.update()
        self.assertFalse(self.app.source_open)
        self.assertFalse(self.app.source_box.winfo_ismapped())

    def test_read_full_text_puts_the_original_back_and_reads_all_of_it(self):
        self.app.settings["summary_mode"] = True
        self.app._set_text(LONG_SAMPLE)
        self.app.update()
        self.app.read()
        self.app.update()
        self.app.stop()
        self.assertNotEqual(self._pane(), LONG_SAMPLE)

        self.app.read_full_text()
        self.app.update()
        self.assertEqual(self._pane(), LONG_SAMPLE)
        self.assertEqual(self.app._source, "")
        self.assertFalse(self.app.source_frame.winfo_ismapped())
        self.assertEqual(
            [p for _s, _e, p in self.app.pieces],
            reading.plan(LONG_SAMPLE, summary=False).sentences,
            "the full text was not what got read",
        )
        self.assertFalse(self.app.cue_pending, "a cue was armed for untrimmed text")
        self.app.stop()

    def _record_engine(self):
        """What SAPI is actually handed. The cue is consumed by the first
        utterance inside read(), so the flag is already gone by the time a test
        could look at it -- the utterance is the only honest witness."""
        uttered = []
        original = self.app.engine.speak

        def recording(text):
            uttered.append(text)
            original(text)

        self.app.engine.speak = recording
        return uttered

    def test_the_cue_is_spoken_for_a_summary_and_not_for_a_bypass(self):
        self.app.settings["summary_mode"] = True
        uttered = self._record_engine()

        self.app._set_text(LONG_SAMPLE)
        self.app.update()
        self.app.read()
        self.assertTrue(uttered)
        self.assertTrue(uttered[0].startswith(reading.CUE), uttered[0])
        self.app.stop()

        uttered.clear()
        self.app._set_text(SAMPLE)  # too short: bypasses
        self.app.update()
        self.app.read()
        self.assertTrue(uttered)
        self.assertFalse(uttered[0].startswith(reading.CUE), "cued a bypass")
        self.app.stop()

    def test_the_cue_does_not_disturb_the_highlight_offsets(self):
        """The cue is prefixed to the utterance, never inserted into pieces."""
        self.app.settings["summary_mode"] = True
        self.app._set_text(LONG_SAMPLE)
        self.app.update()
        self.app.read()
        self.app.update()

        pane = self.app.text.get("1.0", "end-1c")
        for start, end, piece in self.app.pieces:
            self.assertEqual(pane[start:end], piece)
            self.assertNotIn(reading.CUE, piece)
        self.app.stop()

    def test_summary_mode_off_leaves_the_pane_and_the_source_alone(self):
        self.app.settings["summary_mode"] = False
        self.app._set_text(LONG_SAMPLE)
        self.app.update()
        self.app.read()
        self.app.update()

        self.assertEqual(self._pane(), LONG_SAMPLE)
        self.assertEqual(self.app._source, "")
        self.assertFalse(self.app.source_frame.winfo_ismapped())
        self.app.stop()

    def test_a_bypass_changes_nothing_on_screen(self):
        """Short text with the mode on must look exactly like the mode off."""
        self.app.settings["summary_mode"] = True
        self.app._set_text(SAMPLE)
        self.app.update()
        self.app.read()
        self.app.update()

        self.assertEqual(self._pane(), SAMPLE)
        self.assertEqual(self.app._source, "")
        self.assertFalse(self.app.source_frame.winfo_ismapped())
        self.app.stop()

    def test_the_checkbox_and_the_setting_move_together(self):
        self.app.summary_var.set(True)
        self.app._on_summary_toggle()
        self.assertTrue(self.app.settings["summary_mode"])
        import settings as settings_module

        self.assertTrue(settings_module.load()["summary_mode"])

        self.app.summary_var.set(False)
        self.app._on_summary_toggle()
        self.assertFalse(self.app.settings["summary_mode"])
        self.assertFalse(settings_module.load()["summary_mode"])


class AppBehaviour(unittest.TestCase):
    def setUp(self):
        self.app = ReadAloudApp()
        self.app.update()
        # Wait for the voice list so voice-dependent state is settled.
        pump(self.app, 10, lambda: bool(self.app.voice_box.cget("values")))
        self.app.volume_var.set(12)
        self.app._on_volume_change()
        self.app.rate_var.set(6)
        self.app._on_rate_change()
        self.app.settings["summary_mode"] = False
        self.app.update()

    def tearDown(self):
        # Exercise the real shutdown path: it must cancel timers and close pipes.
        self.app._on_close()
        self.assertFalse(self.app.engine.alive)

    # ---------------------------------------------------------------- setup

    def test_voice_list_populated_and_selected(self):
        voices = self.app.voice_box.cget("values")
        self.assertGreater(len(voices), 0)
        self.assertIn(self.app.voice_var.get(), voices)

    def test_word_count_tracks_the_text(self):
        self.app._set_text("one two three")
        self.app.update()
        self.assertIn("3 words", self.app.count_var.get())

    # ------------------------------------------------------------- playback

    def test_reading_walks_every_sentence_then_finishes(self):
        self.app._set_text(SAMPLE)
        self.app.read()
        self.assertEqual(self.app.state_name, "speaking")
        self.assertEqual(len(self.app.pieces), 3)

        seen = set()
        finished = pump(
            self.app,
            40,
            lambda: seen.add(self.app.index) or self.app.state_name == "idle",
        )
        self.assertTrue(finished, "playback never finished")
        self.assertEqual(self.app.status_var.get(), "Finished")
        # Every sentence was the current one at some point — none were skipped.
        self.assertEqual(seen, {0, 1, 2})

    def test_highlight_follows_the_spoken_sentence(self):
        self.app._set_text(SAMPLE)
        self.app.read()
        self.app.update()

        ranges = self.app.text.tag_ranges("speaking")
        self.assertEqual(len(ranges), 2, "expected exactly one highlighted range")
        highlighted = self.app.text.get(str(ranges[0]), str(ranges[1]))
        self.assertEqual(highlighted, "The first sentence is short.")

        pump(self.app, 40, lambda: self.app.state_name == "idle")
        self.assertEqual(
            len(self.app.text.tag_ranges("speaking")), 0, "highlight left behind"
        )

    def test_empty_text_is_refused_politely(self):
        self.app._set_text("   \n  ")
        self.app.read()
        self.assertEqual(self.app.state_name, "idle")
        self.assertIn("Nothing to read", self.app.status_var.get())

    def test_pause_resume_and_stop(self):
        self.app._set_text(SAMPLE)
        self.app.rate_var.set(-2)
        self.app._on_rate_change()
        self.app.read()
        pump(self.app, 1.0)

        self.app.toggle_pause()
        self.assertEqual(self.app.state_name, "paused")
        self.assertEqual(self.app.status_var.get(), "Paused")
        self.assertIn("Resume", self.app.pause_btn.cget("text"))

        index_while_paused = self.app.index
        pump(self.app, 1.0)
        self.assertEqual(
            self.app.index, index_while_paused, "advanced while paused"
        )

        self.app.toggle_pause()
        self.assertEqual(self.app.state_name, "speaking")

        self.app.stop()
        self.assertEqual(self.app.state_name, "idle")
        self.assertEqual(self.app.pieces, [])
        self.assertEqual(len(self.app.text.tag_ranges("speaking")), 0)

    def test_skip_forward_does_not_double_advance(self):
        """A stale STATE reply used to make one Next skip two sentences."""
        self.app._set_text(SAMPLE)
        self.app.rate_var.set(-2)
        self.app._on_rate_change()
        self.app.read()
        pump(self.app, 0.8)
        self.assertEqual(self.app.index, 0)

        self.app.skip(1)
        self.assertEqual(self.app.index, 1)
        pump(self.app, 0.8)
        self.assertEqual(self.app.index, 1, "skip advanced twice")

        self.app.skip(-1)
        self.assertEqual(self.app.index, 0)

    def test_skip_clamps_at_both_ends(self):
        self.app._set_text(SAMPLE)
        self.app.read()
        self.app.skip(-5)
        self.assertEqual(self.app.index, 0)
        self.app.skip(50)
        self.assertEqual(self.app.index, len(self.app.pieces) - 1)

    def test_reads_only_the_selection_when_one_exists(self):
        self.app._set_text(SAMPLE)
        # Select exactly the second sentence.
        start = SAMPLE.index("The second")
        end = SAMPLE.index("first one.") + len("first one.")
        self.app.text.tag_add(
            "sel", f"1.0 + {start} chars", f"1.0 + {end} chars"
        )
        self.app.read()
        self.assertEqual(len(self.app.pieces), 1)
        self.assertTrue(self.app.pieces[0][2].startswith("The second sentence"))
        self.assertIn("selection", self.app.status_var.get())

    def test_read_resumes_when_paused(self):
        self.app._set_text(SAMPLE)
        self.app.read()
        pump(self.app, 0.5)
        self.app.toggle_pause()
        self.assertEqual(self.app.state_name, "paused")
        self.app.read()
        self.assertEqual(self.app.state_name, "speaking")

    # ------------------------------------------------------------- settings

    def test_settings_round_trip_to_disk(self):
        self.app.rate_var.set(-3)
        self.app._on_rate_change()
        self.app.volume_var.set(44)
        self.app._on_volume_change()
        self.app._save_settings()

        reloaded = self.app._load_settings()
        self.assertEqual(reloaded["rate"], -3)
        self.assertEqual(reloaded["volume"], 44)
        self.assertEqual(reloaded["voice"], self.app.voice_var.get())

    def test_speed_label_describes_the_rate(self):
        for rate, word in [(-10, "very slow"), (-4, "slow"), (0, "normal"), (8, "very fast")]:
            self.app.rate_var.set(rate)
            self.app._update_rate_label()
            self.assertIn(word, self.app.speed_label.cget("text"))

    def test_scale_float_does_not_break_the_handlers(self):
        """ttk.Scale writes floats; the handlers must not choke on 3.7."""
        self.app.rate_var.set(3.7)
        self.app._on_rate_change()
        self.assertEqual(self.app.settings["rate"], 4)
        self.app.volume_var.set(61.4)
        self.app._on_volume_change()
        self.assertEqual(self.app.settings["volume"], 61)

    def test_speed_word_boundaries(self):
        self.assertEqual(tts_app.speed_word(-10), "very slow")
        self.assertEqual(tts_app.speed_word(0), "normal")
        self.assertEqual(tts_app.speed_word(10), "very fast")

    def test_highlight_offsets_survive_emoji(self):
        """Astral characters count differently in Python and Tk; the highlight
        must still land on the right sentence."""
        self.app._set_text(
            "Emoji \U0001f3a7 sentence one. Emoji \U0001f50a sentence two. "
            "A plain third one."
        )
        self.app.read()
        self.assertEqual(len(self.app.pieces), 3)
        for start, end, piece in self.app.pieces:
            widget_text = self.app.text.get(
                f"1.0 + {start} chars", f"1.0 + {end} chars"
            )
            self.assertEqual(widget_text.strip(), piece)

    def test_crlf_is_normalised_so_offsets_stay_aligned(self):
        self.app._set_text("Line one.\r\nLine two.\r\n\r\nPara two.")
        content = self.app.text.get("1.0", "end-1c")
        self.assertNotIn("\r", content)
        self.app.read()
        for start, end, piece in self.app.pieces:
            widget_text = self.app.text.get(
                f"1.0 + {start} chars", f"1.0 + {end} chars"
            )
            self.assertEqual(widget_text.strip(), piece)


if __name__ == "__main__":
    unittest.main()
