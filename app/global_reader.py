"""Read Aloud Anywhere — read the selected text in any Windows application.

Runs quietly in the background and listens for system-wide hotkeys:

    Ctrl+Alt+R   read whatever text is selected in the active window
    Ctrl+Alt+C   read whatever is on the clipboard
    Ctrl+Alt+P   pause / resume
    Ctrl+Alt+S   stop

There is no OS call that hands you another application's selection, so the trick
every tool of this kind uses is to synthesise a Ctrl+C into the focused window
and read what lands on the clipboard. The clipboard is restored afterwards.

    python global_reader.py
"""

from __future__ import annotations

import ctypes
import queue
import sys
import threading
import tkinter as tk
from ctypes import wintypes
from pathlib import Path
from tkinter import ttk

import settings
from chunker import chunks
from speech_engine import SpeechEngine, SpeechError

# --------------------------------------------------------------- win32 setup

user32 = ctypes.WinDLL("user32", use_last_error=True)

MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_NOREPEAT = 0x4000

WM_HOTKEY = 0x0312
WM_QUIT = 0x0012

VK_CONTROL = 0x11
VK_MENU = 0x12  # Alt
VK_SHIFT = 0x10
VK_LMENU, VK_RMENU = 0xA4, 0xA5
VK_LSHIFT, VK_RSHIFT = 0xA0, 0xA1
VK_LWIN, VK_RWIN = 0x5B, 0x5C

KEYEVENTF_KEYUP = 0x0002

HOTKEY_READ = 1
HOTKEY_CLIPBOARD = 2
HOTKEY_PAUSE = 3
HOTKEY_STOP = 4

HOTKEYS = {
    HOTKEY_READ: (MOD_CONTROL | MOD_ALT | MOD_NOREPEAT, ord("R"), "Ctrl+Alt+R", "Read selection"),
    HOTKEY_CLIPBOARD: (MOD_CONTROL | MOD_ALT | MOD_NOREPEAT, ord("C"), "Ctrl+Alt+C", "Read clipboard"),
    HOTKEY_PAUSE: (MOD_CONTROL | MOD_ALT | MOD_NOREPEAT, ord("P"), "Ctrl+Alt+P", "Pause / resume"),
    HOTKEY_STOP: (MOD_CONTROL | MOD_ALT | MOD_NOREPEAT, ord("S"), "Ctrl+Alt+S", "Stop"),
}

user32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
user32.RegisterHotKey.restype = wintypes.BOOL
user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
user32.keybd_event.argtypes = [wintypes.BYTE, wintypes.BYTE, wintypes.DWORD, ctypes.POINTER(wintypes.ULONG)]
user32.PostThreadMessageW.argtypes = [wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]


def _key(vk: int, up: bool = False) -> None:
    user32.keybd_event(vk, 0, KEYEVENTF_KEYUP if up else 0, None)


def send_copy() -> None:
    """Synthesise Ctrl+C into the focused window.

    The hotkey's own modifiers are still physically held down when this runs, so
    Alt/Shift/Win are released first — otherwise the target app sees Ctrl+Alt+C
    rather than a plain copy.
    """
    for vk in (VK_MENU, VK_LMENU, VK_RMENU, VK_SHIFT, VK_LSHIFT, VK_RSHIFT, VK_LWIN, VK_RWIN):
        _key(vk, up=True)
    for vk in (ord("R"), ord("C"), ord("P"), ord("S")):
        _key(vk, up=True)

    _key(VK_CONTROL)
    _key(ord("C"))
    _key(ord("C"), up=True)
    _key(VK_CONTROL, up=True)


class HotkeyListener(threading.Thread):
    """Owns a Windows message loop; hotkeys must be registered on that thread."""

    def __init__(self, events: queue.Queue) -> None:
        super().__init__(daemon=True)
        self.events = events
        self.thread_id: int | None = None
        self.ready = threading.Event()
        self.failed: list[str] = []

    def run(self) -> None:
        self.thread_id = ctypes.windll.kernel32.GetCurrentThreadId()

        registered = []
        for hotkey_id, (mods, vk, label, _desc) in HOTKEYS.items():
            if user32.RegisterHotKey(None, hotkey_id, mods, vk):
                registered.append(hotkey_id)
            else:
                self.failed.append(label)
        self.ready.set()

        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            if msg.message == WM_HOTKEY:
                self.events.put(int(msg.wParam))

        for hotkey_id in registered:
            user32.UnregisterHotKey(None, hotkey_id)

    def stop(self) -> None:
        if self.thread_id is not None:
            user32.PostThreadMessageW(self.thread_id, WM_QUIT, 0, 0)


# ------------------------------------------------------------------- the app

BG = "#15171d"
PANEL = "#1d2027"
TEXT = "#e6e8ef"
MUTED = "#8b91a3"
ACCENT = "#6d7cff"
BORDER = "#2b2f3a"

