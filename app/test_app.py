"""Functional tests that drive the real Tk window.

A window flashes on screen while these run. Speech happens at low volume.

    python -m unittest test_app -v
"""

import time
import unittest

import tts_app
from tts_app import ReadAloudApp

SAMPLE = (
    "The first sentence is short. The second sentence is a little longer than "
    "the first one.\n\nAnd this is a third, in its own paragraph."
)


def pump(app, seconds, until=None):
    """Run the Tk event loop for a while, stopping early if `until` becomes true."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        app.update()
        if until is not None and until():
            return True
        time.sleep(0.02)
    return until() if until else False


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
