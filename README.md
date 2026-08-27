# Read Aloud

Text-to-speech for Windows, in three places: a desktop app you paste into, a
Chrome extension that reads highlighted text on any page, and a background
listener that reads the selected text in **any** application.

Everything uses the speech engine already built into Windows. No API keys, no
accounts, no network calls, no pip installs.

---

## What you get

| | What it does | How you start it |
|---|---|---|
| **Desktop app** | Paste text, press Read. Sentence-by-sentence highlighting, voice/speed/volume, save to WAV. | `Read Aloud.bat` |
| **Read Aloud Anywhere** | Tray icon + global hotkeys. Select text in Word, a PDF, Slack, anywhere — press `Ctrl+Alt+R`. | `Read Aloud Anywhere.bat` |
| **Chrome extension** | Highlight text on a page → right-click → **Read aloud**. Highlights each sentence as it reads. | Load `extension\` in Chrome |
| **Explorer menu** | Right-click a `.txt`/`.md`/`.sql`/… file → **Read aloud**. | `Add Right-Click Menu.bat` |

Double-click **`Add to Startup.bat`** to have the OS-wide reader running from
every login.

---

## Requirements

- Windows 10 or 11
- Python 3.10+ on `PATH` (tkinter ships with the standard installer)
- Google Chrome, for the extension
- Node.js, only if you want to run the chunker parity test

---

## 1. Desktop app

```powershell
.\"Read Aloud.bat"
# or
python app\tts_app.py
python app\tts_app.py notes.txt      # load a file and start reading
```

Paste into the big box and press **Read**. The sentence being spoken is
highlighted and scrolls into view.

**Select part of the text first and only that part is read.**

| Key | Action |
|---|---|
| `Ctrl+Enter` | Read (or resume) |
| `Ctrl+Space` | Pause / resume |
| `Esc` | Stop |
| `Ctrl+←` / `Ctrl+→` | Previous / next sentence |
| `Ctrl+Shift+V` | Paste from clipboard and start reading |
| `Ctrl+S` | Save the spoken audio as a WAV file |

Voice, speed (−10…+10) and volume sit along the bottom and are remembered
between runs in `%APPDATA%\ReadAloud\settings.json`. Changing the voice or speed
mid-sentence re-reads that sentence so you hear the change immediately.

---

## 2. Read Aloud Anywhere (the whole OS)

```powershell
.\"Read Aloud Anywhere.bat"
```

It puts a **W** icon in the notification area by the clock and stays out of the
way. From then on, in **any** application:

| Hotkey | Action |
|---|---|
| `Ctrl+Alt+R` | Read the selected text — **press again to stop** |
| `Ctrl+Alt+C` | Read whatever is on the clipboard |
| `Ctrl+Alt+A` | Auto-read the clipboard — on / off |
| `Ctrl+Alt+S` | Summary mode — on / off |
| `Ctrl+Alt+F` | Read the full untrimmed source of the current item |
| `Ctrl+Alt+N` | Skip to the next queued item |
| `Ctrl+Alt+P` | Pause / resume |
| `Ctrl+Alt+X` | Stop (also empties the auto-read queue) |

> `Ctrl+Alt+X` stops, not `Ctrl+Alt+S`. Summary mode took `S`; stop moved to
> `X` in the same change.

### Auto-read the clipboard

Press `Ctrl+Alt+A`, or tick **Auto-read clipboard** in the tray menu, and
anything that lands on the clipboard from then on is read without you pressing
anything else. Toggling says "on" or "off" out loud so you know it took, and the
switch is remembered in `settings.json` like everything else, so it survives a
restart. Copies queue in the order you made them: a new copy waits its turn
rather than cutting the current one off, `Ctrl+Alt+N` skips to the next,
`Ctrl+Alt+P` holds the line, and `Ctrl+Alt+S` is the only thing that empties it.
Twenty items can be waiting; past that the oldest unread one is dropped.
Anything longer than 20,000 characters (`auto_read_max_chars` in the settings
file) is read up to that point and finished with "and more". Hover the tray icon
to see whether it is on and how many copies are waiting.

### What a password manager marks private is never fetched

Not merely never spoken — never fetched. When an update arrives, the clipboard
*formats* are inspected first: if the content carries
`ExcludeClipboardContentFromMonitorProcessing`, or has
`CanIncludeInClipboardHistory` set to zero, the reader returns before it asks
for the text at all. Nothing enters the process, so there is nothing to reach
the speech engine, the status line or a log.

The evidence for that is not the silence — silence is what you would also get
from reading the password and deciding not to say it. It is the memory. The
reader keeps the last thing it auto-read, to avoid repeating a re-copy; after a
private copy, that record still holds the *previous* text, which it could not if
the new one had ever been read. The `global` suite asserts exactly this, and so
does a live trial against a real password-manager-style clipboard write.

If the formats are present but unreadable — another process has the clipboard
open — the content counts as private too. Staying quiet costs one read; guessing
costs a password.

**While it is on it reads everything you copy**, so turn it off before copying
anything you would rather not hear out loud.

### Summary mode

Press `Ctrl+Alt+S`, or tick **Summary mode** in the tray menu, and text stops
being read out in full. What you hear instead is the handful of sentences that
carry the problem: what failed, what is blocked, what is late, what costs money,
and what someone is being asked to decide. It applies to `Ctrl+Alt+R` reads and
to auto-read clipboard items alike, and it is off by default.

Every summarized item is announced with a single spoken word, **"Summary"**, so
you always know text was cut rather than wondering why a message sounded so
short. `Ctrl+Alt+F` reads the full untrimmed source of whatever is playing or
was last played, and works with the mode off too, where it is simply a re-read.

Nothing is written. Sentences are **chosen**, never generated, so every word you
hear appeared in the text you copied — which is a promise a language model
cannot make, and the reason the default engine is not one.

Some things are read as they arrived, with no cue: anything under four sentences
or sixty words, source code, a bare URL, and a list of short lines. Inside
ordinary prose, a code line, a URL or a bullet is never *chosen* either, because
a summary that reads out a link is worse than one sentence too long.

**Tuning it.** The vocabulary and the weights live in
`%APPDATA%\ReadAloud\summary_rules.json`, written with defaults the first time
summary mode runs. Add words to `pain_words` for whatever your own trouble
sounds like — a project name, a client, a system that is always down — and they
count from the next read. No rebuild.

| Weight | Default | What it does |
|---|---|---|
| `pain_word` | 1.0 | Per pain word, square-rooted so one furious sentence cannot own the summary |
| `question` | 0.8 | The sentence asks something |
| `figure` | 0.7 | A number with a unit or a currency |
| `first_paragraph` | 0.25 | Opening nudge — small, because this is email, not news copy |
| `last_paragraph` | 0.2 | Closing nudge |
| `cue_blend` | 0.6 | How much the above counts against… |
| `luhn_blend` | 0.4 | …term frequency after stopword removal |

`negations` and `negation_window` (3) are in the same file. A pain word within
the window after a negator does not count, so *"no errors"* and *"not broken"*
score neutral instead of shouting. Plurals are folded, so `error` catches
`errors`.

The same input always produces the same output. There is no randomness, no
clock, and no graph ranking — TextRank and its relatives order by node insertion
and sum floats non-associatively, so near-tied sentences can swap places between
runs, which would make the whole thing untestable.

### Summary mode with a local model (optional, off)

If you run [Ollama](https://ollama.com) locally, summary mode can hand the text
to it instead. Set `summary_engine` to `"ollama"` in `settings.json`;
`summary_model` defaults to `llama3.2`, and `qwen2.5:3b` is a smaller
alternative. The model is asked for the pain points as at most eight short
spoken sentences, at temperature 0 and seed 0, and is preloaded once per session
so the first summary is not paying the load cost with you waiting. **"Summarizing"**
is spoken before the request, so the wait is not dead air.

If Ollama is not running, times out, or answers with nonsense, the extractive
summarizer takes over silently and you find out on your next toggle, when the
balloon says *"Summary mode on (local model unavailable, using extractive)"*.

This talks to `127.0.0.1` and nothing else. `summary_host` exists only so that
any other value can be rejected and logged — there is no configuration that
turns this into a network call. The request is synchronous: with the model
resident it returns in well under a second, but a cold or wedged one can hold
the window for up to the 20-second read timeout. That is the cost of the opt-in
tier, and the reason the default is a summarizer that cannot stall at all.

### The tray icon

**Right-click the W by the clock** to change things without opening anything:

- **Voice** — every installed voice, with the current one ticked
- **Speed** — Very slow · Slow · Normal · Fast · Faster · Fastest
- **Volume** — Mute · 25% · 50% · 75% · 100%
- **Auto-read clipboard** — tick it and every copy is read; `Ctrl+Alt+A`
- **Summary mode** — tick it and you hear the pain points only; `Ctrl+Alt+S`
- Read selection · Read clipboard · Read full text · Pause · Skip to next · **Stop reading**
- Open Read Aloud · Quit

Left-click the icon to open the window, which has the same voice dropdown plus
speed and volume sliders for finer control than the menu presets. Closing that
window hides it back to the tray; **Quit** is how you actually exit.

> On Windows 11 new tray icons start in the hidden-icons flyout — click the `^`
> next to the clock, then drag the W onto the taskbar to keep it visible.

Every one of these is the same setting: change the speed in the tray, the
Anywhere window or the desktop app and the other two follow, because they share
`%APPDATA%\ReadAloud\settings.json`.

Start it automatically at login — double-click **`Add to Startup.bat`**, or:

```powershell
powershell -ExecutionPolicy Bypass -File tools\install_startup.ps1
powershell -ExecutionPolicy Bypass -File tools\install_startup.ps1 -Remove   # undo
```

`Add to Startup.bat` puts a shortcut in your own Startup folder and launches it
straight away, so you don't have to sign out first. Undo with
**`Remove from Startup.bat`**.

Only one copy runs at a time. Start a second and it says
*"Read Aloud Anywhere is already running"* in a balloon by the clock and exits,
rather than starting up with every hotkey silently dead — `RegisterHotKey` is
first come, first served. If a hotkey is taken by some *other* program, the
balloon names the combination so you are never left wondering why nothing
happens.

### How it reads other applications

Windows gives no way to ask another program for its selected text. So on
`Ctrl+Alt+R` the app synthesises a `Ctrl+C` into the focused window and reads
what lands on the clipboard, then puts your previous clipboard contents back.

Two consequences worth knowing:

- **The app must support Ctrl+C.** Almost everything does. A few custom controls
  do not, and there is nothing to read in that case.
- **Programs running as administrator are out of reach** unless this app is also
  running as administrator. Windows blocks synthetic input from a lower
  integrity level.

---

## 3. Chrome extension

### Install

1. Open `chrome://extensions`
2. Turn on **Developer mode** (top right)
3. **Load unpacked** → select the `extension` folder in this project
4. Pin **Read Aloud** to the toolbar

