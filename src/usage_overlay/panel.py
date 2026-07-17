from __future__ import annotations

import ctypes
import logging
import queue
import re
import threading
import time
import tkinter as tk
import tkinter.font as tkfont
import webbrowser
from dataclasses import replace
from typing import Callable

import customtkinter as ctk
from PIL import Image

from usage_overlay.config import AppConfig, ConfigStore, resolve_language
from usage_overlay.formatting import asset_path, format_reset, format_updated, icon_name, remaining, ui_font_family
from usage_overlay.i18n import text
from usage_overlay.refresh import RefreshService
from usage_overlay.reset_feed import NitterResetFeed, ResetPost, unread_posts
from usage_overlay.windows import set_launch_at_login

PANEL_WIDTH, PANEL_HEIGHT = 300, 280
PANEL_TOPMOST = False
PANEL_CLOSE_ON_FOCUS_LOSS = False
# The 18px Segoe UI glyph box needs 26px for descenders (e.g. "g").  The
# slight widget overlap is transparent; the visible text baselines stay tight.
USAGE_HEADER_LINE_HEIGHT, USAGE_HEADER_LINE_GAP = 26, -4
VERIFIED_LABEL_Y = 68
SESSION_ROW_Y, WEEKLY_ROW_Y = 120, 194

CODEX_ACCENT, CODEX_ACCENT_ALT = "#0F9E74", "#16B98A"
CREAM_BG, CREAM_BG_ALT = "#F4FBF7", "#E7F5ED"
PANEL_TEXT, MUTED_TEXT, TRACK_COLOR, BAR_TRACK = "#12382D", "#628075", "#DCEFE5", "#D0E7DC"
TOOLTIP_BG, TOOLTIP_TEXT, TOOLTIP_RESET_HIGHLIGHT = "#D9EEE3", "#12382D", "#B8DDC9"
KNOB_COLOR = "#FFFFFF"
BORDER_COLOR = "#D0D0D0"
TRANSPARENT_KEY = "#FFFFFE"

ANIM_STEP_MS = 15
RIGHT_MARGIN = 24
CORNER_RADIUS = 14
FRAME_CORNER_INSET = 2


def notice_empty_state_bounds() -> tuple[int, int, int, int]:
    """Bounds for the loading/error layer that sits above the message list."""
    return 20, 70, PANEL_WIDTH - 40, PANEL_HEIGHT - 90


def notice_empty_state_placement() -> dict[str, int]:
    """Keyword arguments for Tk's ``place`` geometry manager."""
    x, y, _, _ = notice_empty_state_bounds()
    return {"x": x, "y": y}


def full_post_text(post: ResetPost) -> str:
    """Prefer the feed description, without repeating an identical title."""
    title = post.title.strip()
    content = post.content.strip()
    if not content or title.casefold() == content.casefold():
        return title or content
    return "\n\n".join(value for value in (title, content) if value)


def format_post_time(value, language: str) -> str:
    local = value.astimezone()
    return f"{local.month}月{local.day}日 {local:%H:%M}" if language == "zh" else f"{local:%b %d, %H:%M}"


def badge_text(count: int) -> str:
    return "" if count <= 0 else "99+" if count > 99 else str(count)


def reset_spans(value: str) -> list[tuple[int, int]]:
    return [(match.start(), match.end()) for match in re.finditer(r"\breset\w*\b", value, re.IGNORECASE)]


def tooltip_content_size(content_width: int, content_height: int) -> tuple[int, int]:
    """Return the rounded bubble size, including its fixed inner padding."""
    return content_width + 20, content_height + 16


def _wrap_tooltip_characters(value: str, font: tkfont.Font, max_width: int) -> list[list[tuple[int, str]]]:
    """Wrap text at character boundaries so CJK and highlighted spans stay aligned."""
    lines: list[list[tuple[int, str]]] = [[]]
    for index, char in enumerate(value):
        if char == "\n":
            lines.append([])
            continue
        proposed = "".join(part for _, part in lines[-1]) + char
        if lines[-1] and font.measure(proposed) > max_width:
            lines.append([])
        lines[-1].append((index, char))
    return lines


def _draw_rounded_rectangle(canvas: tk.Canvas, x1: int, y1: int, x2: int, y2: int, radius: int, fill: str) -> None:
    canvas.create_rectangle(x1 + radius, y1, x2 - radius, y2, fill=fill, outline="")
    canvas.create_rectangle(x1, y1 + radius, x2, y2 - radius, fill=fill, outline="")
    for left, top in ((x1, y1), (x2 - radius * 2, y1), (x1, y2 - radius * 2), (x2 - radius * 2, y2 - radius * 2)):
        canvas.create_oval(left, top, left + radius * 2, top + radius * 2, fill=fill, outline="")


