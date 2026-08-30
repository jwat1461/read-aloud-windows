"""System tray icon and global hotkeys for Read Aloud Anywhere.

Both live on one dedicated Win32 thread, because they have to: RegisterHotKey
delivers WM_HOTKEY to the thread that registered it, and a tray icon needs a
window with a message loop. One loop serves both — and, since the window is
already there, it also carries the clipboard-format listener that drives
auto-read, so nothing has to poll the clipboard.

The Tk side never touches Win32. It reads events off a queue and pushes a state
snapshot back down, so the menu can show the current voice, speed and volume
without either thread reaching into the other's objects.
"""

from __future__ import annotations

import ctypes
import queue
import threading
from ctypes import wintypes
from pathlib import Path

user32 = ctypes.WinDLL("user32", use_last_error=True)
shell32 = ctypes.WinDLL("shell32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

# ------------------------------------------------------------------ constants

WM_DESTROY = 0x0002
WM_CLOSE = 0x0010
WM_QUIT = 0x0012
WM_HOTKEY = 0x0312
WM_NULL = 0x0000
WM_LBUTTONUP = 0x0202
WM_LBUTTONDBLCLK = 0x0203
WM_RBUTTONUP = 0x0205
WM_CONTEXTMENU = 0x007B
WM_CLIPBOARDUPDATE = 0x031D

WM_APP = 0x8000
WM_TRAYICON = WM_APP + 1
WM_REFRESH_TIP = WM_APP + 2
WM_BALLOON = WM_APP + 3

NIM_ADD, NIM_MODIFY, NIM_DELETE = 0, 1, 2
NIF_MESSAGE, NIF_ICON, NIF_TIP, NIF_INFO = 0x01, 0x02, 0x04, 0x10
NIIF_INFO, NIIF_WARNING = 0x01, 0x02

IMAGE_ICON = 1
LR_LOADFROMFILE = 0x0010
LR_DEFAULTSIZE = 0x0040

MF_STRING = 0x0000
MF_GRAYED = 0x0001
MF_CHECKED = 0x0008
MF_POPUP = 0x0010
MF_SEPARATOR = 0x0800

TPM_RIGHTBUTTON = 0x0002
TPM_RETURNCMD = 0x0100

MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_NOREPEAT = 0x4000

WS_OVERLAPPED = 0x00000000
CW_USEDEFAULT = -0x80000000

LRESULT = ctypes.c_ssize_t
WNDPROC = ctypes.WINFUNCTYPE(
    LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM
)

# Menu command ids. Voices, speeds and volumes are numbered from their bases.
CMD_SHOW = 1
CMD_READ_CLIPBOARD = 2
CMD_READ_SELECTION = 3
CMD_PAUSE = 4
CMD_STOP = 5
CMD_QUIT = 6
CMD_AUTO_READ = 7
CMD_NEXT = 8
CMD_SUMMARY = 9
CMD_FULL = 10
CMD_BRIEF = 11
CMD_VOICE_BASE = 1000
CMD_RATE_BASE = 2000
CMD_VOLUME_BASE = 3000

RATE_PRESETS = [
    (-8, "Very slow"),
    (-4, "Slow"),
    (0, "Normal"),
    (3, "Fast"),
    (6, "Faster"),
    (10, "Fastest"),
]
VOLUME_PRESETS = [(0, "Mute"), (25, "25%"), (50, "50%"), (75, "75%"), (100, "100%")]


# ------------------------------------------------------------------ structs


class WNDCLASSW(ctypes.Structure):
    _fields_ = [
        ("style", wintypes.UINT),
        ("lpfnWndProc", WNDPROC),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", wintypes.HINSTANCE),
        ("hIcon", wintypes.HICON),
        ("hCursor", wintypes.HANDLE),
        ("hbrBackground", wintypes.HANDLE),
        ("lpszMenuName", wintypes.LPCWSTR),
        ("lpszClassName", wintypes.LPCWSTR),
    ]


class NOTIFYICONDATAW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("hWnd", wintypes.HWND),
        ("uID", wintypes.UINT),
        ("uFlags", wintypes.UINT),
        ("uCallbackMessage", wintypes.UINT),
        ("hIcon", wintypes.HICON),
        ("szTip", wintypes.WCHAR * 128),
        ("dwState", wintypes.DWORD),
        ("dwStateMask", wintypes.DWORD),
        ("szInfo", wintypes.WCHAR * 256),
        ("uVersion", wintypes.UINT),
        ("szInfoTitle", wintypes.WCHAR * 64),
        ("dwInfoFlags", wintypes.DWORD),
        ("guidItem", ctypes.c_byte * 16),
        ("hBalloonIcon", wintypes.HICON),
    ]


