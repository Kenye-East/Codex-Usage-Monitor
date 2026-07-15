from __future__ import annotations

import ctypes
import logging
from ctypes import wintypes
from pathlib import Path
from threading import Thread, current_thread

from dataclasses import replace

from usage_overlay.config import AppConfig, ConfigStore, resolve_language
from usage_overlay.formatting import asset_path, icon_name, remaining, ui_font_family
from usage_overlay.i18n import text
from usage_overlay.models import ProviderResult
from usage_overlay.panel import PANEL_HEIGHT, PANEL_WIDTH, PanelController, badge_text
from usage_overlay.refresh import RefreshService

WIDTH, HEIGHT = 140, 40
DRAG_THRESHOLD = 4
MENU_ACTION_CODES = {"panel": 0, "settings": 1, "messages": 2, "exit": 3}
NATIVE_MENU_IDS = {100: "panel", 101: "settings", 102: "messages", 103: "exit"}
NATIVE_MENU_TEXT_KEYS = ((100, "menu_panel"), (101, "menu_settings"), (102, "menu_messages"), (103, "menu_exit"))

WM_PAINT, WM_CLOSE, WM_DESTROY, WM_LBUTTONDOWN, WM_LBUTTONUP, WM_MOUSEMOVE, WM_APP = 0x000F, 0x0010, 0x0002, 0x0201, 0x0202, 0x0200, 0x8000
WM_RBUTTONUP = 0x0205
WM_TIMER = 0x0113
WM_LANGUAGE_CHANGED, WM_SETTINGS_CHANGED, WM_MENU_ACTION = WM_APP + 3, WM_APP + 4, WM_APP + 5
WM_PROVIDER_DATA_READY = WM_APP + 6
WM_UNREAD_CHANGED = WM_APP + 7
WS_POPUP, WS_VISIBLE = 0x80000000, 0x10000000
WS_EX_TOPMOST, WS_EX_TOOLWINDOW, WS_EX_LAYERED = 0x00000008, 0x00000080, 0x00080000
GWL_HWNDPARENT, HWND_TOPMOST = -8, -1
SW_SHOW = 5
SWP_NOSIZE, SWP_NOZORDER = 0x0001, 0x0004
ULW_ALPHA, AC_SRC_OVER, AC_SRC_ALPHA, BI_RGB = 2, 0, 1, 0
SMOOTHING_MODE_ANTIALIAS, INTERPOLATION_MODE_HIGH_QUALITY_BICUBIC, PIXEL_OFFSET_MODE_HALF = 4, 7, 4
PIXEL_FORMAT_32BPP_PARGB = 0x000E200B
TEXT_RENDERING_HINT_ANTIALIAS_GRID_FIT = 3
FONT_STYLE_REGULAR, FONT_STYLE_BOLD, UNIT_PIXEL = 0, 1, 2
MF_STRING, MF_SEPARATOR = 0x0000, 0x0800
TPM_LEFTALIGN, TPM_BOTTOMALIGN, TPM_RETURNCMD = 0x0000, 0x0020, 0x0100

# Handle-sized parameters can exceed 32-bit range on 64-bit Windows; without explicit
# argtypes ctypes guesses c_int and raises OverflowError for real HWND/HDC values, or
# (for sentinel handles like HWND_TOPMOST = -1) mis-marshals the sign extension into the
# full 64-bit slot and Windows rejects the call outright (ERROR_INVALID_WINDOW_HANDLE).
ctypes.windll.user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
ctypes.windll.user32.DefWindowProcW.restype = ctypes.c_ssize_t
ctypes.windll.user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT]
ctypes.windll.user32.SetWindowPos.restype = wintypes.BOOL


def rgb(r: int, g: int, b: int) -> int:
    return (b << 16) | (g << 8) | r


def _rgb_components(colorref: int) -> tuple[int, int, int]:
    return colorref & 0xFF, (colorref >> 8) & 0xFF, (colorref >> 16) & 0xFF


def argb(a: int, r: int, g: int, b: int) -> int:
    return (a << 24) | (r << 16) | (g << 8) | b


