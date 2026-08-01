"""Tests for the OS-wide hotkey reader.

Registers the real system hotkeys briefly and drives the app's own event queue.
Speech plays at low volume.

Note: these deliberately never call send_copy(), which would inject a real Ctrl+C
into whatever window happens to be focused.

    python -m unittest test_global_reader -v
"""

import time
import unittest

import global_reader
from global_reader import HOTKEYS, GlobalReader


def pump(app, seconds, until=None):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        app.update()
        if until is not None and until():
            return True
        time.sleep(0.02)
    return until() if until else False


class GlobalReaderBehaviour(unittest.TestCase):
    def setUp(self):
        self.app = GlobalReader()
        self.app.update()
        self.app.engine.set_volume(12)
        self.app.engine.set_rate(6)
        pump(self.app, 2)

    def tearDown(self):
        self.app._on_close()
        self.assertFalse(self.app.engine.alive)

    # ---------------------------------------------------------------- hotkeys

    def test_all_hotkeys_registered(self):
        self.assertEqual(
            self.app.listener.failed,
            [],
            "some hotkeys were already taken by another app",
        )
        self.assertTrue(self.app.listener.is_alive())

    def test_hotkey_table_is_unique(self):
        combos = [(mods, vk) for mods, vk, _label, _desc in HOTKEYS.values()]
        self.assertEqual(len(combos), len(set(combos)))

    def test_listener_thread_stops_cleanly(self):
        self.app.listener.stop()
        self.app.listener.join(timeout=5)
        self.assertFalse(self.app.listener.is_alive())

    # -------------------------------------------------------------- playback

    def test_reads_the_clipboard_on_hotkey(self):
        self.app.clipboard_clear()
        self.app.clipboard_append("First sentence here. Second sentence here.")
        self.app.update()

        self.app._handle_hotkey(global_reader.HOTKEY_CLIPBOARD)
        self.assertEqual(self.app.state_name, "speaking")
        self.assertEqual(len(self.app.pieces), 2)
        self.assertIn("clipboard", self.app.status_var.get())

        self.assertTrue(
            pump(self.app, 30, lambda: self.app.state_name == "idle"),
            "clipboard reading never finished",
        )

    def test_empty_clipboard_is_reported(self):
        self.app.clipboard_clear()
        self.app.clipboard_append("   ")
        self.app.update()
        self.app._handle_hotkey(global_reader.HOTKEY_CLIPBOARD)
        self.assertEqual(self.app.state_name, "idle")
        self.assertIn("empty", self.app.status_var.get().lower())

    def test_pause_and_stop_hotkeys(self):
        self.app.clipboard_clear()
        self.app.clipboard_append("One two three four five six seven eight nine ten.")
        self.app.engine.set_rate(-2)
        self.app.update()

        self.app._handle_hotkey(global_reader.HOTKEY_CLIPBOARD)
        pump(self.app, 0.8)

        self.app._handle_hotkey(global_reader.HOTKEY_PAUSE)
        self.assertEqual(self.app.state_name, "paused")
        self.assertEqual(self.app.pause_btn.cget("text"), "Resume")

        self.app._handle_hotkey(global_reader.HOTKEY_PAUSE)
        self.assertEqual(self.app.state_name, "speaking")

        self.app._handle_hotkey(global_reader.HOTKEY_STOP)
        self.assertEqual(self.app.state_name, "idle")
        self.assertEqual(self.app.pieces, [])

    def test_stop_when_idle_is_harmless(self):
        self.app._handle_hotkey(global_reader.HOTKEY_STOP)
        self.assertEqual(self.app.state_name, "idle")

    # ------------------------------------------------------------- clipboard

    def test_no_selection_times_out_with_a_helpful_message(self):
        """The copy produced nothing, so the user gets told what to do."""
        self.app.clipboard_clear()
        self.app.update()
        self.app._saved_clipboard = "previous value"

        self.app._collect_copy(global_reader.CLIPBOARD_WAIT_MS)
        self.assertEqual(self.app.state_name, "idle")
        self.assertIn("No text selected", self.app.status_var.get())

    def test_clipboard_is_restored_after_a_capture(self):
        original = "something the user had copied earlier"
        self.app.clipboard_clear()
        self.app.clipboard_append(original)
        self.app.update()

        self.app._saved_clipboard = self.app._read_clipboard()
        self.app.clipboard_clear()
        self.app.clipboard_append("temporary selection text.")
        self.app.update()

        self.app._restore_clipboard()
        self.app.update()
        self.assertEqual(self.app._read_clipboard(), original)
        self.assertIsNone(self.app._saved_clipboard)

    def test_captured_selection_is_spoken(self):
        """The tail of _capture_selection, without injecting real keystrokes."""
        self.app.clipboard_clear()
        self.app.clipboard_append("Captured selection text. And a second part.")
        self.app._saved_clipboard = "older clipboard"
        self.app.update()

        self.app._collect_copy(0)
        self.assertEqual(self.app.state_name, "speaking")
        self.assertEqual(len(self.app.pieces), 2)
        self.assertIn("selection", self.app.status_var.get())

    # -------------------------------------------------------------- settings

    def test_shares_settings_with_the_desktop_app(self):
        import settings

        self.assertEqual(self.app.prefs, settings.load())


if __name__ == "__main__":
    unittest.main()
