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
| `Ctrl+Alt+P` | Pause / resume |
| `Ctrl+Alt+S` | Stop |

### The tray icon

**Right-click the W by the clock** to change things without opening anything:

- **Voice** — every installed voice, with the current one ticked
- **Speed** — Very slow · Slow · Normal · Fast · Faster · Fastest
- **Volume** — Mute · 25% · 50% · 75% · 100%
- Read selection · Read clipboard · Pause · **Stop reading**
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

88 tests across six suites:

| Suite | Tests | What it covers |
|---|---|---|
| `chunker` | 11 | Sentence splitting: punctuation, paragraphs, long runs, unicode |
| `parity` | 5 | The Python and JavaScript chunkers produce identical sentences |
| `engine` | 11 | Live round trip against the PowerShell SAPI server, including WAV output |
| `app` | 16 | Drives the real Tk window: playback, highlighting, seeking, settings |
| `global` | 27 | Real system hotkeys and tray icon, menu commands, clipboard capture |
| `dom` | 18 | Extension text extraction, DOM range mapping and highlighting, in Chrome |

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

**`Ctrl+Alt+R` does nothing.** Another program may already own that combination —
the window shows which hotkeys failed to register. A second copy of Read Aloud
Anywhere already running will do that too, so check the tray first. Also check
the target app is not running as administrator.

**No W icon by the clock.** Windows 11 hides new tray icons: click the `^` next
to the clock and drag the W out onto the taskbar.

**The extension's right-click entry is missing.** Reload the extension at
`chrome://extensions`, then reload the page. Content scripts are not injected
into `chrome://` pages, the Chrome Web Store, or PDFs.

**Nothing reads on one particular site.** Some pages render text inside a canvas
or a cross-origin frame that extensions cannot reach. Copy the text and use the
popup's paste box.