FONT = ("Segoe UI", 9)
FONT_BOLD = ("Segoe UI Semibold", 9)
FONT_TITLE = ("Segoe UI Semibold", 12)
FONT_KEY = ("Cascadia Mono", 9)

POLL_MS = 120
CLIPBOARD_WAIT_MS = 700


class GlobalReader(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Read Aloud Anywhere")
        self.configure(bg=BG)
        self.resizable(False, False)
        self.attributes("-topmost", True)

        icon = Path(__file__).with_name("readaloud.ico")
        if icon.exists():
            try:
                self.iconbitmap(str(icon))
            except tk.TclError:
                pass

        self.prefs = settings.load()
        self.state_name = "idle"
        self.pieces: list[str] = []
        self.index = 0
        self.scope = ""
        self.awaiting_speak_ack = False
        self._closing = False
        self._tick_job: str | None = None
        self._saved_clipboard: str | None = None
        self._restore_job: str | None = None

        self._build_ui()

        try:
            self.engine = SpeechEngine()
        except SpeechError as exc:
            self._set_status(f"Speech engine failed: {exc}")
            raise

        self.engine.set_rate(self.prefs["rate"])
        self.engine.set_volume(self.prefs["volume"])
        if self.prefs["voice"]:
            self.engine.set_voice(self.prefs["voice"])

        self.events: queue.Queue[int] = queue.Queue()
        self.listener = HotkeyListener(self.events)
        self.listener.start()
        self.listener.ready.wait(timeout=3)

        if self.listener.failed:
            self._set_status(
                "In use by another app: " + ", ".join(self.listener.failed)
            )
        else:
            self._set_status("Listening — select text anywhere and press Ctrl+Alt+R")

        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self._tick_job = self.after(POLL_MS, self._tick)

    # ------------------------------------------------------------------- ui

    def _build_ui(self) -> None:
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("TFrame", background=BG)
        style.configure("Card.TFrame", background=PANEL)
        style.configure("TLabel", background=BG, foreground=TEXT, font=FONT)
        style.configure("Title.TLabel", background=BG, foreground=TEXT, font=FONT_TITLE)
        style.configure("Muted.TLabel", background=BG, foreground=MUTED, font=FONT)
        style.configure("Key.TLabel", background=PANEL, foreground=ACCENT, font=FONT_KEY)
        style.configure("Desc.TLabel", background=PANEL, foreground=MUTED, font=FONT)
        style.configure(
            "TButton",
            background=PANEL,
            foreground=TEXT,
            font=FONT,
            borderwidth=0,
            focuscolor=PANEL,
            padding=(12, 6),
        )
        style.map("TButton", background=[("active", BORDER)], foreground=[("disabled", MUTED)])

        root = ttk.Frame(self, padding=(16, 14))
        root.pack(fill="both", expand=True)

        head = ttk.Frame(root)
        head.pack(fill="x")
        ttk.Label(head, text="Read Aloud Anywhere", style="Title.TLabel").pack(side="left")

        card = ttk.Frame(root, style="Card.TFrame", padding=(12, 10))
        card.pack(fill="x", pady=(12, 10))
        for _id, (_m, _vk, label, desc) in HOTKEYS.items():
            row = ttk.Frame(card, style="Card.TFrame")
            row.pack(fill="x", pady=1)
            ttk.Label(row, text=f"{label:<12}", style="Key.TLabel").pack(side="left")
            ttk.Label(row, text=desc, style="Desc.TLabel").pack(side="left", padx=(10, 0))

        self.status_var = tk.StringVar(value="Starting…")
        ttk.Label(
            root, textvariable=self.status_var, style="Muted.TLabel", wraplength=330
        ).pack(fill="x")

        buttons = ttk.Frame(root)
        buttons.pack(fill="x", pady=(12, 0))
        self.pause_btn = ttk.Button(
            buttons, text="Pause", command=self.toggle_pause, state="disabled"
        )
        self.pause_btn.pack(side="left")
        self.stop_btn = ttk.Button(
            buttons, text="Stop", command=self.stop, state="disabled"
        )
        self.stop_btn.pack(side="left", padx=(6, 0))
        ttk.Button(buttons, text="Hide", command=self.iconify).pack(side="right")
        ttk.Button(buttons, text="Quit", command=self._on_close).pack(
            side="right", padx=(0, 6)
        )

    def _set_status(self, message: str) -> None:
        self.status_var.set(message)

    def _sync_buttons(self) -> None:
        active = self.state_name != "idle"
        self.pause_btn.configure(
            state="normal" if active else "disabled",
            text="Resume" if self.state_name == "paused" else "Pause",
        )
        self.stop_btn.configure(state="normal" if active else "disabled")

    # ------------------------------------------------------------- clipboard

    def _read_clipboard(self) -> str:
        try:
            return self.clipboard_get(type="STRING")
        except tk.TclError:
            return ""

    def _capture_selection(self) -> None:
        """Copy the focused window's selection, then read it."""
        self._saved_clipboard = self._read_clipboard()
        try:
            self.clipboard_clear()
        except tk.TclError:
            pass

        send_copy()
        self._set_status("Reading selection…")
        self.after(120, self._collect_copy, 0)

    def _collect_copy(self, waited: int) -> None:
        text = self._read_clipboard()
        if text.strip():
            self._restore_clipboard_later()
            self.speak(text, "selection")
            return

        if waited < CLIPBOARD_WAIT_MS:
            self.after(60, self._collect_copy, waited + 60)
            return

        self._restore_clipboard_later()
        self._set_status(
            "No text selected — highlight something first, then press Ctrl+Alt+R"
        )

    def _restore_clipboard_later(self) -> None:
        if self._restore_job is not None:
            self.after_cancel(self._restore_job)
        self._restore_job = self.after(400, self._restore_clipboard)

    def _restore_clipboard(self) -> None:
        self._restore_job = None
        if not self._saved_clipboard:
            self._saved_clipboard = None
            return
        try:
            self.clipboard_clear()
            self.clipboard_append(self._saved_clipboard)
        except tk.TclError:
            pass
        self._saved_clipboard = None

    # -------------------------------------------------------------- playback

    def speak(self, text: str, scope: str) -> None:
        pieces = [piece for _s, _e, piece in chunks(text)]
        if not pieces:
            self._set_status("Nothing to read")
            return
        self.pieces = pieces
        self.index = 0
        self.state_name = "speaking"
        self.scope = scope
        self._sync_buttons()
        self._speak_current()

    def _speak_current(self) -> None:
        if not (0 <= self.index < len(self.pieces)):
            self._finish()
            return
        self.awaiting_speak_ack = True
        self.engine.speak(self.pieces[self.index])
        self._set_status(
            f"Reading {self.scope} — sentence {self.index + 1} of {len(self.pieces)}"
        )

    def toggle_pause(self) -> None:
        if self.state_name == "speaking":
            self.engine.pause()
            self.state_name = "paused"
            self._set_status("Paused")
        elif self.state_name == "paused":
            self.engine.resume()
            self.state_name = "speaking"
        else:
            return
        self._sync_buttons()

    def stop(self) -> None:
        if self.state_name == "idle":
            return
        self.engine.stop()
        self._finish()
        self._set_status("Stopped")

    def _finish(self) -> None:
        self.state_name = "idle"
        self.pieces = []
        self.index = 0
        self.awaiting_speak_ack = False
        self._sync_buttons()
        self._set_status("Listening — Ctrl+Alt+R reads the selected text")

    # -------------------------------------------------------------- dispatch

    def _handle_hotkey(self, hotkey_id: int) -> None:
        if hotkey_id == HOTKEY_READ:
            self._capture_selection()
        elif hotkey_id == HOTKEY_CLIPBOARD:
            text = self._read_clipboard()
            if text.strip():
                self.speak(text, "clipboard")
            else:
                self._set_status("Clipboard is empty")
        elif hotkey_id == HOTKEY_PAUSE:
            self.toggle_pause()
        elif hotkey_id == HOTKEY_STOP:
            self.stop()

    def _handle_reply(self, reply: tuple[str, ...]) -> None:
        tag = reply[0]
        if tag == "OK" and len(reply) > 1 and reply[1] == "SPEAK":
            self.awaiting_speak_ack = False
        elif tag == "STATE":
            if self.awaiting_speak_ack:
                return
            if self.state_name == "speaking" and len(reply) > 2 and reply[2] == "1":
                self.index += 1
                if self.index < len(self.pieces):
                    self._speak_current()
                else:
                    self._finish()
        elif tag == "ERR":
            self._set_status("Error: " + " ".join(reply[1:]))
        elif tag == "EXIT":
            self.state_name = "idle"
            self._sync_buttons()
            self._set_status("Speech engine stopped — restart the app")

    def _tick(self) -> None:
        self._tick_job = None
        if self._closing:
            return

        try:
            while True:
                self._handle_hotkey(self.events.get_nowait())
        except queue.Empty:
            pass

        try:
            while True:
                self._handle_reply(self.engine.replies.get_nowait())
        except queue.Empty:
            pass

        if self.state_name == "speaking" and not self.awaiting_speak_ack:
            self.engine.poll_state()

        self._tick_job = self.after(POLL_MS, self._tick)

    def _on_close(self) -> None:
        self._closing = True
        for job in (self._tick_job, self._restore_job):
            if job is not None:
                try:
                    self.after_cancel(job)
                except tk.TclError:
                    pass
        self._tick_job = self._restore_job = None
        try:
            self.listener.stop()
        except Exception:
            pass
        try:
            self.engine.shutdown()
        except Exception:
            pass
        self.destroy()


def main() -> int:
    if sys.platform != "win32":
        print("Read Aloud Anywhere uses Windows hotkeys and SAPI; Windows only.")
        return 1
    GlobalReader().mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
