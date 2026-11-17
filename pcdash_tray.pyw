"""PCDash - one program for the Onn screen, living in the system tray.

  * runs the dashboard server (dash.py) in this process
  * opens ONE window - Stream Control on top, PC Dashboard below (/onn) -
    filling the portrait monitor's working area
  * keeps that window off the taskbar (it is marked as a tool window)
  * puts an icon in the notification area (it lands in "hidden icons"):
        left-click   show / hide the window
        right-click  menu: Show/Hide, Reopen window, Open OBS, Quit PCDash

Run with pythonw (no console):  pythonw pcdash_tray.pyw [--delay 20]
Uses only ctypes + Pillow, no extra packages.
"""
from __future__ import annotations

import ctypes
import os
import socket
import subprocess
import sys
import threading
import time
from ctypes import wintypes

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
PORT = 8095
URL = f"http://127.0.0.1:{PORT}/onn"
TITLE = "PCDash"
EDGE_PROFILE = os.path.join(os.environ.get("LOCALAPPDATA", HERE), "PCDash", "edge-profile")
ICON_PATH = os.path.join(HERE, "cache", "pcdash.ico")

user32 = ctypes.WinDLL("user32", use_last_error=True)
shell32 = ctypes.WinDLL("shell32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

LRESULT = ctypes.c_ssize_t
WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.DefWindowProcW.restype = LRESULT
user32.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
user32.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
user32.SetWindowLongPtrW.restype = ctypes.c_ssize_t
user32.CreateWindowExW.restype = wintypes.HWND
user32.CreateWindowExW.argtypes = [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
                                   ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                   wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID]
user32.LoadImageW.restype = wintypes.HANDLE
user32.CreatePopupMenu.restype = wintypes.HMENU
user32.TrackPopupMenu.argtypes = [wintypes.HMENU, wintypes.UINT, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                  wintypes.HWND, wintypes.LPVOID]

GWL_EXSTYLE = -20
WS_EX_TOOLWINDOW, WS_EX_APPWINDOW = 0x80, 0x40000
SW_HIDE, SW_SHOWNOACTIVATE, SW_SHOW = 0, 4, 5
SWP_NOZORDER, SWP_NOACTIVATE, SWP_FRAMECHANGED = 0x4, 0x10, 0x20
WM_APP, WM_LBUTTONUP, WM_RBUTTONUP, WM_COMMAND, WM_DESTROY = 0x8000, 0x202, 0x205, 0x111, 0x2
NIM_ADD, NIM_MODIFY, NIM_DELETE = 0, 1, 2
NIF_MESSAGE, NIF_ICON, NIF_TIP = 1, 2, 4
TRAY_MSG = WM_APP + 1
MF_STRING, MF_SEPARATOR, TPM_RETURNCMD, TPM_RIGHTBUTTON = 0, 0x800, 0x100, 0x2
ID_TOGGLE, ID_REOPEN, ID_OBS, ID_ARK, ID_SAVER, ID_QUIT = 1, 2, 3, 4, 5, 9


def log(msg: str) -> None:
    try:
        with open(os.path.join(HERE, "dash.log"), "a", encoding="utf-8") as fh:
            fh.write(time.strftime("%Y-%m-%d %H:%M:%S ") + "tray: " + msg + "\n")
    except OSError:
        pass


# ------------------------------------------------------------------ server --
def port_open() -> bool:
    with socket.socket() as s:
        s.settimeout(0.3)
        return s.connect_ex(("127.0.0.1", PORT)) == 0


def start_server() -> None:
    if port_open():
        log("dashboard server already running - using it")
        return
    import dash
    threading.Thread(target=dash.main, daemon=True, name="dash").start()
    for _ in range(60):
        if port_open():
            return
        time.sleep(0.25)


# ------------------------------------------------------------------ window --
class RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long), ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


class MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", RECT), ("rcWork", RECT), ("dwFlags", wintypes.DWORD)]


def portrait_work_area() -> tuple[int, int, int, int]:
    """Working area (minus its taskbar) of the first monitor taller than wide; else primary."""
    found = []
    MONITORENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HMONITOR, wintypes.HDC, ctypes.POINTER(RECT), wintypes.LPARAM)

    def cb(hmon, _hdc, _rc, _lp):
        mi = MONITORINFO(); mi.cbSize = ctypes.sizeof(mi)
        user32.GetMonitorInfoW(hmon, ctypes.byref(mi))
        w = mi.rcWork
        found.append(((w.left, w.top, w.right - w.left, w.bottom - w.top), bool(mi.dwFlags & 1),
                      (mi.rcMonitor.bottom - mi.rcMonitor.top) > (mi.rcMonitor.right - mi.rcMonitor.left)))
        return True
    user32.EnumDisplayMonitors(None, None, MONITORENUMPROC(cb), 0)
    for area, _primary, portrait in found:
        if portrait:
            return area
    return next((a for a, p, _ in found if p), found[0][0])