### Use

**Highlight any text on a page, then either:**

- click the **Read aloud** button that pops up next to the selection, or
- right-click → **Read aloud: "…"**, or
- press `Alt+Shift+R`

The sentence being spoken is highlighted on the page as it goes, and the page
scrolls to follow along.

The right-click menu also has:

- **Read from here to the end of the page** — start at your selection, keep going
- **Read this page aloud** — skips nav, headers, footers and sidebars
- **Reading speed** → Slow / Normal / Fast / Faster
- **Volume** → Mute / 25% / 50% / 75% / 100%
- **Stop reading**

| Shortcut | Action |
|---|---|
| `Alt+Shift+R` | Read the highlighted text |
| `Alt+Shift+P` | Pause / resume |
| `Alt+Shift+S` | Stop |

Click the toolbar icon for a popup with a paste box, voice, speed, pitch and
volume, plus **Read highlighted text** and **Read whole page** buttons. Playback
lives in the background worker, so closing the popup does not stop it.

Page highlighting uses the CSS Custom Highlight API, which paints ranges without
inserting elements — no `<span>` wrappers, so pages built with React and similar
frameworks are not disturbed. On a browser without it, reading still works and
only the highlight is skipped.

### Permissions, and why

| Permission | Reason |
|---|---|
| `tts` | Speak text |
| `contextMenus` | The right-click menu |
| `storage` | Remember your voice and speed |
| `scripting` | Find which frame holds your selection |
| `http://*/*`, `https://*/*` | Read text from the page you are on |