PANEL_TEXT = rgb(20, 20, 20)


def compact_lines(result: ProviderResult, language: str) -> tuple[str, str]:
    return (
        _compact_line(text(language, "session"), result.session.percent),
        _compact_line(text(language, "weekly"), result.weekly.percent),
    )


def _compact_line(label: str, percent: int | None) -> str:
    shown = "—" if percent is None else f"{remaining(percent)}%"
    return f"{label} {shown}"


def clamp_taskbar_x(x: int, taskbar_width: int, overlay_width: int = WIDTH) -> int:
    return min(max(8, x), max(8, taskbar_width - overlay_width - 8))


def panel_origin(overlay_x: int, overlay_y: int, compact_width: int = WIDTH, panel_width: int = PANEL_WIDTH, panel_height: int = PANEL_HEIGHT) -> tuple[int, int]:
    return max(8, overlay_x + compact_width - panel_width), max(8, overlay_y - panel_height - 8)


def native_menu_items(language: str) -> tuple[tuple[int, str], ...]:
    return tuple((identifier, text(language, key)) for identifier, key in NATIVE_MENU_TEXT_KEYS)


class NativeOverlay:
    def __init__(self, service: RefreshService, store: ConfigStore) -> None:
        self.service, self.store, self.config = service, store, store.load()
        self.language = resolve_language(self.config.language)
        self.hwnd = 0
        self.height = HEIGHT
        self._drag_client_x: int | None = None
        self._drag_start_taskbar_x = 0
        self._drag_taskbar_width = 0
        self._dragging = False
        self._refreshing = False
        self._native_thread: Thread | None = None
        self._proc = WNDPROC(self._wndproc)
        self.panel = PanelController(
            service, store,
            on_language_changed=self._on_panel_language_changed,
            on_settings_changed=self._on_panel_settings_changed,
            on_menu_action=self._on_panel_menu_action,
            on_unread_changed=self._on_panel_unread_changed,
        )

    def start(self) -> None:
        self._native_thread = Thread(target=self._run_safely, name="CodexUsageMonitorNative", daemon=False)
        self._native_thread.start()

    def _run_safely(self) -> None:
        try:
            self.run()
        except Exception:
            logging.getLogger("codex_usage_monitor").exception("Native overlay stopped unexpectedly")
            self.panel.stop()

    def stop(self) -> None:
        if self.hwnd:
            ctypes.windll.user32.PostMessageW(self.hwnd, WM_CLOSE, 0, 0)
        if self._native_thread is not None and self._native_thread is not current_thread():
            self._native_thread.join(timeout=3)

    def run(self) -> None:
        hinstance = ctypes.windll.kernel32.GetModuleHandleW(None)
        taskbar = ctypes.windll.user32.FindWindowW("Shell_TrayWnd", None)
        if not taskbar:
            raise RuntimeError("Windows taskbar not found")
        klass = WNDCLASSW()
        klass.hInstance = hinstance
        klass.lpszClassName = "CodexUsageMonitorNative"
        klass.lpfnWndProc = self._proc
        klass.hCursor = ctypes.windll.user32.LoadCursorW(None, 32512)
        ctypes.windll.user32.RegisterClassW(ctypes.byref(klass))
        taskbar_rect = RECT()
        ctypes.windll.user32.GetWindowRect(taskbar, ctypes.byref(taskbar_rect))
        taskbar_width = taskbar_rect.right - taskbar_rect.left
        taskbar_height = taskbar_rect.bottom - taskbar_rect.top
        self.height = max(32, min(64, taskbar_height)) if taskbar_height > 0 else HEIGHT
        x = clamp_taskbar_x(self.config.taskbar_x, taskbar_width, WIDTH)
        if x != self.config.taskbar_x:
            self.config = replace(self.config, taskbar_x=x)
            self.store.save(self.config)
        y = taskbar_rect.top + max(0, (taskbar_height - self.height) // 2)
        self.hwnd = ctypes.windll.user32.CreateWindowExW(
            WS_EX_TOPMOST | WS_EX_TOOLWINDOW | WS_EX_LAYERED, klass.lpszClassName, "", WS_POPUP | WS_VISIBLE,
            x, y, WIDTH, self.height, None, None, hinstance, None)
        if not self.hwnd:
            raise ctypes.WinError()
        setter = ctypes.windll.user32.SetWindowLongPtrW
        setter.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_void_p]
        setter.restype = ctypes.c_void_p
        setter(self.hwnd, GWL_HWNDPARENT, taskbar)
        ctypes.windll.user32.SetWindowPos(self.hwnd, HWND_TOPMOST, x, y, WIDTH, self.height, 0x0040)
        ctypes.windll.user32.ShowWindow(self.hwnd, SW_SHOW)
        self._render_compact()
        self.panel.load_startup_notices()
        self.refresh()
        msg = MSG()
        while ctypes.windll.user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            ctypes.windll.user32.TranslateMessage(ctypes.byref(msg))
            ctypes.windll.user32.DispatchMessageW(ctypes.byref(msg))

    def refresh(self) -> None:
        if self._refreshing:
            return
        self._refreshing = True

        def fetch() -> None:
            def on_provider_done(_name: str) -> None:
                ctypes.windll.user32.PostMessageW(self.hwnd, WM_PROVIDER_DATA_READY, 0, 0)

            try:
                self.service.refresh_all(on_provider_done)
            finally:
                ctypes.windll.user32.PostMessageW(self.hwnd, WM_APP + 1, 0, 0)

        Thread(target=fetch, daemon=True).start()

    def _panel_position(self) -> tuple[int, int]:
        rect = RECT()
        ctypes.windll.user32.GetWindowRect(self.hwnd, ctypes.byref(rect))
        return panel_origin(rect.left, rect.top, WIDTH)

    def _toggle_panel(self) -> None:
        x, y = self._panel_position()
        self.panel.toggle(x, y, self.language)

    def _open_settings(self) -> None:
        x, y = self._panel_position()
        self.panel.open_settings(x, y, self.language)

    def _show_context_menu(self) -> None:
        rect = RECT()
        ctypes.windll.user32.GetWindowRect(self.hwnd, ctypes.byref(rect))
        menu = _u32.CreatePopupMenu()
        if not menu:
            logging.getLogger("codex_usage_monitor").error("Could not create the native context menu")
            return
        try:
            for identifier, label in native_menu_items(self.language)[:3]:
                _u32.AppendMenuW(menu, MF_STRING, identifier, label)
            _u32.AppendMenuW(menu, MF_SEPARATOR, 0, None)
            identifier, label = native_menu_items(self.language)[3]
            _u32.AppendMenuW(menu, MF_STRING, identifier, label)
            _u32.SetForegroundWindow(self.hwnd)
            selected = _u32.TrackPopupMenu(menu, TPM_LEFTALIGN | TPM_BOTTOMALIGN | TPM_RETURNCMD, rect.left, rect.top, 0, self.hwnd, None)
            action = NATIVE_MENU_IDS.get(selected)
            if action is not None:
                _u32.PostMessageW(self.hwnd, WM_MENU_ACTION, MENU_ACTION_CODES[action], 0)
        finally:
            _u32.DestroyMenu(menu)

    # ---- callbacks invoked from the panel's own thread; PostMessageW is documented as
    # safe to call cross-thread, so these just hand off to our own message loop instead
    # of touching any Win32/GDI state directly from a foreign thread. ----

    def _on_panel_language_changed(self, language: str) -> None:
        ctypes.windll.user32.PostMessageW(self.hwnd, WM_LANGUAGE_CHANGED, 0 if language == "zh" else 1, 0)

    def _on_panel_settings_changed(self) -> None:
        ctypes.windll.user32.PostMessageW(self.hwnd, WM_SETTINGS_CHANGED, 0, 0)

    def _on_panel_menu_action(self, action: str) -> None:
        code = MENU_ACTION_CODES[action]
        ctypes.windll.user32.PostMessageW(self.hwnd, WM_MENU_ACTION, code, 0)

    def _on_panel_unread_changed(self, count: int) -> None:
        ctypes.windll.user32.PostMessageW(self.hwnd, WM_UNREAD_CHANGED, count, 0)

    def _render_compact(self) -> None:
        if not self.hwnd:
            return
        width, height = WIDTH, self.height
        bmi = BITMAPINFO()
        bmi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        bmi.bmiHeader.biWidth = width
        bmi.bmiHeader.biHeight = -height  # top-down, so row 0 is the top row
        bmi.bmiHeader.biPlanes = 1
        bmi.bmiHeader.biBitCount = 32
        bmi.bmiHeader.biCompression = BI_RGB

        screen_dc = ctypes.windll.user32.GetDC(None)
        mem_dc = ctypes.windll.gdi32.CreateCompatibleDC(screen_dc)
        bits_ptr = ctypes.c_void_p()
        dib = ctypes.windll.gdi32.CreateDIBSection(screen_dc, ctypes.byref(bmi), 0, ctypes.byref(bits_ptr), None, 0)
        old_bitmap = ctypes.windll.gdi32.SelectObject(mem_dc, dib)
        try:
            token = ctypes.c_size_t()
            startup = GdiplusStartupInput(1, None, False, False)
            ctypes.windll.gdiplus.GdiplusStartup(ctypes.byref(token), ctypes.byref(startup), None)
            gp_bitmap = ctypes.c_void_p()
            graphics = ctypes.c_void_p()
            try:
                ctypes.windll.gdiplus.GdipCreateBitmapFromScan0(width, height, width * 4, PIXEL_FORMAT_32BPP_PARGB, bits_ptr, ctypes.byref(gp_bitmap))
                ctypes.windll.gdiplus.GdipGetImageGraphicsContext(gp_bitmap, ctypes.byref(graphics))
                ctypes.windll.gdiplus.GdipGraphicsClear(graphics, 0)  # fully transparent
                ctypes.windll.gdiplus.GdipSetSmoothingMode(graphics, SMOOTHING_MODE_ANTIALIAS)
                ctypes.windll.gdiplus.GdipSetInterpolationMode(graphics, INTERPOLATION_MODE_HIGH_QUALITY_BICUBIC)
                ctypes.windll.gdiplus.GdipSetTextRenderingHint(graphics, TEXT_RENDERING_HINT_ANTIALIAS_GRID_FIT)

                result = self.service.result_for("codex")
                session, weekly = compact_lines(result, self.language)
                # Use a true 32px icon on the normal 40px taskbar, while keeping a
                # safe fallback for unusually compact taskbar heights.
                logo_size = min(32, max(16, height - 6))
                logo_y = (height - logo_size) // 2
                self._draw_icon_gdiplus(graphics, asset_path(icon_name()), 6, logo_y, logo_size)
                self._draw_badge_gdiplus(graphics, self.panel.unread_count, 6 + logo_size - 10, logo_y - 2)
                text_x = 6 + logo_size + 8
                line_height, line_gap = 16, 3
                block_height = line_height * 2 + line_gap
                block_top = max(0, (height - block_height) // 2)
                text_argb = argb(255, *_rgb_components(PANEL_TEXT))
                font_family = ui_font_family(self.language)
                self._draw_text_gdiplus(graphics, session, text_x, block_top, 14, True, text_argb, font_family)
                self._draw_text_gdiplus(graphics, weekly, text_x, block_top + line_height + line_gap, 14, True, text_argb, font_family)
            finally:
                if graphics:
                    ctypes.windll.gdiplus.GdipDeleteGraphics(graphics)
                if gp_bitmap:
                    ctypes.windll.gdiplus.GdipDisposeImage(gp_bitmap)
                ctypes.windll.gdiplus.GdiplusShutdown(token)

            self._raise_zero_alpha_floor(bits_ptr, width * height)

            rect = RECT()
            ctypes.windll.user32.GetWindowRect(self.hwnd, ctypes.byref(rect))
            dst_pt = POINT(rect.left, rect.top)
            src_pt = POINT(0, 0)
            size = SIZE(width, height)
            blend = BLENDFUNCTION(AC_SRC_OVER, 0, 255, AC_SRC_ALPHA)
            ctypes.windll.user32.UpdateLayeredWindow(
                self.hwnd, screen_dc, ctypes.byref(dst_pt), ctypes.byref(size),
                mem_dc, ctypes.byref(src_pt), 0, ctypes.byref(blend), ULW_ALPHA)
        finally:
            ctypes.windll.gdi32.SelectObject(mem_dc, old_bitmap)
            ctypes.windll.gdi32.DeleteObject(dib)
            ctypes.windll.gdi32.DeleteDC(mem_dc)
            ctypes.windll.user32.ReleaseDC(None, screen_dc)

    @staticmethod
    def _raise_zero_alpha_floor(bits_ptr: ctypes.c_void_p, pixel_count: int) -> None:
        # A layered window (ULW_ALPHA) treats exactly-zero-alpha pixels as click-through, so a
        # fully transparent background makes only the icon/text pixels draggable/clickable.
        # Raising untouched background pixels to alpha=1 keeps the whole strip hit-testable
        # while staying visually indistinguishable from true transparency. Anything GDI+ actually
        # drew (including anti-aliased edges) already has its own correct alpha and is left alone.
        buffer = (ctypes.c_uint8 * (pixel_count * 4)).from_address(bits_ptr.value)
        for i in range(3, pixel_count * 4, 4):
            if buffer[i] == 0:
                buffer[i] = 1

    @staticmethod
    def _draw_icon_gdiplus(graphics: ctypes.c_void_p, path: Path, x: int, y: int, size: int) -> None:
        if not path.exists():
            return
        image = ctypes.c_void_p()
        if ctypes.windll.gdiplus.GdipLoadImageFromFile(str(path), ctypes.byref(image)) != 0:
            return
        try:
            ctypes.windll.gdiplus.GdipDrawImageRectI(graphics, image, x, y, size, size)
        finally:
            ctypes.windll.gdiplus.GdipDisposeImage(image)

    @staticmethod
    def _draw_badge_gdiplus(graphics: ctypes.c_void_p, count: int, x: int, y: int) -> None:
        if count <= 0:
            return
        diameter = 16
        brush = ctypes.c_void_p()
        try:
            ctypes.windll.gdiplus.GdipCreateSolidFill(argb(255, 229, 72, 77), ctypes.byref(brush))
            ctypes.windll.gdiplus.GdipFillEllipseI(graphics, brush, x, y, diameter, diameter)
        finally:
            if brush:
                ctypes.windll.gdiplus.GdipDeleteBrush(brush)
        label = badge_text(count)
        offset = 1 if len(label) >= 3 else 3 if len(label) == 2 else 5
        NativeOverlay._draw_text_gdiplus(graphics, label, x + offset, y + 3, 7 if len(label) >= 3 else 8, True, argb(255, 255, 255, 255), "Segoe UI")

    @staticmethod
    def _draw_text_gdiplus(graphics: ctypes.c_void_p, value: str, x: int, y: int, size: int, bold: bool, argb_color: int, font_family: str) -> None:
        family = ctypes.c_void_p()
        font = ctypes.c_void_p()
        brush = ctypes.c_void_p()
        try:
            ctypes.windll.gdiplus.GdipCreateFontFamilyFromName(font_family, None, ctypes.byref(family))
            ctypes.windll.gdiplus.GdipCreateFont(family, ctypes.c_float(size), FONT_STYLE_BOLD if bold else FONT_STYLE_REGULAR, UNIT_PIXEL, ctypes.byref(font))
            ctypes.windll.gdiplus.GdipCreateSolidFill(argb_color, ctypes.byref(brush))
            layout = RectF(float(x), float(y), 500.0, float(size * 2))
            ctypes.windll.gdiplus.GdipDrawString(graphics, value, -1, font, ctypes.byref(layout), None, brush)
        finally:
            if brush:
                ctypes.windll.gdiplus.GdipDeleteBrush(brush)
            if font:
                ctypes.windll.gdiplus.GdipDeleteFont(font)
            if family:
                ctypes.windll.gdiplus.GdipDeleteFontFamily(family)

    def _logo_zone_width(self) -> int:
        return 6 + int(self.height * 0.72) + 6

    def _wndproc(self, hwnd, message, wparam, lparam):
        if message == WM_PAINT:
            # Content is pushed via UpdateLayeredWindow, not the normal paint cycle.
            ctypes.windll.user32.ValidateRect(hwnd, None)
            return 0
        if message == WM_APP + 1:
            self._refreshing = False
            self._render_compact()
            if self.panel.is_open:
                self.panel.notify_refreshed()
            ctypes.windll.user32.SetTimer(hwnd, 1, self.config.refresh_seconds * 1000, None)
            return 0
        if message == WM_PROVIDER_DATA_READY:
            self._render_compact()
            if self.panel.is_open:
                self.panel.notify_refreshed()
            return 0
        if message == WM_UNREAD_CHANGED:
            self._render_compact()
            return 0
        if message == WM_LANGUAGE_CHANGED:
            self.language = "zh" if wparam == 0 else "en"
            self._render_compact()
            return 0
        if message == WM_SETTINGS_CHANGED:
            self.config = self.store.load()
            return 0
        if message == WM_MENU_ACTION:
            if wparam == 0:
                self._toggle_panel()
            elif wparam == 1:
                self._open_settings()
            elif wparam == 2:
                x, y = self._panel_position()
                self.panel.open_notices_at(x, y, self.language)
            elif wparam == 3:
                ctypes.windll.user32.DestroyWindow(hwnd)
            return 0
        if message == WM_CLOSE:
            ctypes.windll.user32.DestroyWindow(hwnd)
            return 0
        if message == WM_RBUTTONUP:
            self._show_context_menu()
            return 0
        if message == WM_TIMER:
            ctypes.windll.user32.KillTimer(hwnd, wparam)
            self.refresh()
            return 0
        if message == WM_LBUTTONDOWN:
            self._drag_client_x = ctypes.c_short(lparam & 0xffff).value
            self._drag_start_taskbar_x = self.config.taskbar_x
            self._dragging = False
            rect = RECT()
            taskbar = ctypes.windll.user32.FindWindowW("Shell_TrayWnd", None)
            ctypes.windll.user32.GetWindowRect(taskbar, ctypes.byref(rect))
            self._drag_taskbar_width = rect.right - rect.left
            ctypes.windll.user32.SetCapture(hwnd)
            return 0
        if message == WM_MOUSEMOVE and self._drag_client_x is not None:
            cursor = POINT()
            ctypes.windll.user32.GetCursorPos(ctypes.byref(cursor))
            candidate_x = cursor.x - self._drag_client_x
            if not self._dragging and abs(candidate_x - self._drag_start_taskbar_x) < DRAG_THRESHOLD:
                return 0
            self._dragging = True
            new_x = clamp_taskbar_x(candidate_x, self._drag_taskbar_width, WIDTH)
            self.config = replace(self.config, language=self.language, taskbar_x=new_x)
            self.store.save(self.config)
            taskbar = ctypes.windll.user32.FindWindowW("Shell_TrayWnd", None)
            rect = RECT()
            ctypes.windll.user32.GetWindowRect(taskbar, ctypes.byref(rect))
            y = rect.top + max(0, (rect.bottom - rect.top - self.height) // 2)
            ctypes.windll.user32.SetWindowPos(hwnd, HWND_TOPMOST, new_x, y, 0, 0, SWP_NOSIZE | SWP_NOZORDER)
            return 0
        if message == WM_LBUTTONUP:
            was_dragging = self._dragging
            click_x = self._drag_client_x
            self._drag_client_x = None
            self._dragging = False
            ctypes.windll.user32.ReleaseCapture()
            if not was_dragging and click_x is not None:
                self._toggle_panel()
            return 0
        if message == WM_DESTROY:
            self.panel.stop()
            self.hwnd = 0
            ctypes.windll.user32.PostQuitMessage(0)
            return 0
        return ctypes.windll.user32.DefWindowProcW(hwnd, message, wparam, lparam)


WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)

class WNDCLASSW(ctypes.Structure):
    _fields_ = [("style", wintypes.UINT), ("lpfnWndProc", WNDPROC), ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int), ("hInstance", wintypes.HINSTANCE), ("hIcon", wintypes.HICON), ("hCursor", wintypes.HCURSOR), ("hbrBackground", wintypes.HBRUSH), ("lpszMenuName", wintypes.LPCWSTR), ("lpszClassName", wintypes.LPCWSTR)]