_dead: set[int] = set()   # hidden "PCDash" windows Edge leaves behind after a close; they won't show


def find_window() -> int | None:
    """Our Edge app window: a visible one if there is one, else a hidden one
    (the one we hid). Leftovers known not to show again are skipped."""
    hits = []
    EnumProc = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)

    def cb(h, _):
        buf = ctypes.create_unicode_buffer(64)
        user32.GetWindowTextW(h, buf, 64)
        cls = ctypes.create_unicode_buffer(64)
        user32.GetClassNameW(h, cls, 64)
        if buf.value == TITLE and cls.value.startswith("Chrome_WidgetWin") and h not in _dead:
            hits.append(h)
        return True
    user32.EnumWindows(EnumProc(cb), 0)
    visible = [h for h in hits if user32.IsWindowVisible(h)]
    return (visible or hits or [None])[0]


def close_old_windows() -> None:
    """The two separate windows from before the merge."""
    EnumProc = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)

    def cb(h, _):
        buf = ctypes.create_unicode_buffer(64)
        user32.GetWindowTextW(h, buf, 64)
        if buf.value in ("PC Dashboard", "Stream Control") and user32.IsWindowVisible(h):
            user32.PostMessageW(h, 0x10, 0, 0)   # WM_CLOSE
        return True
    user32.EnumWindows(EnumProc(cb), 0)


def edge_exe() -> str:
    for base in (os.environ.get("ProgramFiles(x86)"), os.environ.get("ProgramFiles")):
        p = os.path.join(base or "", "Microsoft", "Edge", "Application", "msedge.exe")
        if os.path.isfile(p):
            return p
    return "msedge.exe"


def dock(hwnd: int) -> None:
    """Fill the portrait monitor and keep the window off the taskbar."""
    x, y, w, h = portrait_work_area()
    ex = user32.GetWindowLongPtrW(hwnd, GWL_EXSTYLE)
    want = (ex | WS_EX_TOOLWINDOW) & ~WS_EX_APPWINDOW
    if want != ex:
        # The taskbar only re-reads the style when the window is shown again.
        visible = user32.IsWindowVisible(hwnd)
        user32.ShowWindow(hwnd, SW_HIDE)
        user32.SetWindowLongPtrW(hwnd, GWL_EXSTYLE, want)
        if visible:
            user32.ShowWindow(hwnd, SW_SHOWNOACTIVATE)
    user32.ShowWindow(hwnd, 9) if user32.IsZoomed(hwnd) or user32.IsIconic(hwnd) else None   # SW_RESTORE
    user32.SetWindowPos(hwnd, None, x, y, w, h, SWP_NOZORDER | SWP_NOACTIVATE | SWP_FRAMECHANGED)


def open_window() -> int | None:
    hwnd = find_window()
    if hwnd and not user32.IsWindowVisible(hwnd):
        # Ours, hidden by the tray - or a leftover from a closed window that
        # will never show again (seen 2026-11-11). Try; if it stays hidden, drop it.
        user32.ShowWindow(hwnd, SW_SHOWNOACTIVATE)
        time.sleep(0.6)
        if not user32.IsWindowVisible(hwnd):
            _dead.add(hwnd)
            log("ignoring a leftover hidden window; opening a new one")
            hwnd = find_window()
            if hwnd and not user32.IsWindowVisible(hwnd):
                hwnd = None
    if not hwnd:
        x, y, w, h = portrait_work_area()
        subprocess.Popen([edge_exe(), f"--app={URL}", f"--user-data-dir={EDGE_PROFILE}", "--no-first-run",
                          f"--window-position={x},{y}", f"--window-size={w},{h}"])
        for _ in range(60):
            time.sleep(0.25)
            hwnd = find_window()
            if hwnd:
                time.sleep(0.5)
                break
    if hwnd:
        dock(hwnd)
        user32.ShowWindow(hwnd, SW_SHOWNOACTIVATE)
    return hwnd


