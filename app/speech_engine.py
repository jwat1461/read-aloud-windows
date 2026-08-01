"""Python side of the SAPI speech server.

Owns the PowerShell subprocess, pushes commands to it, and drains its replies on a
background thread into a queue the GUI polls from its event loop.
"""

from __future__ import annotations

import base64
import queue
import subprocess
import sys
import threading
from pathlib import Path

SERVER_SCRIPT = Path(__file__).with_name("speech_server.ps1")

# Hide the console window PowerShell would otherwise flash on screen.
_NO_WINDOW = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0


def _b64(text: str) -> str:
    return base64.b64encode(text.encode("utf-8")).decode("ascii")


class SpeechError(RuntimeError):
    pass


class SpeechEngine:
    """Line-protocol client for speech_server.ps1."""

    def __init__(self, script: Path = SERVER_SCRIPT) -> None:
        if not script.exists():
            raise SpeechError(f"speech server script not found: {script}")

        self.replies: queue.Queue[tuple[str, ...]] = queue.Queue()
        self._proc = subprocess.Popen(
            [
                "powershell",
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(script),
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            creationflags=_NO_WINDOW,
        )
        self._lock = threading.Lock()
        self._alive = True

        threading.Thread(target=self._read_stdout, daemon=True).start()
        threading.Thread(target=self._read_stderr, daemon=True).start()

    # ---------- plumbing ----------

    def _read_stdout(self) -> None:
        assert self._proc.stdout
        for line in self._proc.stdout:
            line = line.strip()
            if line:
                self.replies.put(tuple(line.split("|")))
        self._alive = False
        self.replies.put(("EXIT",))

    def _read_stderr(self) -> None:
        assert self._proc.stderr
        for line in self._proc.stderr:
            line = line.strip()
            if line:
                self.replies.put(("ERR", line))

    def _send(self, *fields: str) -> None:
        if not self._alive or self._proc.stdin is None:
            return
        with self._lock:
            try:
                self._proc.stdin.write("|".join(fields) + "\n")
                self._proc.stdin.flush()
            except (OSError, ValueError):
                self._alive = False

    @property
    def alive(self) -> bool:
        return self._alive and self._proc.poll() is None

    # ---------- commands ----------

    def list_voices(self) -> None:
        self._send("VOICES")

    def set_voice(self, name: str) -> None:
        self._send("VOICE", _b64(name))

    def set_rate(self, rate: int) -> None:
        self._send("RATE", str(max(-10, min(10, int(rate)))))

    def set_volume(self, volume: int) -> None:
        self._send("VOLUME", str(max(0, min(100, int(volume)))))

    def speak(self, text: str) -> None:
        self._send("SPEAK", _b64(text))

    def pause(self) -> None:
        self._send("PAUSE")

    def resume(self) -> None:
        self._send("RESUME")

    def stop(self) -> None:
        self._send("STOP")

    def poll_state(self) -> None:
        self._send("STATE")

    def save_wav(self, path: str, text: str) -> None:
        self._send("SAVE", _b64(path), _b64(text))

    def shutdown(self) -> None:
        self._send("QUIT")
        try:
            self._proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            self._proc.kill()
            self._proc.wait(timeout=3)
        self._alive = False
        for pipe in (self._proc.stdin, self._proc.stdout, self._proc.stderr):
            if pipe is not None:
                try:
                    pipe.close()
                except OSError:
                    pass
