"""Tests for the OS-wide hotkey reader and its tray icon.

Registers the real system hotkeys and adds a real notification-area icon for the
duration of the run. Speech plays at low volume.

Note: these deliberately never call send_copy(), which would inject a real Ctrl+C
into whatever window happens to be focused. The auto-read tests put text on the
clipboard through the Win32 API rather than Tk, because the formats a password
manager attaches are the whole point and Tk cannot write them.

    python -m unittest test_global_reader -v
"""

import ctypes
import gc
import subprocess
import sys
import time
import unittest
from ctypes import wintypes
from pathlib import Path

import global_reader
import reading
import settings
import tray
from global_reader import HOTKEYS, GlobalReader

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

GMEM_MOVEABLE = 0x0002

kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
kernel32.GlobalLock.restype = wintypes.LPVOID
kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
user32.OpenClipboard.restype = wintypes.BOOL
user32.OpenClipboard.argtypes = [wintypes.HWND]
user32.EmptyClipboard.restype = wintypes.BOOL
user32.CloseClipboard.restype = wintypes.BOOL
user32.SetClipboardData.restype = wintypes.HANDLE
user32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
user32.RegisterClipboardFormatW.restype = wintypes.UINT
user32.RegisterClipboardFormatW.argtypes = [wintypes.LPCWSTR]

CF_BINARY_JUNK = user32.RegisterClipboardFormatW("ReadAloudTestBinary")


def _moveable(data: bytes):
    """A GMEM_MOVEABLE copy of `data`; the clipboard owns it once handed over."""
    handle = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(data))
    pointer = kernel32.GlobalLock(handle)
    ctypes.memmove(pointer, data, len(data))
    kernel32.GlobalUnlock(handle)
    return handle


def set_clipboard(hwnd, text=None, extra=()):
    """Write the clipboard the Win32 way, with whatever extra formats we like.

    `hwnd` has to be a real window: EmptyClipboard called with a NULL owner
    leaves the clipboard ownerless and every SetClipboardData after it fails.
    """
    for _attempt in range(20):
        if user32.OpenClipboard(hwnd):
            break
        time.sleep(0.05)
    else:
        raise RuntimeError("another process is holding the clipboard open")
    try:
        user32.EmptyClipboard()
        if text is not None:
            user32.SetClipboardData(
                global_reader.CF_UNICODETEXT,
                _moveable(text.encode("utf-16-le") + bytes(2)),
            )
        for fmt, value in extra:
            user32.SetClipboardData(fmt, _moveable(int(value).to_bytes(4, "little")))
    finally:
        user32.CloseClipboard()