def toggle_window() -> None:
    hwnd = find_window()
    if hwnd and user32.IsWindowVisible(hwnd):
        user32.ShowWindow(hwnd, SW_HIDE)
    else:
        open_window()


def reopen_window() -> None:
    hwnd = find_window()
    if hwnd:
        user32.PostMessageW(hwnd, 0x10, 0, 0)
        time.sleep(1.5)
    open_window()


def open_obs() -> None:
    exe = r"C:\Program Files\obs-studio\bin\64bit\obs64.exe"
    if os.path.isfile(exe):
        subprocess.Popen([exe], cwd=os.path.dirname(exe))


def keep_docked() -> None:
    """Edge occasionally restores its own placement/style (after a reload, a
    monitor change, waking from sleep); put it back every few seconds."""
    while True:
        time.sleep(5)
        try:
            hwnd = find_window()
            if hwnd and user32.IsWindowVisible(hwnd):
                ex = user32.GetWindowLongPtrW(hwnd, GWL_EXSTYLE)
                r = RECT(); user32.GetWindowRect(hwnd, ctypes.byref(r))
                x, y, w, h = portrait_work_area()
                if not (ex & WS_EX_TOOLWINDOW) or abs(r.left - x) > 20 or abs(r.top - y) > 20 or abs((r.bottom - r.top) - h) > 40:
                    dock(hwnd)
        except Exception as exc:  # noqa: BLE001
            log(f"keep_docked: {exc}")


# -------------------------------------------------------------------- tray --
class NOTIFYICONDATAW(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("hWnd", wintypes.HWND), ("uID", wintypes.UINT),
                ("uFlags", wintypes.UINT), ("uCallbackMessage", wintypes.UINT), ("hIcon", wintypes.HICON),
                ("szTip", ctypes.c_wchar * 128), ("dwState", wintypes.DWORD), ("dwStateMask", wintypes.DWORD),
                ("szInfo", ctypes.c_wchar * 256), ("uVersion", wintypes.UINT), ("szInfoTitle", ctypes.c_wchar * 64),
                ("dwInfoFlags", wintypes.DWORD), ("guidItem", ctypes.c_byte * 16), ("hBalloonIcon", wintypes.HICON)]


def make_icon() -> None:
    """A small gauge icon, drawn once with Pillow."""
    if os.path.isfile(ICON_PATH):
        return
    from PIL import Image, ImageDraw
    os.makedirs(os.path.dirname(ICON_PATH), exist_ok=True)
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((2, 2, 62, 62), 14, fill=(20, 25, 36, 255))
    d.arc((10, 12, 54, 56), 150, 390, fill=(60, 70, 90, 255), width=7)
    d.arc((10, 12, 54, 56), 150, 300, fill=(76, 194, 255, 255), width=7)
    d.ellipse((27, 29, 37, 39), fill=(255, 45, 75, 255))
    img.save(ICON_PATH, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64)])


