"""Shared preferences for the desktop app and the OS-wide reader.

Both processes read and write the same file, so changing the voice or speed in
one is picked up by the other the next time it loads.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

DEFAULTS: dict = {
    "voice": "",
    "rate": 0,
    "volume": 100,
    "auto_read_clipboard": False,
    "auto_read_max_chars": 20000,
    "summary_mode": False,
}

SETTINGS_PATH = (
    Path(os.environ.get("APPDATA") or Path.home()) / "ReadAloud" / "settings.json"
)


def load() -> dict:
    values = dict(DEFAULTS)
    try:
        stored = json.loads(SETTINGS_PATH.read_text("utf-8"))
    except (OSError, ValueError):
        return values
    if isinstance(stored, dict):
        values.update({k: stored[k] for k in DEFAULTS if k in stored})
    # Hand-edited files are a fact of life; coerce rather than trust.
    values["auto_read_clipboard"] = bool(values["auto_read_clipboard"])
    values["summary_mode"] = bool(values["summary_mode"])
    try:
        values["auto_read_max_chars"] = max(1, int(values["auto_read_max_chars"]))
    except (TypeError, ValueError):
        values["auto_read_max_chars"] = DEFAULTS["auto_read_max_chars"]
    return values


def save(values: dict) -> None:
    try:
        SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
        SETTINGS_PATH.write_text(json.dumps(values, indent=2), "utf-8")
    except OSError:
        pass  # a read-only profile is not worth crashing over