class POINT(ctypes.Structure): _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]
class RECT(ctypes.Structure): _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long), ("right", ctypes.c_long), ("bottom", ctypes.c_long)]
class MSG(ctypes.Structure): _fields_ = [("hwnd", wintypes.HWND), ("message", wintypes.UINT), ("wParam", wintypes.WPARAM), ("lParam", wintypes.LPARAM), ("time", wintypes.DWORD), ("pt", POINT), ("lPrivate", wintypes.DWORD)]
class GdiplusStartupInput(ctypes.Structure): _fields_ = [("GdiplusVersion", ctypes.c_uint32), ("DebugEventCallback", ctypes.c_void_p), ("SuppressBackgroundThread", wintypes.BOOL), ("SuppressExternalCodecs", wintypes.BOOL)]
class SIZE(ctypes.Structure): _fields_ = [("cx", ctypes.c_long), ("cy", ctypes.c_long)]
class RectF(ctypes.Structure): _fields_ = [("X", ctypes.c_float), ("Y", ctypes.c_float), ("Width", ctypes.c_float), ("Height", ctypes.c_float)]
class BLENDFUNCTION(ctypes.Structure): _fields_ = [("BlendOp", ctypes.c_byte), ("BlendFlags", ctypes.c_byte), ("SourceConstantAlpha", ctypes.c_byte), ("AlphaFormat", ctypes.c_byte)]
class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wintypes.DWORD), ("biWidth", ctypes.c_long), ("biHeight", ctypes.c_long),
                ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", ctypes.c_long), ("biYPelsPerMeter", ctypes.c_long),
                ("biClrUsed", wintypes.DWORD), ("biClrImportant", wintypes.DWORD)]