Nothing leaves your machine. The extension makes no network requests.

---

## 4. Explorer right-click menu

Adds a **Read aloud** entry when you right-click a text file, the way DiffMerge
adds its own entry. Double-click **`Add Right-Click Menu.bat`**, or:

```powershell
powershell -ExecutionPolicy Bypass -File tools\install_context_menu.ps1
powershell -ExecutionPolicy Bypass -File tools\uninstall_context_menu.ps1   # undo
```

Covers `.txt .md .log .csv .json .xml .yml .yaml .sql .py .js .ts .html .css
.ini .rtf` by default; pass `-Extensions` to change that. Everything is written
under `HKCU:\Software\Classes`, so no administrator rights are needed and no
other user is affected.

On Windows 11 the entry may sit under **Show more options** (`Shift+F10`).

---

## Voices

Windows ships with two SAPI voices (David and Zira). More can be added in
**Settings → Time & Language → Speech → Manage voices**.

Windows also has a second, larger set of voices used by the modern speech
platform, which `System.Speech` does not see. The Chrome extension often lists
more voices than the desktop app for that reason — Chrome talks to both.

---

## Testing

```powershell
powershell -ExecutionPolicy Bypass -File run_tests.ps1
powershell -ExecutionPolicy Bypass -File run_tests.ps1 -Quiet     # no sound, no windows
powershell -ExecutionPolicy Bypass -File run_tests.ps1 -Browser   # extension DOM suite
```

191 tests across eight suites:

| Suite | Tests | What it covers |
|---|---|---|
| `chunker` | 11 | Sentence splitting: punctuation, paragraphs, long runs, unicode |
| `summary` | 36 | The extractive summarizer: bypasses, negation windows, determinism, the rules file, the corpus snapshot |
| `model` | 29 | The optional Ollama tier against a real local stub server: request shape, warm-up, every failure mode |
| `parity` | 5 | The Python and JavaScript chunkers produce identical sentences |
| `engine` | 11 | Live round trip against the PowerShell SAPI server, including WAV output |
| `app` | 25 | Drives the real Tk window: playback, highlighting, seeking, settings, the summary pane |
| `global` | 56 | Real system hotkeys and tray icon, menu commands, clipboard capture, auto-read and its queue, single-instance guard, summary mode |
| `dom` | 18 | Extension text extraction, DOM range mapping and highlighting, in Chrome |

`summary` and `model` are silent and need nothing running: the Ollama tests
start their own HTTP server on 127.0.0.1 and point the client at it, so the
connection, both timeouts and the JSON handling are genuinely exercised rather
than mocked. The corpus snapshot in `app/test_summary_snapshot.json` records
what the summarizer picks and fails on any drift; regenerate it deliberately
with `python toolsesnapshot.py` and read the diff before committing it.

The `engine`, `app` and `global` suites make sound (at low volume) and briefly
open windows, because they exercise the real speech engine rather than a mock.

The `dom` suite runs in a browser, because flattening a real document and
mapping sentences back onto DOM ranges is exactly what a mock would not test.
`-Browser` serves `extension\` and opens `test\harness.html`; the page reports
pass/fail inline and leaves the results in `window.__testResults`.

---

## How it fits together

```
app/
  tts_app.py         desktop window
  global_reader.py   OS-wide hotkeys and tray-menu handling
  tray.py            Win32 tray icon, popup menu and hotkey registration
  speech_engine.py   talks to the PowerShell server over stdin/stdout
  speech_server.ps1  the SAPI process: speak, pause, stop, save WAV
  chunker.py         text -> sentences (with offsets, for highlighting)
  reading.py         the one place text becomes what the engine is handed
  summarize.py       extractive pain-point summary, deterministic, no model
  local_model.py     optional Ollama tier, 127.0.0.1 only, fails soft
  settings.py        shared preferences

extension/
  background.js      service worker: owns all playback state
  content.js         extracts page text, maps sentences onto DOM ranges
  chunker.js         the same splitter as chunker.py
  popup.html/js/css  controls

tools/
  make_icons.py             generates the icons (no Pillow needed)
  install_context_menu.ps1  Explorer right-click entry
  install_startup.ps1       run at login
  test_parity.py            cross-checks the two chunkers
```

Python talks to SAPI through a long-lived PowerShell process using a small
line-based protocol. Text is base64-encoded on the way across, so newlines,
pipes and non-ASCII survive intact.

The tray icon and the global hotkeys share one Win32 thread. They have to:
`RegisterHotKey` delivers `WM_HOTKEY` to whichever thread registered it, and a
tray icon needs a window with a message loop — so one loop serves both. Tk never
touches Win32; it reads events off a queue and pushes a state snapshot back, so
the menu can show the current voice, speed and volume without either thread
reaching into the other's objects.

Both the app and the extension split text into sentences before speaking. That
buys three things: highlighting has something to point at, seeking has somewhere
to jump to, and stopping is immediate instead of waiting out a long utterance.

`tools/test_parity.py` runs both chunkers over the same corpus and requires
identical sentences, so the two implementations cannot drift apart. Their
*offsets* differ by design — Python counts code points, JavaScript counts UTF-16
code units — and each is checked against its own runtime.

---

## Troubleshooting

**Nothing is spoken.** Check Windows volume and that a voice is selected in the
dropdown. Then run `python app\tts_app.py` from a terminal to see any error.

**`Ctrl+Alt+R` does nothing.** Another program already owns that combination. A
balloon by the clock names it at startup and the window lists it too. Also check
the target app is not running as administrator.

**Summary mode reads out something useless.** Open
`%APPDATA%\ReadAloud\summary_rules.json` and add the words your own trouble
uses to `pain_words`, or raise `pain_word` above 1.0 so vocabulary outweighs
term frequency. `Ctrl+Alt+F` reads the full text whenever the summary missed
something.

**No W icon by the clock.** Windows 11 hides new tray icons: click the `^` next
to the clock and drag the W out onto the taskbar.

**The extension's right-click entry is missing.** Reload the extension at
`chrome://extensions`, then reload the page. Content scripts are not injected
into `chrome://` pages, the Chrome Web Store, or PDFs.

**Nothing reads on one particular site.** Some pages render text inside a canvas
or a cross-origin frame that extensions cannot reach. Copy the text and use the
popup's paste box.
