NEW FEATURES, only after the silent-speak bug fix has landed and reported. If that fix is not committed yet, finish it first and say so.

All four features below, in this order, one commit each. The reading.plan() funnel and the chunkers are the integration points; do not add a second path to the engine.

1. PRONUNCIATION LEXICON
A "lexicon" object in %APPDATA%\ReadAloud\settings.json: case-insensitive whole-word replacements applied to text immediately before it is handed to the engine, in reading.py, so every surface (app, hotkeys, queue, briefs, extension is excluded since it uses Chrome TTS) gets it. Match on word boundaries only; never replace inside a larger word. Ship defaults that demonstrate the shape: "PostgreSQL": "post gress Q L", "SQL": "sequel", "nginx": "engine x", "VARCHAR": "var car". Replacements happen after summarization and after the log write, so the log and the summary scoring see the original text. Missing or malformed lexicon falls back to empty with one logged warning, never a crash. Tray menu gets "Edit pronunciations" which opens the settings file in the default editor.

2. GLOBAL SEEK AND SPEED
Ctrl+Alt+Left / Ctrl+Alt+Right: previous / next sentence in the current read, same behavior as the desktop app's Ctrl+Left/Right, working for hotkey reads, queue items, and briefs. Ctrl+Alt+Up / Ctrl+Alt+Down: speed one step up / down through the existing preset ladder, spoken confirmation of the new step ("Fast"), persisted to settings.json like the tray menu does. Registration failures get the existing balloon treatment.

3. EXPLORER "BRIEF ALOUD"
Second context-menu entry alongside Read aloud, same extension list, same HKCU-only install, added to the existing install/uninstall scripts and both .bat files. It routes the file through the same brief path as Ctrl+Alt+B, bypass rules and all, so a short file just gets read.

4. SAVE LAST READ AS WAV
Tray menu item "Save last read as WAV". Re-renders the text of the most recent completed read or brief through the engine's existing WAV path to a file-save dialog. In-memory only, one item deep, consistent with the no-reading-history rule; note in the README that the last read is held in memory for this purpose. If nothing has been read this session, the item is disabled.

TESTS
Extend the existing suites, count only goes up from the post-bugfix number. Lexicon: boundary matching (no mid-word hits), case-insensitivity, applied after summarize (assert the log row contains original-text-derived scores while the fake engine receives replaced text), malformed lexicon warns and continues. Seek: prev/next moves exactly one sentence in a fake-engine read; speed step persists. Explorer: install script writes the new key, uninstall removes both. WAV: fake engine receives the same text as the last read; disabled state when nothing read.

RULES
Unchanged: no network, no new non-vendored dependencies, no user content in output or report, chunkers and parity test untouched. Update the README sections these touch, matching its existing voice. One commit per feature, messages "lexicon:", "seek:", "explorer-brief:", "wav:". Report new test count against the post-bugfix baseline and any hotkey that failed to register on your machine.