# Explicit prototypes: without them ctypes truncates 64-bit handles to int.
user32.DefWindowProcW.restype = LRESULT
user32.DefWindowProcW.argtypes = [
    wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM
]
user32.RegisterClassW.restype = wintypes.ATOM
user32.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASSW)]
user32.CreateWindowExW.restype = wintypes.HWND
user32.CreateWindowExW.argtypes = [
    wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
    ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
    wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID,
]
user32.DestroyWindow.argtypes = [wintypes.HWND]
user32.LoadImageW.restype = wintypes.HANDLE
user32.LoadImageW.argtypes = [
    wintypes.HINSTANCE, wintypes.LPCWSTR, wintypes.UINT,
    ctypes.c_int, ctypes.c_int, wintypes.UINT,
]
user32.DestroyIcon.argtypes = [wintypes.HICON]
user32.CreatePopupMenu.restype = wintypes.HMENU
user32.AppendMenuW.argtypes = [
    wintypes.HMENU, wintypes.UINT, ctypes.c_size_t, wintypes.LPCWSTR
]
user32.DestroyMenu.argtypes = [wintypes.HMENU]
user32.TrackPopupMenu.restype = ctypes.c_int
user32.TrackPopupMenu.argtypes = [
    wintypes.HMENU, wintypes.UINT, ctypes.c_int, ctypes.c_int,
    ctypes.c_int, wintypes.HWND, wintypes.LPVOID,
]
user32.SetForegroundWindow.argtypes = [wintypes.HWND]
user32.PostMessageW.argtypes = [
    wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM
]
user32.PostThreadMessageW.argtypes = [
    wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM
]
user32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
user32.RegisterHotKey.restype = wintypes.BOOL
user32.RegisterHotKey.argtypes = [
    wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT
]
user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
user32.GetMessageW.argtypes = [
    ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT
]
user32.AddClipboardFormatListener.restype = wintypes.BOOL
user32.AddClipboardFormatListener.argtypes = [wintypes.HWND]
user32.RemoveClipboardFormatListener.restype = wintypes.BOOL
user32.RemoveClipboardFormatListener.argtypes = [wintypes.HWND]
user32.GetClipboardSequenceNumber.restype = wintypes.DWORD
user32.GetClipboardSequenceNumber.argtypes = []
user32.RegisterWindowMessageW.restype = wintypes.UINT
user32.RegisterWindowMessageW.argtypes = [wintypes.LPCWSTR]

# Explorer broadcasts this to every top-level window after it restarts. Every
# tray icon is destroyed when it goes, and it is each app's job to put its own
# back -- an app that does not simply disappears from the notification area and
# looks, from the outside, exactly like it crashed.
WM_TASKBARCREATED = user32.RegisterWindowMessageW("TaskbarCreated")
shell32.Shell_NotifyIconW.restype = wintypes.BOOL
shell32.Shell_NotifyIconW.argtypes = [wintypes.DWORD, ctypes.POINTER(NOTIFYICONDATAW)]
kernel32.GetModuleHandleW.restype = wintypes.HMODULE
kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]


def clipboard_sequence() -> int:
    """Bumps on every clipboard change in the session, our own writes included."""
    return int(user32.GetClipboardSequenceNumber())