# Comfortably past the 4-sentence / 60-word bypass, so summary mode engages.
SUMMARY_FIXTURE = (
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


def setUpModule():
    """Keep the score log out of the user's real %APPDATA% while testing."""
    global _REAL_LOG
    import tempfile
    from pathlib import Path as _Path
    import summarize as _s
    _REAL_LOG = _s.default_log_path
    scratch = _Path(tempfile.mkdtemp()) / "summary_log.jsonl"
    _s.default_log_path = lambda: scratch


def tearDownModule():
    import summarize as _s
    _s.default_log_path = _REAL_LOG


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
        self._fail_on_swallowed_callbacks()
        self.app.prefs["auto_read_clipboard"] = False
        self.app.prefs["summary_mode"] = False
        self.app.set_volume(12)
        self.app.set_rate(6)
        self.app.update()

    def tearDown(self):
        # Auto-read is shared, persisted state: never leave a test run with it
        # switched on, or the machine starts reading everything you copy.
        self.app.prefs["auto_read_clipboard"] = False
        self.app.prefs["summary_mode"] = False
        self.app.prefs["auto_read_max_chars"] = settings.DEFAULTS["auto_read_max_chars"]
        self.app._on_close()
        self.assertFalse(self.app.engine.alive)
        # Tk only tears its interpreter down when the object is finally freed,
        # and that has to happen here, on the thread that built it. Left to a
        # collection triggered from the tray or speech-reader thread it aborts
        # the run with "Tcl_AsyncDelete: async handler deleted by the wrong
        # thread", tens of tests later and nowhere near the cause.
        self.app = None
        gc.collect()
        self._assert_no_swallowed_callbacks()

    # ------------------------------------------------------------- helpers

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


    def _clipboard(self, text=None, extra=()):
        set_clipboard(self.app.winfo_id(), text, extra)
        self.app.update()

    def _update_now(self):
        """Deliver a clipboard update the way the tray thread would."""
        self.app._on_clipboard_update(tray.clipboard_sequence())

    def _record_spoken(self):
        """Collect the texts that actually reach the player, in order."""
        spoken = []
        original = self.app.speak

        # *args/**kwargs deliberately: a stand-in pinned to today's parameter
        # list fails as a TypeError inside a Tk after() callback, where it is
        # printed and swallowed, and the only symptom is a queue that quietly
        # stops draining. Ask how that was found.
        def recording(text, *args, **kwargs):
            spoken.append(text)
            original(text, *args, **kwargs)

        self.app.speak = recording
        return spoken

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

    def test_closing_the_window_never_quits_the_app(self):
        """It used to quit outright when the icon was missing. The icon goes
        missing for ordinary reasons -- Explorer restarting, or the shell not
        being ready at login -- and a background reader that exits when you
        close its window is indistinguishable from one that crashed."""
        quit_calls = []
        self.app.quit_app = lambda: quit_calls.append("quit")

        self.app.tray.tray_ok = False
        self.app.tray.readd_icon = lambda: False
        self.app.hide_to_tray()
        self.app.update()

        self.assertEqual(quit_calls, [], "closing the window quit the app")
        self.assertEqual(self.app.state(), "normal", "the window vanished instead")
        self.assertIn("Quit", self.app.status_var.get())

    def test_a_lost_tray_icon_is_asked_for_again_before_giving_up(self):
        asked = []
        self.app.tray.tray_ok = False
        self.app.tray.readd_icon = lambda: (asked.append("readd"), True)[1]

        self.app.hide_to_tray()
        self.app.update()
        self.assertEqual(asked, ["readd"])
        self.assertEqual(self.app.state(), "withdrawn", "it did not hide")
        self.app.deiconify()

    def test_the_app_listens_for_explorer_restarting(self):
        """Explorer destroys every tray icon when it restarts; putting it back
        is the app's job. Without this the icon just disappears."""
        self.assertTrue(tray.WM_TASKBARCREATED, "TaskbarCreated was not registered")
        self.assertTrue(self.app.tray.readd_icon(), "the icon would not come back")
        self.assertTrue(self.app.tray.tray_ok)

    def test_a_callback_error_is_written_down_not_lost(self):
        """Under pythonw there is no console, so a swallowed exception leaves
        nothing behind at all. That is what makes "it quit for no reason"
        unanswerable."""
        import global_reader as gr

        written = []
        original = gr.record_crash
        gr.record_crash = lambda where, exc: written.append((where, type(exc)))
        try:
            self.app._on_callback_error(ValueError, ValueError("boom"), None)
        finally:
            gr.record_crash = original

        self.assertEqual(written, [("callback", ValueError)])
        self.assertIn("crash.log", self.app.status_var.get())

    def test_tray_icon_was_added(self):
        self.assertTrue(self.app.tray.tray_ok, "Shell_NotifyIcon rejected the icon")

    def test_tray_thread_stops_cleanly(self):
        self.app.tray.stop()
        self.app.tray.join(timeout=5)
        self.assertFalse(self.app.tray.is_alive())

    def test_a_hotkey_clash_is_announced_and_not_swallowed(self):
        balloons = []
        self.app.tray.notify = (
            lambda title, message, warning=False: balloons.append((message, warning))
        )
        self.app.tray.failed_hotkeys = ["Ctrl+Alt+R", "Ctrl+Alt+S"]
        self.app._report_hotkey_failures()

        self.assertEqual(len(balloons), 1, "a lost hotkey went unreported")
        message, warning = balloons[0]
        self.assertIn("Ctrl+Alt+R", message)
        self.assertIn("Ctrl+Alt+S", message)
        self.assertTrue(warning)
        self.assertIn("Ctrl+Alt+R", self.app.status_var.get())

    def test_summary_mode_toggles_from_hotkey_and_tray_and_persists(self):
        balloons = []
        self.app.tray.notify = (
            lambda title, message, warning=False: balloons.append(message)
        )
        self.app.prefs["summary_mode"] = False

        self.app._handle_hotkey(global_reader.HOTKEY_SUMMARY)
        self.assertTrue(self.app.prefs["summary_mode"])
        self.assertTrue(settings.load()["summary_mode"])
        self.assertTrue(self.app.tray.snapshot()["summary_mode"])
        self.assertIn("summary", self.app.tray.tooltip_text().lower())
        self.assertEqual(balloons, ["Summary mode on"])

        self.app._handle_menu(tray.CMD_SUMMARY)
        self.assertFalse(self.app.prefs["summary_mode"])
        self.assertFalse(settings.load()["summary_mode"])
        self.assertFalse(self.app.tray.snapshot()["summary_mode"])
        self.assertEqual(balloons, ["Summary mode on", "Summary mode off"])

    def test_summary_mode_off_reads_the_text_verbatim(self):
        self.app.prefs["summary_mode"] = False
        full = reading.plan(SUMMARY_FIXTURE, summary=False).sentences

        self.app.speak(SUMMARY_FIXTURE, "clipboard")
        self.assertEqual(self.app.pieces, full)
        self.assertEqual(self.app.scope, "clipboard")
        self.app.stop()

    def test_summary_mode_on_reads_the_summary_and_says_so(self):
        self.app.prefs["summary_mode"] = True
        full = reading.plan(SUMMARY_FIXTURE, summary=False).sentences

        self.app.speak(SUMMARY_FIXTURE, "clipboard")
        self.assertLess(len(self.app.pieces), len(full), "nothing was summarized")
        for piece in self.app.pieces:
            self.assertIn(piece, full, "a sentence was invented")
        self.assertEqual(self.app.scope, "clipboard summary")
        self.app.stop()

    def test_the_queue_holds_raw_text_and_summarizes_at_dequeue(self):
        """Queued items are summarized when they come off the queue, not when
        they go on: flipping the mode mid-queue must apply to what is still
        waiting."""
        self.app.prefs["summary_mode"] = True
        self.app.set_rate(-2)
        self.app.speak("A short item that is playing right now.", "clipboard")
        self.app.toggle_pause()

        self.app._enqueue_auto_read(SUMMARY_FIXTURE)
        self.assertEqual(
            self.app.auto_queue, [SUMMARY_FIXTURE], "the queue stored a summary"
        )

        self.app.engine.stop()
        self.app._finish()
        full = reading.plan(SUMMARY_FIXTURE, summary=False).sentences
        self.assertLess(len(self.app.pieces), len(full), "dequeue did not summarize")
        self.assertEqual(self.app.auto_queue, [])
        self.app.stop()

    def test_queue_order_survives_summarization(self):
        self.app.prefs["summary_mode"] = True
        self.app.set_rate(6)
        spoken = self._record_spoken()

        first = SUMMARY_FIXTURE
        second = SUMMARY_FIXTURE.replace("migration", "overnight import")
        self.app.speak("Playing first of all.", "clipboard")
        self.app._enqueue_auto_read(first)
        self.app._enqueue_auto_read(second)
        self.assertEqual(self.app.auto_queue, [first, second])

        self.assertTrue(
            pump(self.app, 60, lambda: self.app.state_name == "idle" and not self.app.auto_queue),
            "the queue never drained",
        )
        self.assertEqual(spoken, ["Playing first of all.", first, second])

    def test_skip_drops_the_whole_summarized_item(self):
        self.app.prefs["summary_mode"] = True
        self.app.set_rate(-2)
        self.app.speak(SUMMARY_FIXTURE, "clipboard")
        self.assertGreater(len(self.app.pieces), 1, "need more than one sentence")
        self.app._enqueue_auto_read("The item that comes after it.")

        self.app._handle_hotkey(global_reader.HOTKEY_NEXT)
        self.assertEqual(self.app.pieces, ["The item that comes after it."])
        self.assertEqual(self.app.auto_queue, [])
        self.app.stop()

    def _fresh_summarizer(self, source, budget=None):
        """A scorer with no duplicate memory, for comparing two paths on the
        same text without the second one being called a repeat."""
        import summarize as _s
        return _s.ExtractiveSummarizer(
            log_path=self.app.summarizer.log_path, source=source,
            budget=budget or self.app.summarizer.budget,
        )

    def _record_engine(self):
        """Everything actually handed to SAPI, so the cue can be seen."""
        uttered = []
        original = self.app.engine.speak

        def recording(text):
            uttered.append(text)
            original(text)

        self.app.engine.speak = recording
        return uttered

    def test_the_summary_cue_is_spoken_before_a_summarized_item(self):
        self.app.prefs["summary_mode"] = True
        uttered = self._record_engine()

        self.app.speak(SUMMARY_FIXTURE, "clipboard")
        self.assertTrue(uttered)
        self.assertTrue(
            uttered[0].startswith(reading.CUE), f"no cue: {uttered[0]!r}"
        )
        self.assertIn(self.app.pieces[0], uttered[0])
        self.app.stop()

    def test_no_cue_is_spoken_for_text_that_was_not_trimmed(self):
        self.app.prefs["summary_mode"] = True
        uttered = self._record_engine()

        short = (
            "The build failed again this morning after the deploy. "
            "Nobody can reproduce it. We are blocked until someone looks."
        )
        self.app.speak(short, "clipboard")
        self.assertTrue(uttered)
        self.assertFalse(
            uttered[0].startswith(reading.CUE), "cued text it never trimmed"
        )
        self.app.stop()

    def test_the_cue_is_spoken_once_not_before_every_sentence(self):
        self.app.prefs["summary_mode"] = True
        self.app.set_rate(8)
        uttered = self._record_engine()

        self.app.speak(SUMMARY_FIXTURE, "clipboard")
        self.assertTrue(
            pump(self.app, 45, lambda: self.app.state_name == "idle"),
            "the summary never finished",
        )
        cued = [u for u in uttered if u.startswith(reading.CUE)]
        self.assertEqual(len(cued), 1, uttered)

    def test_read_full_source_reads_the_untrimmed_text_with_summary_on(self):
        self.app.prefs["summary_mode"] = True
        full = reading.plan(SUMMARY_FIXTURE, summary=False).sentences

        self.app.speak(SUMMARY_FIXTURE, "clipboard")
        self.assertLess(len(self.app.pieces), len(full), "nothing was summarized")
        self.assertEqual(self.app.last_source, SUMMARY_FIXTURE)

        self.app._handle_hotkey(global_reader.HOTKEY_FULL)
        self.assertEqual(self.app.pieces, full, "Ctrl+Alt+F did not read it all")
        self.assertEqual(self.app.scope, "full text")
        self.assertFalse(self.app.cue_pending, "cued untrimmed text")
        self.app.stop()

    def test_read_full_source_works_with_summary_mode_off_too(self):
        self.app.prefs["summary_mode"] = False
        full = reading.plan(SUMMARY_FIXTURE, summary=False).sentences

        self.app.speak(SUMMARY_FIXTURE, "clipboard")
        self.app.stop()
        self.app._handle_hotkey(global_reader.HOTKEY_FULL)
        self.assertEqual(self.app.pieces, full)
        self.app.stop()

    def test_read_full_source_before_anything_was_read_says_so(self):
        self.app._handle_hotkey(global_reader.HOTKEY_FULL)
        self.assertEqual(self.app.state_name, "idle")
        self.assertIn("nothing", self.app.status_var.get().lower())

    def test_read_full_source_leaves_the_queue_where_it_was(self):
        self.app.prefs["summary_mode"] = True
        self.app.set_rate(-2)
        self.app.speak(SUMMARY_FIXTURE, "clipboard")
        self.app._enqueue_auto_read("Still waiting its turn.")

        self.app._handle_hotkey(global_reader.HOTKEY_FULL)
        self.assertEqual(
            self.app.auto_queue, ["Still waiting its turn."], "the queue jumped"
        )
        self.app.stop()

    def test_a_second_copy_bows_out_and_leaves_the_hotkeys_alone(self):
        """Two copies would fight over the hotkeys, and the loser goes silent."""
        handle, _already = global_reader.claim_single_instance()
        try:
            second = subprocess.run(
                [sys.executable, str(Path(global_reader.__file__).resolve())],
                capture_output=True,
                text=True,
                timeout=120,
            )
            self.assertEqual(second.returncode, 0, second.stderr)
            self.assertIn("already running", second.stdout.lower())
        finally:
            global_reader.release_single_instance(handle)

        # ...and it did not take Ctrl+Alt+R down with it on the way out.
        stolen = tray.user32.RegisterHotKey(None, 987, global_reader._MODS, ord("R"))
        if stolen:
            tray.user32.UnregisterHotKey(None, 987)
        self.assertFalse(stolen, "the first copy lost Ctrl+Alt+R")

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

    # ----------------------------------------------------------------- brief

    def test_the_brief_hotkey_is_registered(self):
        self.assertIn(global_reader.HOTKEY_BRIEF, HOTKEYS)
        _mods, vk, label, _desc = HOTKEYS[global_reader.HOTKEY_BRIEF]
        self.assertEqual(label, "Ctrl+Alt+B")
        self.assertEqual(vk, ord("B"))

    def test_the_brief_hotkey_captures_the_selection(self):
        """Same capture as Ctrl+Alt+R: it must reuse that path, not repeat it."""
        called = []
        self.app._capture_selection = lambda brief=False: called.append(brief)
        self.app._handle_hotkey(global_reader.HOTKEY_BRIEF)
        self.assertEqual(called, [True], "the brief did not go through capture")

    def test_a_captured_selection_is_briefed_and_the_clipboard_restored(self):
        original = "something the user had copied earlier"
        self.app.clipboard_clear()
        self.app.clipboard_append(SUMMARY_FIXTURE)
        self.app._saved_clipboard = original
        self.app._capture_brief = True
        self.app.update()

        handed = []
        real = self.app.summarizer.summarize
        self.app.summarizer.summarize = lambda text: (handed.append(text)
                                                      or real(text))

        self.app._collect_copy(0)
        self.assertEqual(self.app.state_name, "speaking")
        self.assertEqual(len(handed), 1, "the summarizer was not handed the text")
        self.assertEqual(handed[0], SUMMARY_FIXTURE)
        self.assertLess(len(self.app.pieces), len(reading.plan(
            SUMMARY_FIXTURE, summary=False).sentences))

        self.app.stop()
        self.app._restore_clipboard()
        self.app.update()
        self.assertEqual(self.app._read_clipboard(), original)

    def test_a_brief_with_no_selection_behaves_exactly_as_a_read_does(self):
        """Inherited, not reimplemented: the same timeout, the same restore."""
        def outcome(brief):
            self.app.clipboard_clear()
            self.app.update()
            self.app._saved_clipboard = "previous value"
            self.app._capture_brief = brief
            spoken = self._record_spoken()
            self.app._collect_copy(global_reader.CLIPBOARD_WAIT_MS)
            self.app.update()
            self.app._restore_clipboard()
            self.app.update()
            return (self.app.state_name, list(spoken),
                    self.app._read_clipboard(), self.app._saved_clipboard)

        as_read = outcome(False)
        as_brief = outcome(True)
        self.assertEqual(as_read[0], as_brief[0], "state diverged")
        self.assertEqual(as_read[1], [], "the read path spoke something")
        self.assertEqual(as_brief[1], [], "the brief path spoke something")
        self.assertEqual(as_read[2], as_brief[2], "clipboard diverged")
        self.assertIsNone(as_brief[3])
        self.assertIn("No text selected", self.app.status_var.get())
        self.assertIn("Ctrl+Alt+B", self.app.status_var.get())

    def test_a_brief_speaks_the_opener_and_the_closing_count(self):
        uttered = self._record_engine()
        self.app.speak(SUMMARY_FIXTURE, "selection", summary=True, brief=True)
        self.assertTrue(uttered)
        self.assertTrue(
            uttered[0].startswith(reading.BRIEF_CUE), f"no opener: {uttered[0]!r}"
        )
        self.app.set_rate(8)
        self.assertTrue(
            pump(self.app, 45, lambda: self.app.state_name == "idle"),
            "the brief never finished",
        )
        self.assertTrue(
            uttered[-1].endswith("."), "the trailer was not spoken last"
        )
        self.assertIn("End of brief.", uttered[-1])

    def test_the_trailer_counts_what_was_kept_and_what_came_in(self):
        plan = reading.plan(SUMMARY_FIXTURE, summary=True, source="hotkey",
                            summarizer=self.app.summarizer)
        expected = reading.BRIEF_END.format(kept=plan.n_output,
                                            total=plan.n_input)
        self.assertIn(str(plan.n_input), expected)
        self.assertLess(plan.n_output, plan.n_input)

    def test_a_brief_does_not_turn_summary_mode_on(self):
        """A single request, not a mode. This is the whole point of Ctrl+Alt+B."""
        self.assertFalse(self.app.prefs["summary_mode"])
        self.app.speak(SUMMARY_FIXTURE, "selection", summary=True, brief=True)
        self.assertFalse(self.app.prefs["summary_mode"],
                         "the brief flipped the mode")
        self.app.stop()
        self.assertFalse(settings.load()["summary_mode"])

    def test_short_text_is_briefed_verbatim_without_the_framing(self):
        uttered = self._record_engine()
        short = "The build failed. Nobody knows why yet. We are blocked."
        self.app.speak(short, "selection", summary=True, brief=True)
        self.assertTrue(uttered)
        self.assertFalse(uttered[0].startswith(reading.BRIEF_CUE),
                         "framed text it never trimmed")
        self.app.stop()

    def test_briefing_the_same_text_twice_says_so_instead(self):
        self.app.speak(SUMMARY_FIXTURE, "selection", summary=True, brief=True)
        self.app.stop()
        uttered = self._record_engine()
        self.app.speak(SUMMARY_FIXTURE, "selection", summary=True, brief=True)
        self.assertEqual(uttered, [reading.DUPLICATE])
        self.assertEqual(self.app.state_name, "idle")

    def test_briefing_nothing_says_nothing_to_brief(self):
        uttered = self._record_engine()
        self.app.speak("   ", "selection", summary=True, brief=True)
        self.assertEqual(uttered, [reading.NOTHING])
        self.assertEqual(self.app.state_name, "idle")

    def test_the_tray_item_briefs_the_clipboard(self):
        self._clipboard(SUMMARY_FIXTURE)
        self.app._handle_menu(tray.CMD_BRIEF)
        self.assertEqual(self.app.state_name, "speaking")
        self.assertIn("brief", self.app.scope)
        self.app.stop()

    def test_a_brief_leaves_the_auto_read_queue_where_it_was(self):
        """It interrupts, it does not enqueue, and it does not clear."""
        self.app.auto_queue = ["First queued item.", "Second queued item."]
        self.app.speak(SUMMARY_FIXTURE, "selection", summary=True, brief=True)
        self.assertEqual(len(self.app.auto_queue), 2, "the brief ate the queue")
        self.app.stop()

    def test_private_clipboard_content_is_never_briefed(self):
        self._clipboard("a password", extra=((global_reader.CF_CLIPBOARD_HISTORY, 0),))
        spoken = self._record_spoken()
        self.app._handle_menu(tray.CMD_BRIEF)
        self.assertEqual(spoken, [], "private content was briefed")
        self.assertEqual(self.app.state_name, "idle")

    def test_the_queue_and_the_hotkey_pick_the_same_sentences(self):
        """One scorer, proven rather than assumed."""
        from_hotkey = reading.plan(SUMMARY_FIXTURE, summary=True,
                                   source="hotkey",
                                   summarizer=self._fresh_summarizer("hotkey"))
        from_queue = reading.plan(SUMMARY_FIXTURE, summary=True,
                                  source="queue",
                                  summarizer=self._fresh_summarizer("queue"))
        self.assertTrue(from_hotkey.summarized)
        self.assertEqual(from_hotkey.sentences, from_queue.sentences)
        self.assertEqual(from_hotkey.n_output, from_queue.n_output)

    def test_the_queue_path_respects_the_settings_budget(self):
        """The budget moved into settings.json; the queue reads it too."""
        import summarize as _s
        wide = _s.Budget(ratio=0.9, min_sentences=1, max_sentences=99,
                         min_chars=0)
        # ratio 0.9 would keep nearly everything; the ceiling has to bind.
        capped = _s.Budget(ratio=0.9, min_sentences=1, max_sentences=2,
                           min_chars=0)
        # ratio 0.01 would keep one; the floor has to bind.
        floored = _s.Budget(ratio=0.01, min_sentences=4, max_sentences=99,
                            min_chars=0)
        many = reading.plan(SUMMARY_FIXTURE, summary=True, source="queue",
                            summarizer=self._fresh_summarizer("queue", wide))
        few = reading.plan(SUMMARY_FIXTURE, summary=True, source="queue",
                           summarizer=self._fresh_summarizer("queue", capped))
        least = reading.plan(SUMMARY_FIXTURE, summary=True, source="queue",
                             summarizer=self._fresh_summarizer("queue", floored))
        self.assertGreater(len(many.sentences), len(few.sentences))
        self.assertEqual(len(few.sentences), 2, "the ceiling did not bind")
        self.assertEqual(len(least.sentences), 4, "the floor did not bind")

    def test_the_weight_set_was_recorded_once_at_startup(self):
        import json as _json
        rows = [_json.loads(line) for line
                in self.app.summarizer.log_path.read_text("utf-8").splitlines()
                if line.strip()]
        weights = [r for r in rows if r.get("event") == "weights"]
        self.assertTrue(weights, "no weight set was written at startup")
        self.assertEqual(weights[0]["source"], "hotkey")


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
            tray.CMD_AUTO_READ, tray.CMD_NEXT,
        }
        self.assertEqual(len(fixed), 8)
        self.assertTrue(max(fixed) < tray.CMD_VOICE_BASE)
        self.assertTrue(tray.CMD_VOICE_BASE + 500 <= tray.CMD_RATE_BASE)
        self.assertTrue(tray.CMD_RATE_BASE + 500 <= tray.CMD_VOLUME_BASE)

    # ------------------------------------------------------------- auto-read

    def test_auto_read_speaks_newly_copied_text(self):
        self.assertTrue(
            self.app.tray.clipboard_listener_ok, "no clipboard listener registered"
        )
        self.app.prefs["auto_read_clipboard"] = True
        self._clipboard("Something freshly copied.")
        self._update_now()

        self.assertTrue(
            pump(self.app, 3, lambda: self.app.state_name == "speaking"),
            "auto-read never started",
        )
        self.assertEqual(self.app.pieces, ["Something freshly copied."])
        self.app.stop()

    def test_auto_read_ignores_an_identical_repeat(self):
        self.app.prefs["auto_read_clipboard"] = True
        self._clipboard("Copied once, then copied again.")
        self._update_now()
        self.assertTrue(pump(self.app, 3, lambda: self.app.state_name == "speaking"))
        self.app.stop()

        self._clipboard("Copied once, then copied again.")
        self._update_now()
        pump(self.app, 1.0)
        self.assertEqual(self.app.state_name, "idle", "the repeat was read again")

    def test_auto_read_ignores_updates_without_text(self):
        self.app.prefs["auto_read_clipboard"] = True
        self._clipboard(text=None, extra=[(CF_BINARY_JUNK, 1)])
        self.assertFalse(global_reader.clipboard_has_text())
        self._update_now()

        pump(self.app, 1.0)
        self.assertEqual(self.app.state_name, "idle")

    def test_auto_read_never_touches_content_marked_private(self):
        secret = "correct horse battery staple"
        self.app.prefs["auto_read_clipboard"] = True
        spoken = []
        self.app.engine.speak = spoken.append

        self._clipboard(secret, extra=[(global_reader.CF_EXCLUDE_MONITOR, 1)])
        self.assertTrue(global_reader.clipboard_is_private())
        self._update_now()
        pump(self.app, 1.0)

        # CanIncludeInClipboardHistory = 0 is the other way apps say the same.
        self._clipboard(secret, extra=[(global_reader.CF_CLIPBOARD_HISTORY, 0)])
        self.assertTrue(global_reader.clipboard_is_private())
        self._update_now()
        pump(self.app, 1.0)

        self.assertEqual(spoken, [], "a password reached the speech engine")
        self.assertEqual(self.app.state_name, "idle")
        self.assertIsNone(self.app._last_auto_read)
        self.assertNotIn(secret, self.app.status_var.get())

    def test_auto_read_ignores_our_own_copy_and_restore(self):
        self.app.prefs["auto_read_clipboard"] = True

        self.app._begin_own_clipboard()
        self._clipboard("What our synthetic Ctrl+C put there.")
        self._update_now()
        pump(self.app, 0.6)
        self.assertEqual(self.app.state_name, "idle", "read our own copy")

        self._clipboard("What the user had copied before.")
        seq = tray.clipboard_sequence()
        self.app._end_own_clipboard()
        self.app._on_clipboard_update(seq)
        pump(self.app, 0.6)
        self.assertEqual(self.app.state_name, "idle", "read our own restore")

        # ...and the very next copy, outside the window, is fair game again.
        self._clipboard("A copy the user actually made.")
        self._update_now()
        self.assertTrue(pump(self.app, 3, lambda: self.app.state_name == "speaking"))
        self.app.stop()

    def test_a_burst_of_updates_is_read_once(self):
        self.app.prefs["auto_read_clipboard"] = True
        spoken = self._record_spoken()

        started = time.monotonic()
        for text in ("Burst one.", "Burst two.", "Burst three."):
            set_clipboard(self.app.winfo_id(), text)
            self._update_now()
        self.assertLess(
            time.monotonic() - started,
            global_reader.AUTO_READ_DEBOUNCE_MS / 1000,
            "the burst took longer than the debounce; test is inconclusive",
        )

        self.assertTrue(pump(self.app, 3, lambda: len(spoken) == 1))
        pump(self.app, 0.5)
        self.assertEqual(spoken, ["Burst three."])
        self.app.stop()

    def test_auto_read_caps_long_text_and_says_there_is_more(self):
        self.app.prefs["auto_read_clipboard"] = True
        self.app.prefs["auto_read_max_chars"] = 40
        spoken = self._record_spoken()

        self._clipboard("plenty of words to go around " * 20)
        self._update_now()
        self.assertTrue(pump(self.app, 3, lambda: bool(spoken)))

        self.assertTrue(spoken[0].startswith("plenty of words"))
        self.assertTrue(spoken[0].endswith(global_reader.AUTO_READ_TAIL))
        self.assertLessEqual(len(spoken[0]), 40 + len(global_reader.AUTO_READ_TAIL) + 1)
        self.app.stop()

    def test_auto_read_toggle_persists_and_both_routes_flip_it(self):
        self.app.prefs["auto_read_clipboard"] = False

        self.app._handle_hotkey(global_reader.HOTKEY_AUTO_READ)
        self.assertTrue(self.app.prefs["auto_read_clipboard"])
        self.assertTrue(settings.load()["auto_read_clipboard"])
        self.assertTrue(self.app.tray.snapshot()["auto_read"])
        self.assertIn("Auto-read: on", self.app.tray.tooltip_text())

        self.app._handle_menu(tray.CMD_AUTO_READ)
        self.assertFalse(self.app.prefs["auto_read_clipboard"])
        self.assertFalse(settings.load()["auto_read_clipboard"])
        self.assertFalse(self.app.tray.snapshot()["auto_read"])
        self.assertIn("Auto-read: off", self.app.tray.tooltip_text())

    # ----------------------------------------------------------- the queue

    def test_copies_are_read_in_the_order_they_were_made(self):
        self.app.prefs["auto_read_clipboard"] = True
        self.app.set_rate(6)
        spoken = self._record_spoken()

        for text in ("Queued first.", "Queued second.", "Queued third."):
            self._clipboard(text)
            self._update_now()
            pump(self.app, 0.3)

        self.assertTrue(
            pump(self.app, 45, lambda: len(spoken) == 3 and self.app.state_name == "idle"),
            f"the queue never drained: {spoken}",
        )
        self.assertEqual(spoken, ["Queued first.", "Queued second.", "Queued third."])

    def test_stop_empties_the_whole_queue(self):
        self.app.set_rate(-2)
        self.app.speak("The item that happens to be playing.", "clipboard")
        self.app.toggle_pause()  # hold it so the queue cannot drain under us

        self.app._enqueue_auto_read("Waiting one.")
        self.app._enqueue_auto_read("Waiting two.")
        self.assertEqual(len(self.app.auto_queue), 2)

        self.app._handle_hotkey(global_reader.HOTKEY_STOP)
        self.assertEqual(self.app.auto_queue, [])
        self.assertEqual(self.app.state_name, "idle")

    def test_skip_to_next_starts_the_next_queued_item(self):
        self.app.set_rate(-2)
        self.app.speak("The item we are about to skip past.", "clipboard")
        self.app._enqueue_auto_read("The item that comes after it.")
        self.assertEqual(len(self.app.auto_queue), 1)

        self.app._handle_hotkey(global_reader.HOTKEY_NEXT)
        self.assertEqual(self.app.state_name, "speaking")
        self.assertEqual(self.app.pieces, ["The item that comes after it."])
        self.assertEqual(self.app.auto_queue, [])

        # Nothing left to move on to, so the next press just stops.
        self.app._handle_hotkey(global_reader.HOTKEY_NEXT)
        self.assertEqual(self.app.state_name, "idle")

    def test_pause_holds_the_queue_and_resumes_the_same_item(self):
        self.app.set_rate(-4)
        self.app.speak("A deliberately unhurried sentence, to leave time.", "clipboard")
        self.app._enqueue_auto_read("The one that must wait its turn.")
        playing = list(self.app.pieces)

        self.app._handle_hotkey(global_reader.HOTKEY_PAUSE)
        self.assertEqual(self.app.state_name, "paused")
        pump(self.app, 1.5)
        self.assertEqual(self.app.auto_queue, ["The one that must wait its turn."])
        self.assertEqual(self.app.pieces, playing)

        self.app._handle_hotkey(global_reader.HOTKEY_PAUSE)
        self.assertEqual(self.app.state_name, "speaking")
        self.assertEqual(self.app.pieces, playing, "resume started something else")
        self.assertEqual(len(self.app.auto_queue), 1)
        self.app.stop()

    def test_turning_auto_read_off_still_finishes_the_queue(self):
        self.app.prefs["auto_read_clipboard"] = True
        self.app.set_rate(6)
        spoken = self._record_spoken()

        self.app.speak("Already playing.", "clipboard")
        self.app._enqueue_auto_read("Already queued and still owed.")

        self.app._handle_hotkey(global_reader.HOTKEY_AUTO_READ)
        self.assertFalse(self.app.prefs["auto_read_clipboard"])

        self._clipboard("Copied after the switch went off.")
        self._update_now()
        pump(self.app, 0.6)
        self.assertNotIn("Copied after the switch went off.", self.app.auto_queue)

        self.assertTrue(
            pump(self.app, 45, lambda: self.app.state_name == "idle"),
            "the queue never drained",
        )
        self.assertEqual(
            spoken, ["Already playing.", "Already queued and still owed."]
        )

    def test_the_queue_drops_the_oldest_unread_when_it_overflows(self):
        self.app.set_rate(-2)
        self.app.speak("The item playing while the queue fills up.", "clipboard")
        self.app.toggle_pause()

        for n in range(1, global_reader.AUTO_READ_QUEUE_MAX + 2):
            self.app._enqueue_auto_read(f"Item {n}.")

        self.assertEqual(len(self.app.auto_queue), global_reader.AUTO_READ_QUEUE_MAX)
        self.assertNotIn("Item 1.", self.app.auto_queue)
        self.assertEqual(self.app.auto_queue[0], "Item 2.")
        self.assertEqual(self.app.auto_queue[-1], "Item 21.")
        self.assertTrue(self.app.queue_dropped)
        self.assertIn("oldest dropped", self.app.tray.tooltip_text())
        self.app.stop()


if __name__ == "__main__":
    unittest.main()
