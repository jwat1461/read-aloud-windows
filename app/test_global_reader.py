"""Tests for the OS-wide hotkey reader and its tray icon.

Registers the real system hotkeys and adds a real notification-area icon for the
duration of the run. Speech plays at low volume.

Note: these deliberately never call send_copy(), which would inject a real Ctrl+C
into whatever window happens to be focused.

    python -m unittest test_global_reader -v
"""

import time
import unittest

import global_reader
import tray
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
        pump(self.app, 6, lambda: bool(self.app.voice_box.cget("values")))
        self.app.set_volume(12)
        self.app.set_rate(6)
        self.app.update()

    def tearDown(self):
        self.app._on_close()
        self.assertFalse(self.app.engine.alive)

    # ---------------------------------------------------------------- hotkeys

    def test_all_hotkeys_registered(self):
        self.assertEqual(
            self.app.tray.failed_hotkeys,
            [],
            "some hotkeys were already taken by another app",
        )
        self.assertTrue(self.app.tray.is_alive())

    def test_hotkey_table_is_unique(self):
        combos = [(mods, vk) for mods, vk, _label, _desc in HOTKEYS.values()]
        self.assertEqual(len(combos), len(set(combos)))

    def test_tray_icon_was_added(self):
        self.assertTrue(self.app.tray.tray_ok, "Shell_NotifyIcon rejected the icon")

    def test_tray_thread_stops_cleanly(self):
        self.app.tray.stop()
        self.app.tray.join(timeout=5)
        self.assertFalse(self.app.tray.is_alive())

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
        self.app.set_rate(-2)
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

    def test_read_hotkey_stops_when_already_reading(self):
        """Ctrl+Alt+R is a toggle — pressing it again shuts the voice up."""
        self.app.clipboard_clear()
        self.app.clipboard_append("A fairly long sentence to give us time to react.")
        self.app.set_rate(-2)
        self.app.update()

        self.app._handle_hotkey(global_reader.HOTKEY_CLIPBOARD)
        pump(self.app, 0.6)
        self.assertEqual(self.app.state_name, "speaking")

        self.app._handle_hotkey(global_reader.HOTKEY_READ)
        self.assertEqual(self.app.state_name, "idle", "second press did not stop it")
        self.assertEqual(self.app.status_var.get(), "Stopped")

    # ------------------------------------------------------------- clipboard

    def test_no_selection_times_out_with_a_helpful_message(self):
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
        self.app.clipboard_clear()
        self.app.clipboard_append("Captured selection text. And a second part.")
        self.app._saved_clipboard = "older clipboard"
        self.app.update()

        self.app._collect_copy(0)
        self.assertEqual(self.app.state_name, "speaking")
        self.assertEqual(len(self.app.pieces), 2)
        self.assertIn("selection", self.app.status_var.get())

    # ------------------------------------------------------ voice / speed / volume

    def test_volume_control_changes_the_setting(self):
        self.app.set_volume(35)
        self.assertEqual(self.app.prefs["volume"], 35)
        self.assertIn("35", self.app.volume_label.cget("text"))

    def test_speed_control_changes_the_setting(self):
        self.app.set_rate(-4)
        self.assertEqual(self.app.prefs["rate"], -4)
        self.assertIn("slow", self.app.speed_label.cget("text"))

    def test_voice_control_changes_the_setting(self):
        voices = list(self.app.voice_box.cget("values"))
        self.assertTrue(voices, "no voices available")
        self.app.set_voice(voices[-1])
        self.assertEqual(self.app.prefs["voice"], voices[-1])

    def test_scale_floats_do_not_break_the_handlers(self):
        self.app.volume_var.set(43.7)
        self.app._on_volume_change()
        self.assertEqual(self.app.prefs["volume"], 44)
        self.app.rate_var.set(-2.4)
        self.app._on_rate_change()
        self.assertEqual(self.app.prefs["rate"], -2)

    def test_settings_persist_to_the_shared_file(self):
        import settings

        self.app.set_volume(58)
        self.app.set_rate(2)
        self.assertEqual(settings.load()["volume"], 58)
        self.assertEqual(settings.load()["rate"], 2)

    # ------------------------------------------------------------ tray menu

    def test_tray_snapshot_tracks_the_app(self):
        self.app.set_volume(70)
        self.app.set_rate(-4)
        snap = self.app.tray.snapshot()
        self.assertEqual(snap["volume"], 70)
        self.assertEqual(snap["rate"], -4)
        self.assertEqual(snap["voice"], self.app.voice_var.get())
        self.assertEqual(snap["state"], "idle")
        self.assertEqual(snap["voices"], list(self.app.voice_box.cget("values")))

    def test_tray_menu_sets_the_volume(self):
        for index, (value, _label) in enumerate(tray.VOLUME_PRESETS):
            self.app._handle_menu(tray.CMD_VOLUME_BASE + index)
            self.assertEqual(self.app.prefs["volume"], value)

    def test_tray_menu_sets_the_speed(self):
        for index, (value, _label) in enumerate(tray.RATE_PRESETS):
            self.app._handle_menu(tray.CMD_RATE_BASE + index)
            self.assertEqual(self.app.prefs["rate"], value)

    def test_tray_menu_sets_the_voice(self):
        voices = list(self.app.voice_box.cget("values"))
        for index, name in enumerate(voices):
            self.app._handle_menu(tray.CMD_VOICE_BASE + index)
            self.assertEqual(self.app.prefs["voice"], name)

    def test_tray_menu_out_of_range_ids_are_ignored(self):
        before = dict(self.app.prefs)
        self.app._handle_menu(tray.CMD_VOICE_BASE + 400)
        self.app._handle_menu(tray.CMD_RATE_BASE + 400)
        self.app._handle_menu(tray.CMD_VOLUME_BASE + 400)
        self.assertEqual(self.app.prefs, before)

    def test_tray_menu_stops_reading(self):
        self.app.clipboard_clear()
        self.app.clipboard_append("Something long enough to still be playing.")
        self.app.set_rate(-2)
        self.app.update()
        self.app._handle_menu(tray.CMD_READ_CLIPBOARD)
        pump(self.app, 0.6)
        self.assertEqual(self.app.state_name, "speaking")

        self.app._handle_menu(tray.CMD_STOP)
        self.assertEqual(self.app.state_name, "idle")

    def test_tray_menu_pauses_and_resumes(self):
        self.app.clipboard_clear()
        self.app.clipboard_append("One two three four five six seven eight nine ten.")
        self.app.set_rate(-2)
        self.app.update()
        self.app._handle_menu(tray.CMD_READ_CLIPBOARD)
        pump(self.app, 0.6)

        self.app._handle_menu(tray.CMD_PAUSE)
        self.assertEqual(self.app.state_name, "paused")
        self.app._handle_menu(tray.CMD_PAUSE)
        self.assertEqual(self.app.state_name, "speaking")
        self.app.stop()

    def test_tray_show_command_restores_the_window(self):
        self.app.hide_to_tray()
        self.app.update()
        self.assertEqual(self.app.state(), "withdrawn")
        self.app._handle_menu(tray.CMD_SHOW)
        self.app.update()
        self.assertEqual(self.app.state(), "normal")

    def test_events_from_the_tray_thread_are_dispatched(self):
        self.app.events.put(("menu", tray.CMD_VOLUME_BASE + 2))  # 50%
        pump(self.app, 1.0, lambda: self.app.prefs["volume"] == 50)
        self.assertEqual(self.app.prefs["volume"], 50)

    def test_menu_ids_do_not_collide(self):
        fixed = {
            tray.CMD_SHOW, tray.CMD_READ_CLIPBOARD, tray.CMD_READ_SELECTION,
            tray.CMD_PAUSE, tray.CMD_STOP, tray.CMD_QUIT,
        }
        self.assertEqual(len(fixed), 6)
        self.assertTrue(max(fixed) < tray.CMD_VOICE_BASE)
        self.assertTrue(tray.CMD_VOICE_BASE + 500 <= tray.CMD_RATE_BASE)
        self.assertTrue(tray.CMD_RATE_BASE + 500 <= tray.CMD_VOLUME_BASE)


if __name__ == "__main__":
    unittest.main()
