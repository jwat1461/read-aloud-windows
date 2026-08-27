"""Read Aloud — paste text in, hear it spoken back.

Windows desktop front end over SAPI (System.Speech). Voice, speed and volume are
adjustable, the sentence being spoken is highlighted, and playback can be paused,
skipped or saved to a WAV file.

    python tts_app.py
"""

from __future__ import annotations

import queue
import sys
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import reading
import settings
from speech_engine import SpeechEngine, SpeechError

# ---------------------------------------------------------------- appearance

BG = "#15171d"
PANEL = "#1d2027"
FIELD = "#0f1116"
TEXT = "#e6e8ef"
MUTED = "#8b91a3"
ACCENT = "#6d7cff"
ACCENT_DIM = "#4a55b8"
BORDER = "#2b2f3a"
HL_BG = "#2f3f7a"
HL_FG = "#ffffff"

FONT = ("Segoe UI", 10)
FONT_BOLD = ("Segoe UI Semibold", 10)
FONT_TEXT = ("Cascadia Mono", 12)
FONT_TITLE = ("Segoe UI Semibold", 15)

SPEED_WORDS = [
    (-6, "very slow"),
    (-2, "slow"),
    (1, "normal"),
    (5, "fast"),
    (10, "very fast"),
]

POLL_MS = 120


def speed_word(rate: int) -> str:
    for threshold, word in SPEED_WORDS:
        if rate <= threshold:
            return word
    return "very fast"


class ReadAloudApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Read Aloud")
        self.geometry("980x720")
        self.minsize(720, 520)
        self.configure(bg=BG)

        icon = Path(__file__).with_name("readaloud.ico")
        if icon.exists():
            try:
                self.iconbitmap(str(icon))
            except tk.TclError:
                pass

        self.settings = self._load_settings()

        # Playback state: "idle" | "speaking" | "paused"
        self.state_name = "idle"
        self.pieces: list[tuple[int, int, str]] = []
        self.index = 0
        self.saving = False
        self._save_target = ""
        # STATE replies that arrive before the ack for our latest SPEAK describe the
        # *previous* utterance. Honouring them would skip a sentence on every seek.
        self.awaiting_speak_ack = False
        self._restart_job: str | None = None
        self._tick_job: str | None = None
        self._closing = False
        self.scope = "document"

        self._build_style()
        self._build_ui()
        self._bind_keys()

        try:
            self.engine = SpeechEngine()
        except SpeechError as exc:
            messagebox.showerror("Read Aloud", str(exc))
            self.destroy()
            raise SystemExit(1)

        self.engine.list_voices()
        self.engine.set_rate(self.settings["rate"])
        self.engine.set_volume(self.settings["volume"])

        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self._tick_job = self.after(POLL_MS, self._tick)
        self.text.focus_set()

    # ------------------------------------------------------------- settings

    def _load_settings(self) -> dict:
        return settings.load()

    def _save_settings(self) -> None:
        settings.save(self.settings)

    def _on_summary_toggle(self) -> None:
        """Shares summary_mode with the tray reader through settings.json, the
        same way voice and speed have always been shared."""
        self.settings["summary_mode"] = bool(self.summary_var.get())
        self._save_settings()
        self.status_var.set(
            "Summary mode on — Read will speak the pain points"
            if self.settings["summary_mode"]
            else "Summary mode off"
        )

    # ---------------------------------------------------------------- style

    def _build_style(self) -> None:
        style = ttk.Style(self)
        style.theme_use("clam")

        style.configure("TFrame", background=BG)
        style.configure("Panel.TFrame", background=PANEL)
        style.configure("TLabel", background=BG, foreground=TEXT, font=FONT)
        style.configure("Muted.TLabel", background=BG, foreground=MUTED, font=FONT)
        style.configure(
            "PanelMuted.TLabel", background=PANEL, foreground=MUTED, font=FONT
        )
        style.configure("Panel.TLabel", background=PANEL, foreground=TEXT, font=FONT)
        style.configure(
            "Panel.TCheckbutton",
            background=PANEL,
            foreground=TEXT,
            font=FONT,
            focuscolor=PANEL,
            indicatorcolor=FIELD,
        )
        style.map(
            "Panel.TCheckbutton",
            background=[("active", PANEL)],
            indicatorcolor=[("selected", ACCENT)],
        )
        style.configure(
            "Title.TLabel", background=BG, foreground=TEXT, font=FONT_TITLE
        )

        style.configure(
            "TButton",
            background=PANEL,
            foreground=TEXT,
            font=FONT,
            borderwidth=0,
            focuscolor=PANEL,
            padding=(14, 8),
        )
        style.map(
            "TButton",
            background=[("pressed", BORDER), ("active", BORDER), ("disabled", PANEL)],
            foreground=[("disabled", MUTED)],
        )

        style.configure(
            "Accent.TButton",
            background=ACCENT,
            foreground="#ffffff",
            font=FONT_BOLD,
            borderwidth=0,
            focuscolor=ACCENT,
            padding=(20, 9),
        )
        style.map(
            "Accent.TButton",
            background=[
                ("pressed", ACCENT_DIM),
                ("active", "#7d8bff"),
                ("disabled", BORDER),
            ],
            foreground=[("disabled", MUTED)],
        )

        style.configure(
            "TCombobox",
            fieldbackground=FIELD,
            background=PANEL,
            foreground=TEXT,
            arrowcolor=TEXT,
            bordercolor=BORDER,
            lightcolor=BORDER,
            darkcolor=BORDER,
            selectbackground=FIELD,
            selectforeground=TEXT,
            padding=6,
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
        self.option_add("*TCombobox*Listbox.font", FONT)

        style.configure(
            "TScale",
            background=BG,
            troughcolor=FIELD,
            bordercolor=BG,
            lightcolor=ACCENT,
            darkcolor=ACCENT,
        )
        style.configure(
            "Vertical.TScrollbar",
            background=PANEL,
            troughcolor=BG,
            bordercolor=BG,
            arrowcolor=MUTED,
            width=12,
        )
        style.map("Vertical.TScrollbar", background=[("active", BORDER)])

    # ------------------------------------------------------------------- ui

    def _build_ui(self) -> None:
        root = ttk.Frame(self, padding=(18, 14, 18, 14))
        root.pack(fill="both", expand=True)
        root.columnconfigure(0, weight=1)
        root.rowconfigure(2, weight=1)

        # --- header -------------------------------------------------------
        header = ttk.Frame(root)
        header.grid(row=0, column=0, sticky="ew", pady=(0, 12))
        header.columnconfigure(1, weight=1)

        ttk.Label(header, text="Read Aloud", style="Title.TLabel").grid(
            row=0, column=0, sticky="w"
        )
        ttk.Label(
            header,
            text="Paste text below, then press Read  ·  Ctrl+Enter",
            style="Muted.TLabel",
        ).grid(row=0, column=1, sticky="w", padx=(14, 0))

        ttk.Button(header, text="Open file…", command=self.open_file).grid(
            row=0, column=2, padx=(6, 0)
        )
        ttk.Button(header, text="Save WAV", command=self.save_wav).grid(
            row=0, column=3, padx=(6, 0)
        )
        ttk.Button(header, text="Clear", command=self.clear_text).grid(
            row=0, column=4, padx=(6, 0)
        )

        # --- transport ----------------------------------------------------
        bar = ttk.Frame(root, style="Panel.TFrame", padding=(12, 10))
        bar.grid(row=1, column=0, sticky="ew", pady=(0, 12))

        self.read_btn = ttk.Button(
            bar, text="▶  Read", style="Accent.TButton", command=self.read
        )
        self.read_btn.pack(side="left")

        self.pause_btn = ttk.Button(
            bar, text="❚❚  Pause", command=self.toggle_pause, state="disabled"
        )
        self.pause_btn.pack(side="left", padx=(8, 0))

        self.stop_btn = ttk.Button(
            bar, text="■  Stop", command=self.stop, state="disabled"
        )
        self.stop_btn.pack(side="left", padx=(8, 0))

        self.prev_btn = ttk.Button(
            bar, text="⏮", width=4, command=lambda: self.skip(-1), state="disabled"
        )
        self.prev_btn.pack(side="left", padx=(18, 0))

        self.next_btn = ttk.Button(
            bar, text="⏭", width=4, command=lambda: self.skip(1), state="disabled"
        )
        self.next_btn.pack(side="left", padx=(6, 0))

        ttk.Button(bar, text="Paste & Read", command=self.paste_and_read).pack(
            side="right"
        )

        # --- text area ----------------------------------------------------
        wrap = tk.Frame(root, bg=BORDER, highlightthickness=0, bd=0)
        wrap.grid(row=2, column=0, sticky="nsew")
        wrap.columnconfigure(0, weight=1)
        wrap.rowconfigure(0, weight=1)

        self.text = tk.Text(
            wrap,
            wrap="word",
            bg=FIELD,
            fg=TEXT,
            insertbackground=ACCENT,
            selectbackground=ACCENT_DIM,
            selectforeground="#ffffff",
            font=FONT_TEXT,
            relief="flat",
            padx=16,
            pady=14,
            spacing1=2,
            spacing3=6,
            undo=True,
        )
        self.text.grid(row=0, column=0, sticky="nsew", padx=1, pady=1)

        scroll = ttk.Scrollbar(
            wrap, orient="vertical", command=self.text.yview, style="Vertical.TScrollbar"
        )
        scroll.grid(row=0, column=1, sticky="ns", padx=(0, 1), pady=1)
        self.text.configure(yscrollcommand=scroll.set)

        self.text.tag_configure("speaking", background=HL_BG, foreground=HL_FG)
        self.text.bind("<<Modified>>", self._on_text_modified)

        # --- controls -----------------------------------------------------
        ctrl = ttk.Frame(root, style="Panel.TFrame", padding=(14, 12))
        ctrl.grid(row=3, column=0, sticky="ew", pady=(12, 0))
        ctrl.columnconfigure(1, weight=3)
        ctrl.columnconfigure(4, weight=2)

        ttk.Label(ctrl, text="Voice", style="PanelMuted.TLabel").grid(
            row=0, column=0, sticky="w", padx=(0, 10)
        )
        self.voice_var = tk.StringVar(value=str(self.settings["voice"]))
        self.voice_box = ttk.Combobox(
            ctrl, textvariable=self.voice_var, state="readonly", font=FONT
        )
        self.voice_box.grid(row=0, column=1, sticky="ew", padx=(0, 24))
        self.voice_box.bind("<<ComboboxSelected>>", self._on_voice_change)

        self.speed_label = ttk.Label(ctrl, style="PanelMuted.TLabel")
        self.speed_label.grid(row=0, column=2, sticky="w", padx=(0, 10))
        # DoubleVar, not IntVar: ttk.Scale writes floats and IntVar.get() would raise.
        self.rate_var = tk.DoubleVar(value=float(self.settings["rate"]))
        rate_scale = ttk.Scale(
            ctrl, from_=-10, to=10, variable=self.rate_var, command=self._on_rate_change
        )
        rate_scale.grid(row=0, column=3, sticky="ew", padx=(0, 24))
        ctrl.columnconfigure(3, weight=2)

        self.volume_label = ttk.Label(ctrl, style="PanelMuted.TLabel")
        self.volume_label.grid(row=0, column=4, sticky="w", padx=(0, 10))
        self.volume_var = tk.DoubleVar(value=float(self.settings["volume"]))
        vol_scale = ttk.Scale(
            ctrl, from_=0, to=100, variable=self.volume_var, command=self._on_volume_change
        )
        vol_scale.grid(row=0, column=5, sticky="ew")
        ctrl.columnconfigure(5, weight=2)

        self.summary_var = tk.BooleanVar(value=bool(self.settings["summary_mode"]))
        ttk.Checkbutton(
            ctrl,
            text="Summary mode",
            variable=self.summary_var,
            command=self._on_summary_toggle,
            style="Panel.TCheckbutton",
        ).grid(row=1, column=0, columnspan=2, sticky="w", pady=(10, 0))

        self._update_rate_label()
        self._update_volume_label()

        # --- status -------------------------------------------------------
        status = ttk.Frame(root)
        status.grid(row=4, column=0, sticky="ew", pady=(10, 0))
        status.columnconfigure(0, weight=1)

        self.status_var = tk.StringVar(value="Ready")
        ttk.Label(status, textvariable=self.status_var, style="Muted.TLabel").grid(
            row=0, column=0, sticky="w"
        )

        self.count_var = tk.StringVar(value="0 words")
        ttk.Label(status, textvariable=self.count_var, style="Muted.TLabel").grid(
            row=0, column=1, sticky="e"
        )

    def _bind_keys(self) -> None:
        self.bind("<Control-Return>", lambda e: (self.read(), "break")[1])
        self.bind("<Control-space>", lambda e: (self.toggle_pause(), "break")[1])
        self.bind("<Escape>", lambda e: (self.stop(), "break")[1])
        self.bind("<Control-Shift-V>", lambda e: (self.paste_and_read(), "break")[1])
        self.bind("<Control-Right>", lambda e: (self.skip(1), "break")[1])
        self.bind("<Control-Left>", lambda e: (self.skip(-1), "break")[1])
        self.bind("<Control-s>", lambda e: (self.save_wav(), "break")[1])

    # ------------------------------------------------------------- settings

    def _restart_sentence_soon(self) -> None:
        """Re-speak the current sentence with the new settings, once dragging settles."""
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
        if name:
            self.settings["voice"] = name
            self.engine.set_voice(name)
            self._save_settings()
            self._restart_sentence_soon()

    def _on_rate_change(self, _value=None) -> None:
        rate = int(round(self.rate_var.get()))
        if rate != self.settings["rate"]:
            self.settings["rate"] = rate
            self.engine.set_rate(rate)
            self._save_settings()
            self._restart_sentence_soon()
        self._update_rate_label()

    def _on_volume_change(self, _value=None) -> None:
        vol = int(round(self.volume_var.get()))
        if vol != self.settings["volume"]:
            self.settings["volume"] = vol
            self.engine.set_volume(vol)
            self._save_settings()
        self._update_volume_label()

    def _update_rate_label(self) -> None:
        rate = int(round(self.rate_var.get()))
        self.speed_label.configure(text=f"Speed  {rate:+d} ({speed_word(rate)})")

    def _update_volume_label(self) -> None:
        self.volume_label.configure(text=f"Volume  {int(round(self.volume_var.get()))}")

    # ------------------------------------------------------------- text ops

    def _on_text_modified(self, _event=None) -> None:
        self.text.edit_modified(False)
        content = self.text.get("1.0", "end-1c")
        words = len(content.split())
        chars = len(content)
        self.count_var.set(f"{words:,} words · {chars:,} chars")

    def _set_text(self, content: str) -> None:
        # Tk stores a literal \r, which would desync spoken text from highlight offsets.
        content = content.replace("\r\n", "\n").replace("\r", "\n")
        self.stop()
        self.text.delete("1.0", "end")
        self.text.insert("1.0", content)
        self._on_text_modified()

    def paste_and_read(self) -> None:
        try:
            clip = self.clipboard_get()
        except tk.TclError:
            self.status_var.set("Clipboard is empty or holds no text")
            return
        self._set_text(clip)
        self.read()

    def open_file(self) -> None:
        path = filedialog.askopenfilename(
            title="Open text file",
            filetypes=[("Text files", "*.txt *.md *.log *.csv"), ("All files", "*.*")],
        )
        if path:
            self.load_path(path)

    def load_path(self, path: str | Path, autoplay: bool = False) -> bool:
        """Load a text file into the window. Used by Open and by the shell menu."""
        path = Path(path)
        try:
            content = path.read_text("utf-8", errors="replace")
        except OSError as exc:
            messagebox.showerror("Read Aloud", f"Could not open file:\n{exc}")
            return False
        self._set_text(content)
        self.status_var.set(f"Loaded {path.name}")
        self.title(f"{path.name} — Read Aloud")
        if autoplay and content.strip():
            # Let the voice list arrive before the first utterance.
            self.after(600, self.read)
        return True

    def clear_text(self) -> None:
        self.stop()
        self.text.delete("1.0", "end")
        self._on_text_modified()
        self.status_var.set("Ready")

    # ------------------------------------------------------------- playback

    def _selection_range(self) -> tuple[int, int] | None:
        try:
            start = self.text.index("sel.first")
            end = self.text.index("sel.last")
        except tk.TclError:
            return None
        return (
            int(self.text.count("1.0", start, "chars")[0]),
            int(self.text.count("1.0", end, "chars")[0]),
        )

    def read(self) -> None:
        if self._closing:
            return  # autoplay timer can outlive a window closed straight away
        if self.state_name == "paused":
            self.toggle_pause()
            return

        content = self.text.get("1.0", "end-1c")
        if not content.strip():
            self.status_var.set("Nothing to read — paste or type some text first")
            return

        selection = self._selection_range()
        summary = bool(self.settings["summary_mode"])
        if selection:
            start, end = selection
            plan = reading.plan(content[start:end], offset=start, summary=summary)
            self.scope = "selection"
        else:
            plan = reading.plan(content, summary=summary)
            self.scope = "document"

        self.pieces = plan.pieces
        if not self.pieces:
            self.status_var.set("Nothing to read")
            return

        self.index = 0
        self.state_name = "speaking"
        self._sync_buttons()
        self._speak_current()

    def _speak_current(self) -> None:
        if not (0 <= self.index < len(self.pieces)):
            self._finish()
            return
        start, end, piece = self.pieces[self.index]
        self._highlight(start, end)
        self.awaiting_speak_ack = True
        self.engine.speak(piece)
        if self.state_name == "paused":
            self.state_name = "speaking"
            self._sync_buttons()
        self._update_progress()

    def toggle_pause(self) -> None:
        if self.state_name == "speaking":
            self.engine.pause()
            self.state_name = "paused"
            self.status_var.set("Paused")
        elif self.state_name == "paused":
            self.engine.resume()
            self.state_name = "speaking"
            self._update_progress()
        else:
            return
        self._sync_buttons()

    def stop(self) -> None:
        if self.state_name == "idle":
            return
        self.engine.stop()
        self.state_name = "idle"
        self.pieces = []
        self.index = 0
        self.awaiting_speak_ack = False
        if self._restart_job is not None:
            self.after_cancel(self._restart_job)
            self._restart_job = None
        self._clear_highlight()
        self._sync_buttons()
        self.status_var.set("Stopped")

    def skip(self, delta: int) -> None:
        if self.state_name == "idle" or not self.pieces:
            return
        self.index = max(0, min(len(self.pieces) - 1, self.index + delta))
        self._speak_current()

    def _finish(self) -> None:
        self.state_name = "idle"
        self.pieces = []
        self.index = 0
        self.awaiting_speak_ack = False
        self._clear_highlight()
        self._sync_buttons()
        self.status_var.set("Finished")

    def _update_progress(self) -> None:
        if self.pieces:
            self.status_var.set(
                f"Reading {self.scope} · sentence {self.index + 1} "
                f"of {len(self.pieces)}"
            )

    # ---------------------------------------------------------- highlighting

    def _clear_highlight(self) -> None:
        self.text.tag_remove("speaking", "1.0", "end")

    def _highlight(self, start: int, end: int) -> None:
        self._clear_highlight()
        a = f"1.0 + {start} chars"
        b = f"1.0 + {end} chars"
        self.text.tag_add("speaking", a, b)
        self.text.see(a)

    def _sync_buttons(self) -> None:
        active = self.state_name != "idle"
        self.pause_btn.configure(
            state="normal" if active else "disabled",
            text="▶  Resume" if self.state_name == "paused" else "❚❚  Pause",
        )
        for btn in (self.stop_btn, self.prev_btn, self.next_btn):
            btn.configure(state="normal" if active else "disabled")

    # ------------------------------------------------------------ main loop

    def _tick(self) -> None:
        self._tick_job = None
        if self._closing:
            return

        try:
            while True:
                self._handle_reply(self.engine.replies.get_nowait())
        except queue.Empty:
            pass

        if self.state_name == "speaking" and not self.saving and not self.awaiting_speak_ack:
            self.engine.poll_state()

        self._tick_job = self.after(POLL_MS, self._tick)

    def _handle_reply(self, reply: tuple[str, ...]) -> None:
        tag = reply[0]

        if tag == "VOICES":
            voices = [v for v in reply[1:] if v]
            self.voice_box.configure(values=voices)
            chosen = self.settings["voice"] if self.settings["voice"] in voices else ""
            if not chosen and voices:
                chosen = voices[0]
            if chosen:
                self.voice_var.set(chosen)
                self.settings["voice"] = chosen
                self.engine.set_voice(chosen)
            self.status_var.set(
                f"Ready · {len(voices)} voice{'s' if len(voices) != 1 else ''} available"
            )

        elif tag == "OK" and len(reply) > 1 and reply[1] == "SPEAK":
            self.awaiting_speak_ack = False

        elif tag == "STATE":
            if self.awaiting_speak_ack:
                return  # describes the utterance we just replaced
            done = len(reply) > 2 and reply[2] == "1"
            if self.state_name == "speaking" and done:
                self.index += 1
                if self.index < len(self.pieces):
                    self._speak_current()
                else:
                    self._finish()

        elif tag == "OK" and len(reply) > 1 and reply[1] == "SAVE":
            self.saving = False
            self.status_var.set(f"Saved {self._save_target}")

        elif tag == "ERR":
            self.saving = False
            self.status_var.set("Error: " + " ".join(reply[1:]))

        elif tag == "EXIT":
            self.state_name = "idle"
            self._sync_buttons()
            self.status_var.set("Speech engine stopped — restart the app")

    # ---------------------------------------------------------------- misc

    def save_wav(self) -> None:
        content = self.text.get("1.0", "end-1c")
        selection = self._selection_range()
        if selection:
            content = content[selection[0] : selection[1]]
        if not content.strip():
            self.status_var.set("Nothing to save")
            return

        path = filedialog.asksaveasfilename(
            title="Save spoken audio",
            defaultextension=".wav",
            filetypes=[("WAV audio", "*.wav")],
            initialfile="read-aloud.wav",
        )
        if not path:
            return

        self.stop()
        self.saving = True
        self._save_target = Path(path).name
        self.status_var.set(f"Rendering {self._save_target}…")
        self.engine.save_wav(path, content)

    def _on_close(self) -> None:
        self._closing = True
        for job in (self._tick_job, self._restart_job):
            if job is not None:
                try:
                    self.after_cancel(job)
                except tk.TclError:
                    pass
        self._tick_job = self._restart_job = None
        self._save_settings()
        try:
            self.engine.shutdown()
        except Exception:
            pass
        # Variable.__del__ calls back into Tcl; left to the garbage collector it
        # runs after destroy() and Python prints an ignored RuntimeError.
        for name in ("voice_var", "rate_var", "volume_var", "status_var", "count_var"):
            if hasattr(self, name):
                delattr(self, name)
        self.destroy()


def main(argv: list[str] | None = None) -> int:
    """`tts_app.py [FILE]` — a file argument is loaded and read immediately.

    That is how the Explorer right-click entry launches us.
    """
    argv = sys.argv[1:] if argv is None else argv
    if sys.platform != "win32":
        print("Read Aloud uses Windows SAPI and only runs on Windows.")
        return 1

    app = ReadAloudApp()
    if argv and argv[0]:
        app.load_path(argv[0], autoplay=True)
    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