class PanelController:
    """Owns panel state and marshals native-overlay calls into Tk's event loop."""

    def __init__(
        self,
        service: RefreshService,
        store: ConfigStore,
        on_language_changed: Callable[[str], None],
        on_settings_changed: Callable[[], None],
        on_menu_action: Callable[[str], None],
        on_unread_changed: Callable[[int], None],
    ) -> None:
        self.service = service
        self.store = store
        self.on_language_changed = on_language_changed
        self.on_settings_changed = on_settings_changed
        self.on_menu_action = on_menu_action
        self.on_unread_changed = on_unread_changed
        self.reset_feed = NitterResetFeed()
        self._queue: "queue.Queue[tuple]" = queue.Queue()
        self._app: PanelApp | None = None
        self._open = False
        self._state_lock = threading.RLock()
        self._notice_lock = threading.RLock()
        self._startup_posts: list[ResetPost] = []
        self._startup_errors: list[str] = []
        self._notice_loading = False
        self._notice_loaded = False
        self._mark_notices_read_on_load = False
        self._unread_count = 0

    def start(self) -> None:
        """Run the Tk event loop on Python's main thread.

        Tk's Tcl interpreter is thread-affine and can crash during interpreter
        shutdown when created from a worker thread.  The native Win32 overlay
        runs separately; it communicates with this loop through ``_queue``.
        """
        self._run()

    @property
    def is_open(self) -> bool:
        with self._state_lock:
            return self._open

    @property
    def unread_count(self) -> int:
        with self._notice_lock:
            return self._unread_count

    def load_startup_notices(self) -> None:
        """Fetch the Codex public feed exactly once for this process."""
        self._begin_notice_fetch(force=False)

    def retry_notices(self) -> None:
        """Retry the public feed only when the user explicitly asks."""
        self._begin_notice_fetch(force=True)

    def _begin_notice_fetch(self, force: bool) -> None:
        with self._notice_lock:
            if self._notice_loading or (self._notice_loaded and not force):
                return
            self._notice_loading = True
            if force:
                self._startup_errors = []
        threading.Thread(target=self._fetch_startup_notices, name="ResetFeedStartup", daemon=True).start()

    def _fetch_startup_notices(self) -> None:
        posts: list[ResetPost] = []
        errors: list[str] = []
        try:
            posts.extend(self.reset_feed.fetch())
        except Exception as exc:
            logging.getLogger("codex_usage_monitor").exception("Message feed fetch failed")
            errors.append(str(exc))
        posts.sort(key=lambda post: post.published_at, reverse=True)
        config = self.store.load()
        with self._notice_lock:
            self._startup_posts = posts
            self._startup_errors = errors
            self._notice_loading = False
            self._notice_loaded = True
            self._unread_count = 0 if self._mark_notices_read_on_load else len(unread_posts(posts, config.read_post_urls))
        if self._mark_notices_read_on_load:
            self._persist_posts_as_read(posts)
        self._queue.put(("notices_ready", posts, errors))
        self._publish_unread_count()

    def mark_notices_read(self) -> None:
        with self._notice_lock:
            self._mark_notices_read_on_load = True
            loaded, posts = self._notice_loaded, list(self._startup_posts)
            self._unread_count = 0
        if loaded:
            self._persist_posts_as_read(posts)
        self._publish_unread_count()

    def startup_notice_snapshot(self) -> tuple[bool, list[ResetPost], list[str]]:
        with self._notice_lock:
            return self._notice_loaded, list(self._startup_posts), list(self._startup_errors)

    def _persist_posts_as_read(self, posts: list[ResetPost]) -> None:
        config = self.store.load()
        urls = tuple(dict.fromkeys((*config.read_post_urls, *(post.url for post in posts))))
        self.store.save(replace(config, read_post_urls=urls))

    def _publish_unread_count(self) -> None:
        self._queue.put(("unread", self.unread_count))
        self.on_unread_changed(self.unread_count)

    def toggle(self, x: int, y: int, language: str) -> None:
        with self._state_lock:
            self._open = not self._open
            open_now = self._open
        self._queue.put(("show", x, y, language) if open_now else ("hide",))

    def hide(self) -> None:
        with self._state_lock:
            was_open = self._open
            self._open = False
        if was_open:
            self._queue.put(("hide",))

    def open_settings(self, x: int, y: int, language: str) -> None:
        with self._state_lock:
            self._open = True
        self._queue.put(("show_settings", x, y, language))

    def notify_closed(self) -> None:
        with self._state_lock:
            self._open = False

    def stop(self) -> None:
        self._queue.put(("shutdown",))

    def notify_refreshed(self) -> None:
        self._queue.put(("refresh",))

    def open_notices(self) -> None:
        self.mark_notices_read()
        self._queue.put(("notices",))

    def open_notices_at(self, x: int, y: int, language: str) -> None:
        self.mark_notices_read()
        with self._state_lock:
            self._open = True
        self._queue.put(("show_notices", x, y, language))

    def notify_notices(self, posts: list[ResetPost], errors: list[str]) -> None:
        self._queue.put(("notices_ready", posts, errors))

    def _run(self) -> None:
        app = PanelApp(self)
        self._app = app
        app.after(30, self._drain)
        try:
            app.mainloop()
        finally:
            # The main thread owns this Tcl interpreter and is therefore also the
            # only thread allowed to release its widgets during shutdown.
            self._app = None

    def _drain(self) -> None:
        app = self._app
        try:
            while True:
                item = self._queue.get_nowait()
                if item[0] == "show":
                    _, x, y, language = item
                    app.show_at(x, y, language)
                elif item[0] == "show_settings":
                    _, x, y, language = item
                    app.show_at(x, y, language, view="settings")
                elif item[0] == "hide":
                    app.hide_panel()
                elif item[0] == "refresh":
                    app.refresh_data()
                elif item[0] == "notices":
                    app.show_notices()
                elif item[0] == "show_notices":
                    _, x, y, language = item
                    app.show_at(x, y, language)
                    app.show_notices()
                elif item[0] == "notices_ready":
                    if app.view == "notices":
                        app.set_notices(item[1], item[2])
                elif item[0] == "unread":
                    app.set_unread_count(item[1])
                elif item[0] == "shutdown":
                    app.destroy()
                    return
        except queue.Empty:
            pass
        app.after(30, self._drain)


