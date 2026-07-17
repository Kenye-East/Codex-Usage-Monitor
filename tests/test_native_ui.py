from datetime import datetime, timezone

from usage_overlay.models import ProviderResult, WindowUsage
from usage_overlay.native_ui import COMPACT_EX_STYLE, COMPACT_WINDOW_STYLE, COMPACT_Z_ORDER, HWND_TOP, MENU_ACTION_CODES, NATIVE_MENU_IDS, WS_CHILD, WS_EX_TOPMOST, WS_POPUP, compact_layered_destination, clamp_taskbar_x, compact_lines, native_menu_items, panel_origin, taskbar_child_y
from usage_overlay.native_ui import NativeOverlay
from usage_overlay.panel import PANEL_TOPMOST


def test_compact_lines_show_remaining_usage_not_used_usage():
    result = ProviderResult("codex", "ok", "Codex logs", datetime.now(timezone.utc), WindowUsage(12, None), WindowUsage(46, None))

    assert compact_lines(result, "en") == ("Session 88%", "Weekly 54%")


def test_taskbar_x_is_clamped_to_the_visible_taskbar_width():
    assert clamp_taskbar_x(18827, taskbar_width=1920, overlay_width=300) == 1612


def test_expanded_panel_is_anchored_above_compact_taskbar_strip():
    assert panel_origin(420, 1036, compact_width=300, panel_width=370, panel_height=290) == (350, 738)


def test_native_overlay_has_no_provider_switching_entrypoint():
    assert not hasattr(NativeOverlay, "_switch_provider")


def test_compact_overlay_is_a_non_topmost_taskbar_child_window():
    assert COMPACT_EX_STYLE & WS_EX_TOPMOST == 0
    assert COMPACT_WINDOW_STYLE & WS_CHILD == WS_CHILD
    assert COMPACT_WINDOW_STYLE & WS_POPUP == 0
    assert COMPACT_Z_ORDER == HWND_TOP
    assert PANEL_TOPMOST is False


def test_compact_overlay_uses_taskbar_relative_vertical_coordinates():
    assert taskbar_child_y(taskbar_height=48, overlay_height=40) == 4


def test_taskbar_child_layered_render_keeps_its_existing_position():
    assert compact_layered_destination() is None


def test_native_menu_maps_messages_to_its_own_action_code():
    assert MENU_ACTION_CODES == {"panel": 0, "settings": 1, "messages": 2, "exit": 3}


def test_native_context_menu_has_localized_commands():
    assert native_menu_items("zh") == ((100, "面板"), (101, "设置"), (102, "消息"), (103, "退出"))
    assert NATIVE_MENU_IDS == {100: "panel", 101: "settings", 102: "messages", 103: "exit"}


def test_native_run_guard_stops_the_panel_after_an_unhandled_error():
    class Panel:
        stopped = False

        def stop(self):
            self.stopped = True

    class BrokenOverlay:
        panel = Panel()

        def run(self):
            raise RuntimeError("taskbar unavailable")

    NativeOverlay._run_safely(BrokenOverlay())

    assert BrokenOverlay.panel.stopped is True