class TrayBackend(threading.Thread):
    """Owns the tray icon, the popup menu and the system-wide hotkeys."""

    def __init__(
        self,
        events: queue.Queue,
        icon_path: Path,
        hotkeys: dict,
        tooltip: str = "Read Aloud",
    ) -> None:
        super().__init__(daemon=True, name="ReadAloudTray")
        self.events = events
        self.icon_path = Path(icon_path)
        self.hotkeys = hotkeys
        self.base_tooltip = tooltip

        self.ready = threading.Event()
        self.failed_hotkeys: list[str] = []
        self.tray_ok = False
        self.clipboard_listener_ok = False

        self._thread_id: int | None = None
        self._hwnd = None
        self._hicon = None
        self._nid = None
        self._balloon: tuple | None = None
        self._lock = threading.Lock()
        self._snapshot: dict = {
            "voices": [],
            "voice": "",
            "rate": 0,
            "volume": 100,
            "state": "idle",
            "status": "",
            "auto_read": False,
            "summary_mode": False,
            "queued": 0,
            "queue_dropped": False,
        }
        # WNDPROC must outlive the window or Windows calls into freed memory.
        self._wndproc = WNDPROC(self._on_message)

    # ------------------------------------------------------------- public API

    def set_snapshot(self, **values) -> None:
        with self._lock:
            self._snapshot.update(values)
        if self._hwnd:
            user32.PostMessageW(self._hwnd, WM_REFRESH_TIP, 0, 0)

    def snapshot(self) -> dict:
        with self._lock:
            return dict(self._snapshot)

    def notify(self, title: str, message: str, warning: bool = False) -> bool:
        """Pop a balloon by the clock. False if there is no icon to hang it on."""
        if not self._hwnd or not self.tray_ok:
            return False
        with self._lock:
            # szInfoTitle is 64 wide chars, szInfo 256, terminators included.
            self._balloon = (title[:63], message[:255], warning)
        return bool(user32.PostMessageW(self._hwnd, WM_BALLOON, 0, 0))

    def stop(self, timeout: float = 3.0) -> None:
        """Ask the loop to exit and wait for it.

        Waiting matters: the icon is only removed as the thread unwinds, and the
        hotkeys stay registered until then. Returning early leaves a ghost icon
        by the clock and blocks the next process from claiming Ctrl+Alt+R.
        """
        if self._thread_id is None:
            return
        user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)
        if self.is_alive() and threading.current_thread() is not self:
            self.join(timeout=timeout)

    # ------------------------------------------------------------- menu build

    def _build_menu(self):
        snap = self.snapshot()
        menu = user32.CreatePopupMenu()

        state = snap["state"]
        reading = state != "idle"

        user32.AppendMenuW(menu, MF_STRING, CMD_SHOW, "Open Read Aloud")
        user32.AppendMenuW(menu, MF_SEPARATOR, 0, None)

        user32.AppendMenuW(
            menu, MF_STRING, CMD_READ_SELECTION, "Read selection\tCtrl+Alt+R"
        )
        user32.AppendMenuW(
            menu, MF_STRING, CMD_READ_CLIPBOARD, "Read clipboard\tCtrl+Alt+C"
        )
        user32.AppendMenuW(
            menu,
            MF_STRING | (MF_CHECKED if snap.get("auto_read") else 0),
            CMD_AUTO_READ,
            "Auto-read clipboard\tCtrl+Alt+A",
        )
        user32.AppendMenuW(
            menu,
            MF_STRING | (MF_CHECKED if snap.get("summary_mode") else 0),
            CMD_SUMMARY,
            "Summary mode\tCtrl+Alt+S",
        )
        user32.AppendMenuW(
            menu, MF_STRING, CMD_BRIEF, "Brief me\tCtrl+Alt+B"
        )
        user32.AppendMenuW(
            menu, MF_STRING, CMD_FULL, "Read full text\tCtrl+Alt+F"
        )
        user32.AppendMenuW(
            menu,
            MF_STRING | (0 if reading else MF_GRAYED),
            CMD_PAUSE,
            ("Resume\tCtrl+Alt+P" if state == "paused" else "Pause\tCtrl+Alt+P"),
        )
        user32.AppendMenuW(
            menu,
            MF_STRING | (0 if reading else MF_GRAYED),
            CMD_NEXT,
            "Skip to next\tCtrl+Alt+N",
        )
        user32.AppendMenuW(
            menu,
            MF_STRING | (0 if reading else MF_GRAYED),
            CMD_STOP,
            "Stop reading\tCtrl+Alt+X",
        )
        user32.AppendMenuW(menu, MF_SEPARATOR, 0, None)

        # --- Voice ---
        voice_menu = user32.CreatePopupMenu()
        for i, name in enumerate(snap["voices"]):
            flags = MF_STRING | (MF_CHECKED if name == snap["voice"] else 0)
            user32.AppendMenuW(voice_menu, flags, CMD_VOICE_BASE + i, name)
        if not snap["voices"]:
            user32.AppendMenuW(voice_menu, MF_STRING | MF_GRAYED, 0, "(loading…)")
        user32.AppendMenuW(menu, MF_POPUP, voice_menu, "Voice")

        # --- Speed ---
        rate_menu = user32.CreatePopupMenu()
        for i, (value, label) in enumerate(RATE_PRESETS):
            flags = MF_STRING | (MF_CHECKED if value == snap["rate"] else 0)
            user32.AppendMenuW(rate_menu, flags, CMD_RATE_BASE + i, f"{label}\t{value:+d}")
        user32.AppendMenuW(menu, MF_POPUP, rate_menu, "Speed")

        # --- Volume ---
        volume_menu = user32.CreatePopupMenu()
        for i, (value, label) in enumerate(VOLUME_PRESETS):
            flags = MF_STRING | (MF_CHECKED if value == snap["volume"] else 0)
            user32.AppendMenuW(volume_menu, flags, CMD_VOLUME_BASE + i, label)
        user32.AppendMenuW(menu, MF_POPUP, volume_menu, "Volume")

        user32.AppendMenuW(menu, MF_SEPARATOR, 0, None)
        user32.AppendMenuW(menu, MF_STRING, CMD_QUIT, "Quit")

        return menu, [voice_menu, rate_menu, volume_menu]

    def _show_menu(self) -> None:
        menu, submenus = self._build_menu()
        point = wintypes.POINT()
        user32.GetCursorPos(ctypes.byref(point))

        # Required so the menu closes when the user clicks elsewhere.
        user32.SetForegroundWindow(self._hwnd)
        command = user32.TrackPopupMenu(
            menu,
            TPM_RIGHTBUTTON | TPM_RETURNCMD,
            point.x,
            point.y,
            0,
            self._hwnd,
            None,
        )
        user32.PostMessageW(self._hwnd, WM_NULL, 0, 0)

        for handle in submenus:
            user32.DestroyMenu(handle)
        user32.DestroyMenu(menu)

        if command:
            self.events.put(("menu", int(command)))

    # ---------------------------------------------------------------- wndproc

    def _on_message(self, hwnd, message, wparam, lparam):
        if message == WM_TRAYICON:
            event = lparam & 0xFFFF
            if event in (WM_RBUTTONUP, WM_CONTEXTMENU):
                self._show_menu()
            elif event in (WM_LBUTTONUP, WM_LBUTTONDBLCLK):
                self.events.put(("menu", CMD_SHOW))
            return 0

        if message and message == WM_TASKBARCREATED:
            self.tray_ok = self._add_icon()
            return 0

        if message == WM_CLIPBOARDUPDATE:
            # With auto-read off there is nothing to say, and every copy anyone
            # makes would otherwise cross to the Tk thread for nothing. The
            # sequence number is read here, on the thread the message arrived
            # on, so the Tk side can tell our own writes from everybody else's.
            with self._lock:
                wanted = bool(self._snapshot.get("auto_read"))
            if wanted:
                self.events.put(("clipboard", clipboard_sequence()))
            return 0

        if message == WM_REFRESH_TIP:
            self._update_tooltip()
            return 0

        if message == WM_BALLOON:
            self._show_balloon()
            return 0

        if message == WM_CLOSE:
            user32.DestroyWindow(hwnd)
            return 0

        if message == WM_DESTROY:
            self._remove_icon()
            user32.PostQuitMessage(0)
            return 0

        return user32.DefWindowProcW(hwnd, message, wparam, lparam)

    # ------------------------------------------------------------- tray icon

    def tooltip_text(self) -> str:
        snap = self.snapshot()
        auto = "Auto-read: " + ("on" if snap.get("auto_read") else "off")
        queued = int(snap.get("queued") or 0)
        if queued:
            auto += f" · {queued} queued"
            if snap.get("queue_dropped"):
                auto += " · oldest dropped"

        if snap.get("summary_mode"):
            auto += " \u00b7 summary"

        lines = [self.base_tooltip, auto]
        status = snap.get("status") or ""
        if status:
            lines.append(status)
        # szTip is 128 wide chars including the terminator.
        return "\n".join(lines)[:127]

    def _show_balloon(self) -> None:
        with self._lock:
            balloon, self._balloon = self._balloon, None
        if not self._nid or not balloon:
            return
        title, text, warning = balloon
        self._nid.szInfoTitle = title
        self._nid.szInfo = text
        self._nid.dwInfoFlags = NIIF_WARNING if warning else NIIF_INFO
        self._nid.uFlags = NIF_INFO
        shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(self._nid))

    def _update_tooltip(self) -> None:
        if not self._nid:
            return
        self._nid.szTip = self.tooltip_text()
        self._nid.uFlags = NIF_TIP
        shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(self._nid))

    def readd_icon(self) -> bool:
        """Put the icon back. Safe to call when it is already there."""
        if not self._hwnd:
            return False
        self._remove_icon()
        self.tray_ok = self._add_icon()
        return self.tray_ok

    def _add_icon(self) -> bool:
        hicon = None
        if self.icon_path.exists():
            hicon = user32.LoadImageW(
                None, str(self.icon_path), IMAGE_ICON, 0, 0,
                LR_LOADFROMFILE | LR_DEFAULTSIZE,
            )
        if not hicon:
            # IDI_APPLICATION, so the tray still gets an icon if ours is missing.
            hicon = user32.LoadIconW(None, ctypes.c_wchar_p(32512))
        self._hicon = hicon

        nid = NOTIFYICONDATAW()
        nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
        nid.hWnd = self._hwnd
        nid.uID = 1
        nid.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
        nid.uCallbackMessage = WM_TRAYICON
        nid.hIcon = hicon
        nid.szTip = self.tooltip_text()
        self._nid = nid

        return bool(shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(nid)))

    def _remove_icon(self) -> None:
        if self._nid:
            shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(self._nid))
            self._nid = None
        if self._hicon:
            user32.DestroyIcon(self._hicon)
            self._hicon = None

    # ------------------------------------------------------------- main loop

    def run(self) -> None:
        self._thread_id = kernel32.GetCurrentThreadId()
        instance = kernel32.GetModuleHandleW(None)

        cls = WNDCLASSW()
        cls.lpfnWndProc = self._wndproc
        cls.hInstance = instance
        cls.lpszClassName = f"ReadAloudTray{self._thread_id}"
        atom = user32.RegisterClassW(ctypes.byref(cls))

        if atom:
            self._hwnd = user32.CreateWindowExW(
                0, cls.lpszClassName, "Read Aloud", WS_OVERLAPPED,
                CW_USEDEFAULT, CW_USEDEFAULT, 0, 0,
                None, None, instance, None,
            )

        if self._hwnd:
            self.tray_ok = self._add_icon()
            self.clipboard_listener_ok = bool(
                user32.AddClipboardFormatListener(self._hwnd)
            )

        registered = []
        for hotkey_id, (mods, vk, label, _desc) in self.hotkeys.items():
            if user32.RegisterHotKey(None, hotkey_id, mods, vk):
                registered.append(hotkey_id)
            else:
                self.failed_hotkeys.append(label)

        self.ready.set()

        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            if msg.message == WM_HOTKEY:
                self.events.put(("hotkey", int(msg.wParam)))
                continue
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

        for hotkey_id in registered:
            user32.UnregisterHotKey(None, hotkey_id)
        if self.clipboard_listener_ok and self._hwnd:
            user32.RemoveClipboardFormatListener(self._hwnd)
            self.clipboard_listener_ok = False
        self._remove_icon()
        if self._hwnd:
            user32.DestroyWindow(self._hwnd)
            self._hwnd = None
