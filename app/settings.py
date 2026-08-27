"""Shared preferences for the desktop app and the OS-wide reader.

Both processes read and write the same file, so changing the voice or speed in
one is picked up by the other the next time it loads.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

# The only host the local-model tier may ever talk to. Not a default: a rule.
LOCAL_HOST = "127.0.0.1"
SUMMARY_ENGINES = ("extractive", "ollama")

# Anything rejected while loading, so the app can show it rather than the user
# wondering why a hand-edited file did nothing.
warnings: list[str] = []

DEFAULTS: dict = {
    "voice": "",
    "rate": 0,
    "volume": 100,
    "auto_read_clipboard": False,
    "auto_read_max_chars": 20000,
    "summary_mode": False,
    "summary_engine": "extractive",
    "summary_model": "llama3.2",
    "summary_host": LOCAL_HOST,
    "log_sentence_text": False,
}

SETTINGS_PATH = (
    Path(os.environ.get("APPDATA") or Path.home()) / "ReadAloud" / "settings.json"
)


def _reject(message: str) -> None:
    warnings.append(message)
    print(f"ReadAloud settings: {message}", file=sys.stderr)


def load() -> dict:
    warnings.clear()
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
    values["log_sentence_text"] = bool(values["log_sentence_text"])

    if values["summary_engine"] not in SUMMARY_ENGINES:
        _reject(f"summary_engine {values['summary_engine']!r} is not one of "
                f"{SUMMARY_ENGINES}; using 'extractive'")
        values["summary_engine"] = "extractive"

    # The promise is no network calls. A configurable host would quietly turn
    # this into one, so the setting exists only to be checked.
    if values["summary_host"] != LOCAL_HOST:
        _reject(f"summary_host {values['summary_host']!r} rejected: the local "
                f"model tier only ever talks to {LOCAL_HOST}")
        values["summary_host"] = LOCAL_HOST

    values["summary_model"] = str(values["summary_model"]) or DEFAULTS["summary_model"]
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