class PanelApp(ctk.CTk):
    def __init__(self, controller: PanelController) -> None:
        super().__init__()
        self.controller = controller
        self.language = resolve_language(controller.store.load().language)
        self.view = "usage"
        # Last seen reset times, cached so _retext() can re-render the "Resets …"
        # line in the new language without waiting for the next refresh tick.
        self._last_session_reset = None
        self._last_weekly_reset = None
        self._gear_image = ctk.CTkImage(Image.open(asset_path("gear-icon.png")), size=(18, 18))
        self._close_image = ctk.CTkImage(Image.open(asset_path("close-icon.png")), size=(15, 15))
        self._codex_image = ctk.CTkImage(Image.open(asset_path(icon_name())), size=(32, 32))

        self.overrideredirect(True)
        self.attributes("-topmost", PANEL_TOPMOST)
        self.attributes("-alpha", 0.0)
        self.attributes("-transparentcolor", TRANSPARENT_KEY)
        self.configure(fg_color=CREAM_BG)
        self.geometry(f"{PANEL_WIDTH}x{PANEL_HEIGHT}+0+0")
        self.withdraw()
        # iconphoto() does not reliably reach the Windows taskbar-button icon (it only
        # sets the Tk-level photo, which Tk's HICON conversion frequently drops on this
        # platform); iconbitmap() with a real multi-resolution .ico goes through Tk's
        # native Windows icon path instead and actually shows up on the taskbar.
        try:
            self.iconbitmap(default=str(asset_path("openai-icon.ico")))
        except Exception:
            pass
        self.bind("<FocusOut>", self._on_focus_out)

        self.usage_frame = ctk.CTkFrame(self, fg_color=CREAM_BG, bg_color=TRANSPARENT_KEY, corner_radius=max(0, CORNER_RADIUS - FRAME_CORNER_INSET), border_width=1, border_color=BORDER_COLOR)
        self.settings_frame = ctk.CTkFrame(self, fg_color=CREAM_BG, bg_color=TRANSPARENT_KEY, corner_radius=max(0, CORNER_RADIUS - FRAME_CORNER_INSET), border_width=1, border_color=BORDER_COLOR)
        self.notices_frame = ctk.CTkFrame(self, fg_color=CREAM_BG, bg_color=TRANSPARENT_KEY, corner_radius=max(0, CORNER_RADIUS - FRAME_CORNER_INSET), border_width=1, border_color=BORDER_COLOR)
        for frame in (self.usage_frame, self.settings_frame, self.notices_frame):
            frame.place(x=0, y=0, relwidth=1, relheight=1)

        self._nav_button = ctk.CTkButton(
            self, text="", image=self._gear_image, width=32, height=32, corner_radius=8, fg_color="transparent",
            hover_color=CREAM_BG_ALT, command=self._toggle_view,
        )
        self._nav_button.place(x=PANEL_WIDTH - 44, y=14)
        self._message_button = ctk.CTkButton(
            self, text="✉", width=32, height=32, corner_radius=8, fg_color="transparent",
            hover_color=CREAM_BG_ALT, text_color=PANEL_TEXT, font=("Segoe UI Symbol", 16, "bold"),
            command=self.controller.open_notices,
        )
        self._message_button.place(x=PANEL_WIDTH - 82, y=14)
        self._message_badge = ctk.CTkLabel(
            self, text="", width=18, height=18, corner_radius=9, fg_color="#E5484D",
            text_color="#FFFFFF", font=("Segoe UI", 9, "bold"),
        )

        self._build_usage_frame()
        self._build_settings_frame()
        self._build_notices_frame()
        self.settings_frame.lower()
        self.notices_frame.lower()
        self._nav_button.lift()
        self._message_button.lift()
        self.set_unread_count(controller.unread_count)

    def _on_focus_out(self, _event) -> None:
        if PANEL_CLOSE_ON_FOCUS_LOSS:
            self.after(60, self._maybe_close_on_focus_loss)

    def _maybe_close_on_focus_loss(self) -> None:
        if self.focus_get() is None and self.winfo_viewable():
            self.hide_panel()
            self.controller.notify_closed()

    def _build_usage_frame(self) -> None:
        frame = self.usage_frame
        self._codex_icon = ctk.CTkLabel(frame, text="", image=self._codex_image, width=32, height=32)
        self._codex_icon.place(x=24, y=28)
        self._usage_header = ctk.CTkFrame(
            frame, width=120, height=USAGE_HEADER_LINE_HEIGHT * 2 + USAGE_HEADER_LINE_GAP,
            fg_color="transparent",
        )
        self._usage_header.place(x=64, y=18)
        self._usage_header.pack_propagate(False)
        self.title_label = ctk.CTkLabel(
            self._usage_header, text="Codex", font=self._font(18, "bold"), text_color=PANEL_TEXT,
            anchor="w", fg_color="transparent", padx=0, pady=0, height=USAGE_HEADER_LINE_HEIGHT,
        )
        self.title_label.place(x=0, y=0)
        self.subtitle_label = ctk.CTkLabel(
            self._usage_header, text="Usage", font=self._font(18, "bold"), text_color=PANEL_TEXT,
            anchor="w", fg_color="transparent", padx=0, pady=0, height=USAGE_HEADER_LINE_HEIGHT,
        )
        self.subtitle_label.place(x=0, y=USAGE_HEADER_LINE_HEIGHT + USAGE_HEADER_LINE_GAP)
        self.session_row = self._build_usage_row(frame, SESSION_ROW_Y, "session_title")
        self.weekly_row = self._build_usage_row(frame, WEEKLY_ROW_Y, "weekly_title")
        self._updated_label = ctk.CTkLabel(frame, text="", font=self._font(10), text_color=MUTED_TEXT, anchor="w")
        self._updated_label.place(x=24, y=VERIFIED_LABEL_Y)

    def _font(self, size: int, weight: str = "normal") -> tuple[str, int, str]:
        return (ui_font_family(self.language), size, weight)

    def _build_usage_row(self, parent: ctk.CTkFrame, y: int, title_key: str) -> dict:
        name_label = ctk.CTkLabel(parent, text=text(self.language, title_key), font=self._font(13, "bold"), text_color=PANEL_TEXT, anchor="w")
        name_label.place(x=24, y=y)
        value_label = ctk.CTkLabel(parent, text="—", font=self._font(13, "bold"), anchor="e", width=80)
        value_label.place(relx=1.0, x=-RIGHT_MARGIN, y=y, anchor="ne")
        bar = ctk.CTkProgressBar(parent, width=PANEL_WIDTH - 48, height=8, corner_radius=4, progress_color=CODEX_ACCENT, fg_color=BAR_TRACK)
        bar.set(0)
        bar.place(x=24, y=y + 26)
        reset_label = ctk.CTkLabel(parent, text="", font=self._font(11), text_color=MUTED_TEXT, anchor="w")
        reset_label.place(x=24, y=y + 38)
        return {"title_key": title_key, "name": name_label, "value": value_label, "bar": bar, "reset": reset_label}

    def _build_settings_frame(self) -> None:
        frame = self.settings_frame
        frame.grid_columnconfigure(0, weight=1)
        frame.grid_columnconfigure(1, weight=0)

        self._settings_title = ctk.CTkLabel(frame, text=text(self.language, "settings"), font=self._font(20, "bold"), text_color=PANEL_TEXT, anchor="w")
        self._settings_title.grid(row=0, column=0, columnspan=2, sticky="w", padx=24, pady=(30, 20))

        self._login_label = ctk.CTkLabel(frame, text=text(self.language, "launch_at_login"), font=self._font(13), text_color=PANEL_TEXT, anchor="w")
        self._login_label.grid(row=1, column=0, sticky="nsw", padx=24, pady=10)
        self._login_switch = ctk.CTkSwitch(frame, text="", width=40, height=20, progress_color=CODEX_ACCENT, command=self._on_login_toggle)
        self._login_switch.grid(row=1, column=1, sticky="nse", padx=24, pady=10)

        self._interval_label = ctk.CTkLabel(frame, text=text(self.language, "refresh_interval"), font=self._font(13), text_color=PANEL_TEXT, anchor="w")
        self._interval_label.grid(row=2, column=0, sticky="nsw", padx=24, pady=10)
        interval_group = ctk.CTkFrame(frame, fg_color="transparent")
        interval_group.grid(row=2, column=1, sticky="nse", padx=24, pady=10)
        self._interval_entry = ctk.CTkEntry(interval_group, width=56, height=28, corner_radius=8)
        self._interval_entry.pack(side="left")
        self._seconds_label = ctk.CTkLabel(interval_group, text=text(self.language, "seconds"), font=self._font(13), text_color=PANEL_TEXT)
        self._seconds_label.pack(side="left", padx=(6, 0))
        self._interval_entry.bind("<FocusOut>", lambda _: self._commit_interval())
        self._interval_entry.bind("<Return>", lambda _: self._commit_interval())

        self._language_label = ctk.CTkLabel(frame, text=text(self.language, "language"), font=self._font(13), text_color=PANEL_TEXT, anchor="w")
        self._language_label.grid(row=3, column=0, sticky="nsw", padx=24, pady=10)
        self._language_button = ctk.CTkButton(
            frame, text="EN" if self.language == "en" else "中", width=32, height=28, corner_radius=8,
            fg_color=CODEX_ACCENT, hover_color=CODEX_ACCENT_ALT, text_color="#FFFFFF",
            font=self._font(12, "bold"), command=self._on_language_toggle,
        )
        self._language_button.grid(row=3, column=1, sticky="nse", padx=24, pady=10)

    def _build_notices_frame(self) -> None:
        frame = self.notices_frame
        self._notices_title = ctk.CTkLabel(frame, text=text(self.language, "reset_alerts"), font=self._font(20, "bold"), text_color=PANEL_TEXT, anchor="w")
        self._notices_title.place(x=24, y=22)
        self._notices_list = ctk.CTkScrollableFrame(
            frame, width=250, height=274, corner_radius=0, fg_color="transparent",
            scrollbar_button_color=CREAM_BG_ALT, scrollbar_button_hover_color="#DDD3C6",
        )
        self._notices_list.place(x=20, y=70)
        x, y, width, height = notice_empty_state_bounds()
        self._notices_empty_state = ctk.CTkFrame(frame, width=width, height=height, corner_radius=10, fg_color=CREAM_BG)
        self._notices_empty_state.place(x=x, y=y)
        self._notices_empty_state.pack_propagate(False)
        self._notices_status = ctk.CTkLabel(
            self._notices_empty_state, text="", font=self._font(12), text_color=MUTED_TEXT,
            anchor="center", justify="center", wraplength=220,
        )
        self._notices_status.place(relx=0.5, rely=0.44, anchor="center")
        self._notices_retry = ctk.CTkButton(
            self._notices_empty_state, text=text(self.language, "retry"), width=88, height=28, corner_radius=8,
            fg_color=CODEX_ACCENT, hover_color=CODEX_ACCENT_ALT, text_color="#FFFFFF",
            font=self._font(12, "bold"), command=self._retry_notices,
        )
        self._notice_tooltip: tk.Toplevel | None = None
        self._notice_tooltip_source: tk.Widget | None = None
        self._notice_tooltip_hide_job: str | None = None

    def _show_notice_empty_state(self, message: str, retry: bool) -> None:
        self._notices_status.configure(text=message)
        if retry:
            self._notices_retry.place(relx=0.5, rely=0.64, anchor="center")
        else:
            self._notices_retry.place_forget()
        self._notices_empty_state.place(**notice_empty_state_placement())
        self._notices_empty_state.lift()

    def _hide_notice_empty_state(self) -> None:
        self._notices_retry.place_forget()
        self._notices_empty_state.place_forget()

    def show_notices(self) -> None:
        self.view = "notices"
        self._nav_button.configure(image=self._close_image)
        self._show_notice_empty_state(text(self.language, "loading"), retry=False)
        self._clear_notices()
        self.notices_frame.lift()
        self._nav_button.lift()
        self._message_button.lift()
        self._message_badge.lift()
        loaded, posts, errors = self.controller.startup_notice_snapshot()
        if loaded:
            self.set_notices(posts, errors)

    def set_notices(self, posts: list[ResetPost], errors: list[str]) -> None:
        self._clear_notices()
        if not posts:
            self._show_notice_empty_state(
                text(self.language, "feed_unavailable") if errors else text(self.language, "no_reset_alerts"),
                retry=bool(errors),
            )
            return
        self._hide_notice_empty_state()
        self._notices_list.lift()
        for post in posts:
            self._add_notice(post)

    def _clear_notices(self) -> None:
        self._hide_notice_tooltip()
        for child in self._notices_list.winfo_children():
            child.destroy()

    def _retry_notices(self) -> None:
        self._show_notice_empty_state(text(self.language, "loading"), retry=False)
        self._clear_notices()
        self.controller.retry_notices()

    def _add_notice(self, post: ResetPost) -> None:
        preview = (post.title or post.content).replace("\n", " ")
        if len(preview) > 62:
            preview = f"{preview[:59].rstrip()}…"
        card = ctk.CTkFrame(self._notices_list, width=236, height=58, corner_radius=9, fg_color=CREAM_BG_ALT)
        card.pack(fill="x", padx=(0, 5), pady=(0, 8))
        card.pack_propagate(False)
        preview_label = ctk.CTkLabel(card, text=preview, text_color=PANEL_TEXT, font=self._font(12), anchor="w", justify="left", wraplength=210, width=212, height=32)
        preview_label.place(x=12, y=4)
        timestamp = ctk.CTkLabel(card, text=format_post_time(post.published_at, self.language), text_color=MUTED_TEXT, font=self._font(10), anchor="w", width=212, height=12)
        timestamp.place(x=12, y=35)
        full_text = full_post_text(post)
        for widget in (card, preview_label, timestamp):
            widget.bind("<Button-1>", lambda _event, url=post.url: self._open_notice(url))
            widget.bind("<Enter>", lambda _event, source=card, value=full_text: self._enter_notice_card(source, value))
            widget.bind("<Leave>", lambda _event, source=card: self._schedule_notice_tooltip_hide(source))

    def _enter_notice_card(self, source: tk.Widget, value: str) -> None:
        self.configure(cursor="hand2")
        self._show_notice_tooltip(source, value)

    def _open_notice(self, url: str) -> None:
        self._hide_notice_tooltip()
        self.configure(cursor="")
        webbrowser.open(url)

    def set_unread_count(self, count: int) -> None:
        if count <= 0:
            self._message_badge.place_forget()
            return
        self._message_badge.configure(text=badge_text(count))
        self._message_badge.place(x=PANEL_WIDTH - 56, y=8)
        self._message_badge.lift()

    def _show_notice_tooltip(self, source: tk.Widget, value: str) -> None:
        if self._notice_tooltip_hide_job is not None:
            self.after_cancel(self._notice_tooltip_hide_job)
            self._notice_tooltip_hide_job = None
        # CTkButton may emit repeated enter notifications while moving across its
        # internal canvas/text widgets.  Keep the existing tip for this card rather
        # than destroying and recreating it on every notification.
        if self._notice_tooltip_source is source and self._notice_tooltip is not None and self._notice_tooltip.winfo_exists():
            return
        self._hide_notice_tooltip()
        tooltip = tk.Toplevel(self)
        tooltip.overrideredirect(True)
        tooltip.attributes("-topmost", True)
        tooltip.attributes("-transparentcolor", TRANSPARENT_KEY)
        tooltip.configure(bg=TRANSPARENT_KEY)
        font = tkfont.Font(family=ui_font_family(self.language), size=10)
        lines = _wrap_tooltip_characters(value, font, max_width=360)
        line_height = font.metrics("linespace")
        content_width = max((font.measure("".join(char for _, char in line)) for line in lines), default=0)
        bubble_width, bubble_height = tooltip_content_size(content_width, line_height * len(lines))
        canvas = tk.Canvas(
            tooltip, width=bubble_width, height=bubble_height, bg=TRANSPARENT_KEY,
            highlightthickness=0, bd=0, cursor="arrow", takefocus=False,
        )
        canvas.pack()
        _draw_rounded_rectangle(canvas, 0, 0, bubble_width, bubble_height, radius=10, fill=TOOLTIP_BG)
        highlighted = {index for start, end in reset_spans(value) for index in range(start, end)}
        for line_number, line in enumerate(lines):
            x, y = 10, 8 + line_number * line_height
            run: list[str] = []
            run_highlighted: bool | None = None
            for index, char in [*line, (None, "")]:
                is_highlighted = index in highlighted if index is not None else None
                if run and is_highlighted != run_highlighted:
                    segment = "".join(run)
                    width = font.measure(segment)
                    if run_highlighted:
                        canvas.create_rectangle(x, y + 2, x + width, y + line_height - 2, fill=TOOLTIP_RESET_HIGHLIGHT, outline="")
                    canvas.create_text(x, y, text=segment, anchor="nw", fill=TOOLTIP_TEXT, font=font)
                    x += width
                    run = []
                if index is not None:
                    run.append(char)
                    run_highlighted = is_highlighted
        tooltip.update_idletasks()
        # Keep it directly to the card's right, with their bottom edges aligned.
        x = source.winfo_rootx() + source.winfo_width() + 10
        y = max(8, source.winfo_rooty() + source.winfo_height() - tooltip.winfo_reqheight())
        tooltip.geometry(f"+{x}+{y}")
        tooltip.bind("<Enter>", lambda _event: self._cancel_notice_tooltip_hide())
        tooltip.bind("<Leave>", lambda _event, source=source: self._schedule_notice_tooltip_hide(source))
        canvas.bind("<Enter>", lambda _event: self._cancel_notice_tooltip_hide())
        canvas.bind("<Leave>", lambda _event, source=source: self._schedule_notice_tooltip_hide(source))
        self._notice_tooltip = tooltip
        self._notice_tooltip_source = source

    def _schedule_notice_tooltip_hide(self, source: tk.Widget) -> None:
        if self._notice_tooltip_hide_job is not None:
            self.after_cancel(self._notice_tooltip_hide_job)
        self._notice_tooltip_hide_job = self.after(80, lambda: self._hide_tooltip_if_pointer_left(source))

    def _cancel_notice_tooltip_hide(self) -> None:
        if self._notice_tooltip_hide_job is not None:
            self.after_cancel(self._notice_tooltip_hide_job)
            self._notice_tooltip_hide_job = None

    def _hide_tooltip_if_pointer_left(self, source: tk.Widget) -> None:
        self._notice_tooltip_hide_job = None
        if self._notice_tooltip_source is not source:
            return
        pointer_x, pointer_y = self.winfo_pointerxy()
        left, top = source.winfo_rootx(), source.winfo_rooty()
        if left <= pointer_x < left + source.winfo_width() and top <= pointer_y < top + source.winfo_height():
            return
        tooltip = self._notice_tooltip
        if tooltip is not None and tooltip.winfo_exists():
            left, top = tooltip.winfo_rootx(), tooltip.winfo_rooty()
            if left <= pointer_x < left + tooltip.winfo_width() and top <= pointer_y < top + tooltip.winfo_height():
                return
        self._hide_notice_tooltip()
        self.configure(cursor="")

    def _hide_notice_tooltip(self) -> None:
        if self._notice_tooltip_hide_job is not None:
            self.after_cancel(self._notice_tooltip_hide_job)
            self._notice_tooltip_hide_job = None
        if self._notice_tooltip is not None:
            self._notice_tooltip.destroy()
            self._notice_tooltip = None
        self._notice_tooltip_source = None

    # ---- usage data + bar animation ----

    def refresh_data(self) -> None:
        result = self.controller.service.result_for("codex")
        self._animate_row(self.session_row, result.session.percent, result.session.resets_at)
        self._animate_row(self.weekly_row, result.weekly.percent, result.weekly.resets_at)
        self._updated_label.configure(text=format_updated(result.refreshed_at, self.language) if result.status == "ok" else "")

    def _animate_row(self, row: dict, percent: int | None, reset_at) -> None:
        if row["title_key"] == "session_title":
            self._last_session_reset = reset_at
        else:
            self._last_weekly_reset = reset_at
        row["reset"].configure(text=format_reset(reset_at, self.language))
        target = None if percent is None else remaining(percent) / 100
        start = row["bar"].get()
        self._tick_bar(row, start, target or 0.0, percent, time.monotonic())

    def _tick_bar(self, row: dict, start: float, target: float, raw_percent: int | None, t0: float) -> None:
        elapsed = time.monotonic() - t0
        t = min(1.0, elapsed / 0.32)
        eased = 1 - (1 - t) ** 3
        value = start + (target - start) * eased
        row["bar"].set(value)
        row["value"].configure(text="—" if raw_percent is None else f"{round(value * 100)}%")
        if t < 1.0:
            self.after(ANIM_STEP_MS, lambda: self._tick_bar(row, start, target, raw_percent, t0))

    # ---- settings view ----

    def _toggle_view(self) -> None:
        if self.view == "usage":
            self._enter_settings()
        else:
            self._leave_auxiliary_view()

    def _enter_settings(self) -> None:
        self.view = "settings"
        self._nav_button.configure(image=self._close_image)
        self._load_settings_values()
        self.settings_frame.lift()
        self._nav_button.lift()
        self._message_button.lift()
        self._message_badge.lift()

    def _leave_auxiliary_view(self) -> None:
        if self.view == "settings":
            self._commit_interval()
        self._hide_notice_tooltip()
        self.view = "usage"
        self._nav_button.configure(image=self._gear_image)
        self.usage_frame.lift()
        self._nav_button.lift()
        self._message_button.lift()
        self._message_badge.lift()

    def _load_settings_values(self) -> None:
        config = self.controller.store.load()
        self._interval_entry.delete(0, "end")
        self._interval_entry.insert(0, str(config.refresh_seconds))
        (self._login_switch.select if config.launch_at_login else self._login_switch.deselect)()
        self._language_button.configure(text="EN" if self.language == "en" else "中")

    def _commit_interval(self) -> None:
        try:
            seconds = max(30, int(self._interval_entry.get()))
        except ValueError:
            seconds = self.controller.store.load().refresh_seconds
        config = self.controller.store.load()
        self.controller.store.save(replace(config, refresh_seconds=seconds))
        self.controller.on_settings_changed()

    def _on_login_toggle(self) -> None:
        enabled = self._login_switch.get() == 1
        config = self.controller.store.load()
        self.controller.store.save(replace(config, launch_at_login=enabled))
        set_launch_at_login(enabled)
        self.controller.on_settings_changed()

    def _on_language_toggle(self) -> None:
        language = "en" if self.language == "zh" else "zh"
        self.language = language
        config = self.controller.store.load()
        self.controller.store.save(replace(config, language=language))
        self._language_button.configure(text="EN" if language == "en" else "中")
        self._retext()
        self.controller.on_language_changed(language)

    def _retext(self) -> None:
        self.title_label.configure(font=self._font(18, "bold"))
        self.subtitle_label.configure(font=self._font(18, "bold"))
        self._settings_title.configure(text=text(self.language, "settings"), font=self._font(20, "bold"))
        self._interval_label.configure(text=text(self.language, "refresh_interval"), font=self._font(13))
        self._seconds_label.configure(text=text(self.language, "seconds"), font=self._font(13))
        self._login_label.configure(text=text(self.language, "launch_at_login"), font=self._font(13))
        self._language_label.configure(text=text(self.language, "language"), font=self._font(13))
        self._language_button.configure(font=self._font(12, "bold"))
        self._notices_title.configure(text=text(self.language, "reset_alerts"), font=self._font(20, "bold"))
        self._notices_retry.configure(text=text(self.language, "retry"), font=self._font(12, "bold"))
        for row in (self.session_row, self.weekly_row):
            row["name"].configure(text=text(self.language, row["title_key"]), font=self._font(13, "bold"))
            row["value"].configure(font=self._font(13, "bold"))
            cached = self._last_session_reset if row["title_key"] == "session_title" else self._last_weekly_reset
            row["reset"].configure(text=format_reset(cached, self.language), font=self._font(11))
        result = self.controller.service.result_for("codex")
        self._updated_label.configure(text=format_updated(result.refreshed_at, self.language) if result.status == "ok" else "", font=self._font(10))

    # ---- show / hide ----

    def show_at(self, x: int, y: int, language: str, view: str = "usage") -> None:
        self.language = language
        self._retext()
        self.refresh_data()
        self.geometry(f"{PANEL_WIDTH}x{PANEL_HEIGHT}+{x}+{y}")
        self.deiconify()
        self.lift()
        self.focus_force()
        if view == "settings":
            self._enter_settings()
        else:
            self.view = "usage"
            self._nav_button.configure(image=self._gear_image)
            self.usage_frame.lift()
            self._nav_button.lift()
            self._message_button.lift()
            self._message_badge.lift()
        self._fade(0.0, 1.0)

    def hide_panel(self) -> None:
        self._fade(1.0, 0.0, on_done=self.withdraw)

    def _fade(self, start: float, end: float, on_done: Callable[[], None] | None = None, t0: float | None = None) -> None:
        t0 = t0 or time.monotonic()
        elapsed = time.monotonic() - t0
        t = min(1.0, elapsed / 0.15)
        value = start + (end - start) * t
        try:
            self.attributes("-alpha", value)
        except Exception:
            pass
        if t < 1.0:
            self.after(ANIM_STEP_MS, lambda: self._fade(start, end, on_done, t0))
        elif on_done:
            on_done()