class Tray:
    def __init__(self):
        self.hinst = kernel32.GetModuleHandleW(None)
        self._proc = WNDPROC(self.wndproc)   # keep a reference
        wc_cls = "PCDashTray"

        class WNDCLASSW(ctypes.Structure):
            _fields_ = [("style", wintypes.UINT), ("lpfnWndProc", WNDPROC), ("cbClsExtra", ctypes.c_int),
                        ("cbWndExtra", ctypes.c_int), ("hInstance", wintypes.HINSTANCE), ("hIcon", wintypes.HICON),
                        ("hCursor", wintypes.HANDLE), ("hbrBackground", wintypes.HBRUSH),
                        ("lpszMenuName", wintypes.LPCWSTR), ("lpszClassName", wintypes.LPCWSTR)]
        wc = WNDCLASSW(0, self._proc, 0, 0, self.hinst, None, None, None, None, wc_cls)
        user32.RegisterClassW(ctypes.byref(wc))
        self.hwnd = user32.CreateWindowExW(0, wc_cls, "PCDash tray", 0, 0, 0, 0, 0, None, None, self.hinst, None)
        self.taskbar_created = user32.RegisterWindowMessageW("TaskbarCreated")
        make_icon()
        self.hicon = user32.LoadImageW(None, ICON_PATH, 1, 0, 0, 0x10 | 0x40)   # IMAGE_ICON, LR_LOADFROMFILE|LR_DEFAULTSIZE
        self.add()

    def nid(self) -> NOTIFYICONDATAW:
        n = NOTIFYICONDATAW()
        n.cbSize = ctypes.sizeof(n)
        n.hWnd, n.uID = self.hwnd, 1
        n.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
        n.uCallbackMessage, n.hIcon = TRAY_MSG, self.hicon
        n.szTip = "PCDash - click to show or hide"
        return n

    def add(self):
        ok = shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(self.nid()))
        log(f"tray icon {'added' if ok else 'FAILED to add'}")

    def remove(self):
        shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(self.nid()))

    def menu(self):
        hwnd = find_window()
        shown = bool(hwnd and user32.IsWindowVisible(hwnd))
        m = user32.CreatePopupMenu()
        user32.AppendMenuW(m, MF_STRING, ID_TOGGLE, "Hide PCDash" if shown else "Show PCDash")
        user32.AppendMenuW(m, MF_STRING, ID_REOPEN, "Reopen window")
        user32.AppendMenuW(m, MF_STRING, ID_ARK, "Show ArkPanel")
        user32.AppendMenuW(m, MF_STRING, ID_OBS, "Open OBS")
        user32.AppendMenuW(m, MF_STRING, ID_SAVER, "Start screensaver")
        user32.AppendMenuW(m, MF_SEPARATOR, 0, None)
        user32.AppendMenuW(m, MF_STRING, ID_QUIT, "Quit PCDash")
        pt = wintypes.POINT()
        user32.GetCursorPos(ctypes.byref(pt))
        user32.SetForegroundWindow(self.hwnd)        # so the menu closes when you click away
        cmd = user32.TrackPopupMenu(m, TPM_RETURNCMD | TPM_RIGHTBUTTON, pt.x, pt.y, 0, self.hwnd, None)
        user32.DestroyMenu(m)
        self.run_cmd(cmd)

    def run_cmd(self, cmd):
        def bg(fn):
            threading.Thread(target=fn, daemon=True).start()
        if cmd == ID_TOGGLE:
            bg(toggle_window)
        elif cmd == ID_REOPEN:
            bg(reopen_window)
        elif cmd == ID_OBS:
            bg(open_obs)
        elif cmd == ID_SAVER:
            import saver
            saver.start_now()
        elif cmd == ID_ARK:
            show_arkpanel()                          # on this thread: it holds the foreground right
        elif cmd == ID_QUIT:
            log("quit from tray")
            try:
                import saver
                saver._close_all()
            except Exception:
                pass
            hwnd = find_window()
            if hwnd:
                user32.PostMessageW(hwnd, 0x10, 0, 0)
            self.remove()
            try:
                import taskbar_hide
                taskbar_hide.restore()               # give Discord/ArkPanel their buttons back
            except Exception:
                pass
            os._exit(0)                              # stops the in-process server threads too

    def wndproc(self, hwnd, msg, wparam, lparam):
        if msg == TRAY_MSG:
            if lparam == WM_LBUTTONUP:
                threading.Thread(target=toggle_window, daemon=True).start()
            elif lparam == WM_RBUTTONUP:
                self.menu()
            return 0
        if msg == self.taskbar_created:              # Explorer restarted: put the icon back
            self.add()
            return 0
        if msg == WM_DESTROY:
            self.remove()
            user32.PostQuitMessage(0)
            return 0
        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    def loop(self):
        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))


ARKPANEL_APP = r"D:\Servers\ArkPanel\ArkPanel.py"


def show_arkpanel():
    """ArkPanel's desktop window has no taskbar button (taskbar_hide), so it is here."""
    import taskbar_hide
    taskbar_hide.show_arkpanel()


def single_instance() -> bool:
    kernel32.CreateMutexW(None, False, "Local\\PCDashTray")
    return ctypes.get_last_error() != 183              # ERROR_ALREADY_EXISTS


def main():
    if "--delay" in sys.argv:
        time.sleep(float(sys.argv[sys.argv.index("--delay") + 1]))
    if not single_instance():
        hwnd = find_window()                         # already running: just show the window
        if hwnd:
            user32.ShowWindow(hwnd, SW_SHOW)
        return
    log("starting")
    start_server()
    close_old_windows()
    tray = Tray()
    threading.Thread(target=open_window, daemon=True).start()
    threading.Thread(target=keep_docked, daemon=True).start()
    import taskbar_hide
    threading.Thread(target=taskbar_hide.run, args=(log,), daemon=True, name="taskbar").start()
    tray.loop()


if __name__ == "__main__":
    main()