class BITMAPINFO(ctypes.Structure): _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wintypes.DWORD * 3)]


# Real window/DC handles (from GetDC, CreateCompatibleDC, ...) are not guaranteed to fit in the
# 32-bit int ctypes assumes when a function has no declared argtypes; on 64-bit Windows that
# silently corrupts the call or raises OverflowError. Declaring real argtypes for every
# handle-accepting function used in this module avoids the whole bug class instead of patching
# individual crash reports.
_u32, _g32 = ctypes.windll.user32, ctypes.windll.gdi32
_HDC, _HWND, _HBRUSH, _HGDIOBJ, _HBITMAP, _HCURSOR, _HINSTANCE = (
    wintypes.HDC, wintypes.HWND, wintypes.HBRUSH, wintypes.HGDIOBJ, wintypes.HBITMAP, wintypes.HCURSOR, wintypes.HINSTANCE,
)
_PRECT, _PPOINT, _PSIZE = ctypes.POINTER(RECT), ctypes.POINTER(POINT), ctypes.POINTER(SIZE)

_u32.GetWindowRect.argtypes = [_HWND, _PRECT]
_u32.CreatePopupMenu.restype = wintypes.HANDLE
_u32.AppendMenuW.argtypes = [wintypes.HANDLE, wintypes.UINT, ctypes.c_size_t, wintypes.LPCWSTR]
_u32.AppendMenuW.restype = wintypes.BOOL
_u32.TrackPopupMenu.argtypes = [wintypes.HANDLE, wintypes.UINT, ctypes.c_int, ctypes.c_int, ctypes.c_int, _HWND, ctypes.c_void_p]
_u32.TrackPopupMenu.restype = wintypes.UINT
_u32.DestroyMenu.argtypes = [wintypes.HANDLE]
_u32.DestroyMenu.restype = wintypes.BOOL
_u32.SetForegroundWindow.argtypes = [_HWND]
_u32.SetForegroundWindow.restype = wintypes.BOOL
_u32.DestroyWindow.argtypes = [_HWND]
_u32.DestroyWindow.restype = wintypes.BOOL
_u32.LoadCursorW.argtypes = [_HINSTANCE, ctypes.c_void_p]  # 2nd arg is often a small MAKEINTRESOURCE id, not a real string pointer
_u32.LoadCursorW.restype = _HCURSOR
_u32.ShowWindow.argtypes = [_HWND, ctypes.c_int]
_u32.PostMessageW.argtypes = [_HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
_u32.SetTimer.argtypes = [_HWND, ctypes.c_size_t, wintypes.UINT, ctypes.c_void_p]
_u32.SetTimer.restype = ctypes.c_size_t
_u32.KillTimer.argtypes = [_HWND, ctypes.c_size_t]
_u32.ValidateRect.argtypes = [_HWND, _PRECT]
_u32.GetDC.argtypes = [_HWND]
_u32.GetDC.restype = _HDC
_u32.ReleaseDC.argtypes = [_HWND, _HDC]
_u32.SetCapture.argtypes = [_HWND]
_u32.SetCapture.restype = _HWND
_u32.GetCursorPos.argtypes = [_PPOINT]
_u32.UpdateLayeredWindow.argtypes = [_HWND, _HDC, _PPOINT, _PSIZE, _HDC, _PPOINT, wintypes.COLORREF, ctypes.POINTER(BLENDFUNCTION), wintypes.DWORD]

_g32.SelectObject.argtypes = [_HDC, _HGDIOBJ]
_g32.SelectObject.restype = _HGDIOBJ
_g32.DeleteObject.argtypes = [_HGDIOBJ]
_g32.CreateCompatibleDC.argtypes = [_HDC]
_g32.CreateCompatibleDC.restype = _HDC
_g32.DeleteDC.argtypes = [_HDC]
_g32.CreateDIBSection.argtypes = [_HDC, ctypes.POINTER(BITMAPINFO), wintypes.UINT, ctypes.POINTER(ctypes.c_void_p), wintypes.HANDLE, wintypes.DWORD]
_g32.CreateDIBSection.restype = _HBITMAP

# GDI+ handles (GpGraphics*, GpImage*) are plain opaque pointers, not Windows HANDLEs, but the
# same 32-bit-default-argtype problem applies whenever one is passed as a call argument.
_gp = ctypes.windll.gdiplus
_gp.GdipSetSmoothingMode.argtypes = [ctypes.c_void_p, ctypes.c_int]
_gp.GdipSetInterpolationMode.argtypes = [ctypes.c_void_p, ctypes.c_int]
_gp.GdipDrawImageRectI.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int]
_gp.GdipFillEllipseI.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int]
_gp.GdipDeleteGraphics.argtypes = [ctypes.c_void_p]
_gp.GdipDisposeImage.argtypes = [ctypes.c_void_p]
_gp.GdiplusShutdown.argtypes = [ctypes.c_size_t]
_gp.GdipLoadImageFromFile.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_void_p)]
_gp.GdipCreateBitmapFromScan0.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
_gp.GdipGetImageGraphicsContext.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
_gp.GdipGraphicsClear.argtypes = [ctypes.c_void_p, wintypes.DWORD]
_gp.GdipSetTextRenderingHint.argtypes = [ctypes.c_void_p, ctypes.c_int]
_gp.GdipCreateFontFamilyFromName.argtypes = [wintypes.LPCWSTR, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
_gp.GdipCreateFont.argtypes = [ctypes.c_void_p, ctypes.c_float, ctypes.c_int, ctypes.c_int, ctypes.POINTER(ctypes.c_void_p)]
_gp.GdipCreateSolidFill.argtypes = [wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p)]
_gp.GdipDrawString.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR, ctypes.c_int, ctypes.c_void_p, ctypes.POINTER(RectF), ctypes.c_void_p, ctypes.c_void_p]
_gp.GdipDeleteFont.argtypes = [ctypes.c_void_p]
_gp.GdipDeleteFontFamily.argtypes = [ctypes.c_void_p]
_gp.GdipDeleteBrush.argtypes = [ctypes.c_void_p]
