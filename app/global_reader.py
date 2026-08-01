"""Read Aloud Anywhere — read the selected text in any Windows application.

Lives in the notification area by the clock. Right-click the tray icon to change
voice, speed and volume, or to stop whatever is being read.

    Ctrl+Alt+R   read the selected text — press again to stop
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
import tkinter as tk
from ctypes import wintypes
from pathlib import Path
from tkinter import ttk

import settings
import tray
from chunker import chunks
from speech_engine import SpeechEngine, SpeechError

user32 = ctypes.WinDLL("user32", use_last_error=True)

# --------------------------------------------------------------- key injection

VK_CONTROL = 0x11
VK_MENU = 0x12  # Alt
VK_SHIFT = 0x10
VK_LMENU, VK_RMENU = 0xA4, 0xA5
VK_LSHIFT, VK_RSHIFT = 0xA0, 0xA1
VK_LWIN, VK_RWIN = 0x5B, 0x5C
KEYEVENTF_KEYUP = 0x0002

user32.keybd_event.argtypes = [
    wintypes.BYTE, wintypes.BYTE, wintypes.DWORD, ctypes.POINTER(wintypes.ULONG)
]

HOTKEY_READ = 1
HOTKEY_CLIPBOARD = 2
HOTKEY_PAUSE = 3
HOTKEY_STOP = 4

_MODS = tray.MOD_CONTROL | tray.MOD_ALT | tray.MOD_NOREPEAT

HOTKEYS = {
    HOTKEY_READ: (_MODS, ord("R"), "Ctrl+Alt+R", "Read selection / stop"),
    HOTKEY_CLIPBOARD: (_MODS, ord("C"), "Ctrl+Alt+C", "Read clipboard"),
    HOTKEY_PAUSE: (_MODS, ord("P"), "Ctrl+Alt+P", "Pause / resume"),
    HOTKEY_STOP: (_MODS, ord("S"), "Ctrl+Alt+S", "Stop"),
}


def _key(vk: int, up: bool = False) -> None:
    user32.keybd_event(vk, 0, KEYEVENTF_KEYUP if up else 0, None)


def send_copy() -> None:
    """Synthesise Ctrl+C into the focused window.

    The hotkey's own modifiers are still physically held down when this runs, so
    Alt/Shift/Win are released first — otherwise the target app sees Ctrl+Alt+C
    rather than a plain copy.
    """
    for vk in (VK_MENU, VK_LMENU, VK_RMENU, VK_SHIFT, VK_LSHIFT, VK_RSHIFT,
               VK_LWIN, VK_RWIN):
        _key(vk, up=True)
    for vk in (ord("R"), ord("C"), ord("P"), ord("S")):
        _key(vk, up=True)

    _key(VK_CONTROL)
    _key(ord("C"))
    _key(ord("C"), up=True)
    _key(VK_CONTROL, up=True)


# ------------------------------------------------------------------- the app

BG = "#15171d"
PANEL = "#1d2027"
FIELD = "#0f1116"
TEXT = "#e6e8ef"
MUTED = "#8b91a3"
ACCENT = "#6d7cff"
BORDER = "#2b2f3a"

FONT = ("Segoe UI", 9)
FONT_TITLE = ("Segoe UI Semibold", 12)
FONT_KEY = ("Cascadia Mono", 9)

POLL_MS = 120
CLIPBOARD_WAIT_MS = 700

SPEED_WORDS = [(-6, "very slow"), (-2, "slow"), (1, "normal"), (5, "fast"), (10, "very fast")]


def speed_word(rate: int) -> str:
    for threshold, word in SPEED_WORDS:
        if rate <= threshold:
            return word
    return "very fast"


class GlobalReader(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Read Aloud Anywhere")
        self.configure(bg=BG)
        self.resizable(False, False)

        icon = Path(__file__).with_name("watson.ico")
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
        self._restart_job: str | None = None
        self._restore_job: str | None = None
        self._saved_clipboard: str | None = None

        self._build_ui()

        try:
            self.engine = SpeechEngine()
        except SpeechError as exc:
            self._set_status(f"Speech engine failed: {exc}")
            raise

        self.engine.list_voices()
        self.engine.set_rate(self.prefs["rate"])
        self.engine.set_volume(self.prefs["volume"])
        if self.prefs["voice"]:
            self.engine.set_voice(self.prefs["voice"])

        self.events: queue.Queue = queue.Queue()
        self.tray = tray.TrayBackend(
            self.events, icon, HOTKEYS, tooltip="Read Aloud Anywhere"
        )
        self.tray.start()
        self.tray.ready.wait(timeout=5)
        self._push_snapshot()

        if self.tray.failed_hotkeys:
            self._set_status(
                "Already in use by another app: " + ", ".join(self.tray.failed_hotkeys)
            )
        else:
            self._set_status("Listening — select text anywhere and press Ctrl+Alt+R")

        # Closing the window hides to the tray; Quit is how you actually exit.
        self.protocol("WM_DELETE_WINDOW", self.hide_to_tray)
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
        style.configure("CardMuted.TLabel", background=PANEL, foreground=MUTED, font=FONT)
        style.configure(
            "TButton", background=PANEL, foreground=TEXT, font=FONT,
            borderwidth=0, focuscolor=PANEL, padding=(12, 6),
        )
        style.map(
            "TButton", background=[("active", BORDER)], foreground=[("disabled", MUTED)]
        )
        style.configure(
            "TCombobox", fieldbackground=FIELD, background=PANEL, foreground=TEXT,
            arrowcolor=TEXT, bordercolor=BORDER, lightcolor=BORDER, darkcolor=BORDER,
            selectbackground=FIELD, selectforeground=TEXT, padding=4,
        )
        style.map(
            "TCombobox",
            fieldbackground=[("readonly", FIELD)],
            foreground=[("readonly", TEXT)],
        )
        self.option_add("*TCombobox*Listbox.background", FIELD)
        self.option_add("*TCombobox*Listbox.foreground", TEXT)
        self.option_add("*TCombobox*Listbox.selectBackground", ACCENT)
        self.option_add("*TCombobox*Listbox.selectForeground", "#ffffff")
        style.configure(
            "TScale", background=PANEL, troughcolor=FIELD, bordercolor=PANEL,
            lightcolor=ACCENT, darkcolor=ACCENT,
        )

        root = ttk.Frame(self, padding=(16, 14))
        root.pack(fill="both", expand=True)

        ttk.Label(root, text="Read Aloud Anywhere", style="Title.TLabel").pack(anchor="w")

        # --- hotkeys ------------------------------------------------------
        card = ttk.Frame(root, style="Card.TFrame", padding=(12, 10))
        card.pack(fill="x", pady=(10, 10))
        for _id, (_m, _vk, label, desc) in HOTKEYS.items():
            row = ttk.Frame(card, style="Card.TFrame")
            row.pack(fill="x", pady=1)
            ttk.Label(row, text=f"{label:<12}", style="Key.TLabel").pack(side="left")
            ttk.Label(row, text=desc, style="Desc.TLabel").pack(side="left", padx=(10, 0))

        # --- voice / speed / volume --------------------------------------
        controls = ttk.Frame(root, style="Card.TFrame", padding=(12, 10))
        controls.pack(fill="x", pady=(0, 10))
        controls.columnconfigure(1, weight=1)

        ttk.Label(controls, text="Voice", style="CardMuted.TLabel").grid(
            row=0, column=0, sticky="w", padx=(0, 10), pady=(0, 6)
        )
        self.voice_var = tk.StringVar(value=str(self.prefs["voice"]))
        self.voice_box = ttk.Combobox(
            controls, textvariable=self.voice_var, state="readonly", font=FONT, width=26
        )
        self.voice_box.grid(row=0, column=1, sticky="ew", pady=(0, 6))
        self.voice_box.bind("<<ComboboxSelected>>", self._on_voice_change)

        self.speed_label = ttk.Label(controls, style="CardMuted.TLabel")
        self.speed_label.grid(row=1, column=0, sticky="w", padx=(0, 10), pady=(0, 6))
        self.rate_var = tk.DoubleVar(value=float(self.prefs["rate"]))
        ttk.Scale(
            controls, from_=-10, to=10, variable=self.rate_var,
            command=self._on_rate_change,
        ).grid(row=1, column=1, sticky="ew", pady=(0, 6))

        self.volume_label = ttk.Label(controls, style="CardMuted.TLabel")
        self.volume_label.grid(row=2, column=0, sticky="w", padx=(0, 10))
        self.volume_var = tk.DoubleVar(value=float(self.prefs["volume"]))
        ttk.Scale(
            controls, from_=0, to=100, variable=self.volume_var,
            command=self._on_volume_change,
        ).grid(row=2, column=1, sticky="ew")

        self._update_rate_label()
        self._update_volume_label()

        # --- status + buttons --------------------------------------------
        self.status_var = tk.StringVar(value="Starting…")
        ttk.Label(
            root, textvariable=self.status_var, style="Muted.TLabel", wraplength=360
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
        ttk.Button(buttons, text="Hide to tray", command=self.hide_to_tray).pack(
            side="right"
        )
        ttk.Button(buttons, text="Quit", command=self.quit_app).pack(
            side="right", padx=(0, 6)
        )

    def _set_status(self, message: str) -> None:
        self.status_var.set(message)
        self._push_snapshot()

    def _sync_buttons(self) -> None:
        active = self.state_name != "idle"
        self.pause_btn.configure(
            state="normal" if active else "disabled",
            text="Resume" if self.state_name == "paused" else "Pause",
        )
        self.stop_btn.configure(state="normal" if active else "disabled")

    def _push_snapshot(self) -> None:
        """Give the tray thread what it needs to draw its menu and tooltip."""
        if not hasattr(self, "tray"):
            return
        self.tray.set_snapshot(
            voices=list(self.voice_box.cget("values")),
            voice=self.voice_var.get(),
            rate=int(round(self.rate_var.get())),
            volume=int(round(self.volume_var.get())),
            state=self.state_name,
            status=self.status_var.get(),
        )

    # -------------------------------------------------------------- settings

    def _restart_sentence_soon(self) -> None:
        if self.state_name == "idle":
            return
        if self._restart_job is not None:
            self.after_cancel(self._restart_job)
        self._restart_job = self.after(400, self._do_restart)

    def _do_restart(self) -> None:
        self._restart_job = None
        if self.state_name != "idle":
            self._speak_current()

    def _on_voice_change(self, _event=None) -> None:
        name = self.voice_var.get()
        if not name:
            return
        self.prefs["voice"] = name
        self.engine.set_voice(name)
        settings.save(self.prefs)
        self._restart_sentence_soon()
        self._push_snapshot()

    def _on_rate_change(self, _value=None) -> None:
        rate = int(round(self.rate_var.get()))
        if rate != self.prefs["rate"]:
            self.prefs["rate"] = rate
            self.engine.set_rate(rate)
            settings.save(self.prefs)
            self._restart_sentence_soon()
        self._update_rate_label()
        self._push_snapshot()

    def _on_volume_change(self, _value=None) -> None:
        volume = int(round(self.volume_var.get()))
        if volume != self.prefs["volume"]:
            self.prefs["volume"] = volume
            self.engine.set_volume(volume)
            settings.save(self.prefs)
        self._update_volume_label()
        self._push_snapshot()

    def _update_rate_label(self) -> None:
        rate = int(round(self.rate_var.get()))
        self.speed_label.configure(text=f"Speed {rate:+d} ({speed_word(rate)})")

    def _update_volume_label(self) -> None:
        self.volume_label.configure(text=f"Volume {int(round(self.volume_var.get()))}")

    def set_voice(self, name: str) -> None:
        self.voice_var.set(name)
        self._on_voice_change()

    def set_rate(self, rate: int) -> None:
        self.rate_var.set(float(rate))
        self._on_rate_change()

    def set_volume(self, volume: int) -> None:
        self.volume_var.set(float(volume))
        self._on_volume_change()

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
        self._push_snapshot()

    def stop(self) -> None:
        if self.state_name == "idle":
            return
        self.engine.stop()
        self._finish()
        self._set_status("Stopped")

    def read_selection_or_stop(self) -> None:
        """Ctrl+Alt+R is a toggle: press it again to shut the voice up."""
        if self.state_name != "idle":
            self.stop()
        else:
            self._capture_selection()

    def _finish(self) -> None:
        self.state_name = "idle"
        self.pieces = []
        self.index = 0
        self.awaiting_speak_ack = False
        if self._restart_job is not None:
            self.after_cancel(self._restart_job)
            self._restart_job = None
        self._sync_buttons()
        self._set_status("Listening — Ctrl+Alt+R reads the selected text")

    # -------------------------------------------------------------- windowing

    def hide_to_tray(self) -> None:
        if not self.tray.tray_ok:
            # No tray icon means no way back; treat the close box as Quit.
            self.quit_app()
            return
        self.withdraw()

    def show_window(self) -> None:
        self.deiconify()
        self.lift()
        self.focus_force()

    def quit_app(self) -> None:
        self._on_close()

    # -------------------------------------------------------------- dispatch

    def _handle_hotkey(self, hotkey_id: int) -> None:
        if hotkey_id == HOTKEY_READ:
            self.read_selection_or_stop()
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

    def _handle_menu(self, command: int) -> None:
        if command == tray.CMD_SHOW:
            self.show_window()
        elif command == tray.CMD_READ_SELECTION:
            self._capture_selection()
        elif command == tray.CMD_READ_CLIPBOARD:
            self._handle_hotkey(HOTKEY_CLIPBOARD)
        elif command == tray.CMD_PAUSE:
            self.toggle_pause()
        elif command == tray.CMD_STOP:
            self.stop()
        elif command == tray.CMD_QUIT:
            self.quit_app()
        elif tray.CMD_VOICE_BASE <= command < tray.CMD_VOICE_BASE + 500:
            voices = list(self.voice_box.cget("values"))
            index = command - tray.CMD_VOICE_BASE
            if 0 <= index < len(voices):
                self.set_voice(voices[index])
        elif tray.CMD_RATE_BASE <= command < tray.CMD_RATE_BASE + 500:
            index = command - tray.CMD_RATE_BASE
            if 0 <= index < len(tray.RATE_PRESETS):
                self.set_rate(tray.RATE_PRESETS[index][0])
        elif tray.CMD_VOLUME_BASE <= command < tray.CMD_VOLUME_BASE + 500:
            index = command - tray.CMD_VOLUME_BASE
            if 0 <= index < len(tray.VOLUME_PRESETS):
                self.set_volume(tray.VOLUME_PRESETS[index][0])

    def _handle_event(self, event: tuple) -> None:
        kind, value = event
        if kind == "hotkey":
            self._handle_hotkey(value)
        elif kind == "menu":
            self._handle_menu(value)

    def _handle_reply(self, reply: tuple[str, ...]) -> None:
        tag = reply[0]

        if tag == "VOICES":
            voices = [v for v in reply[1:] if v]
            self.voice_box.configure(values=voices)
            chosen = self.prefs["voice"] if self.prefs["voice"] in voices else ""
            if not chosen and voices:
                chosen = voices[0]
            if chosen:
                self.voice_var.set(chosen)
                self.prefs["voice"] = chosen
                self.engine.set_voice(chosen)
            self._push_snapshot()

        elif tag == "OK" and len(reply) > 1 and reply[1] == "SPEAK":
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
                self._handle_event(self.events.get_nowait())
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

    def _release_tk_variables(self) -> None:
        """Drop the Tk variables before the interpreter goes away.

        Variable.__del__ calls back into Tcl. Left to the garbage collector it
        runs after destroy(), and Python prints an ignored RuntimeError for each
        one. Releasing them here means __del__ runs while Tcl is still alive.
        """
        for name in ("voice_var", "rate_var", "volume_var", "status_var"):
            if hasattr(self, name):
                delattr(self, name)

    def _on_close(self) -> None:
        self._closing = True
        for job in (self._tick_job, self._restart_job, self._restore_job):
            if job is not None:
                try:
                    self.after_cancel(job)
                except tk.TclError:
                    pass
        self._tick_job = self._restart_job = self._restore_job = None
        settings.save(self.prefs)
        try:
            self.tray.stop()
        except Exception:
            pass
        try:
            self.engine.shutdown()
        except Exception:
            pass
        self._release_tk_variables()
        self.destroy()


def main() -> int:
    if sys.platform != "win32":
        print("Read Aloud Anywhere uses Windows hotkeys and SAPI; Windows only.")
        return 1
    GlobalReader().mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
