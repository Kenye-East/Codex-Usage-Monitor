from datetime import datetime, timezone

from usage_overlay.i18n import text
from usage_overlay.panel import PANEL_CLOSE_ON_FOCUS_LOSS, PANEL_HEIGHT, PANEL_WIDTH, SESSION_ROW_Y, TOOLTIP_BG, TOOLTIP_RESET_HIGHLIGHT, TOOLTIP_TEXT, USAGE_HEADER_LINE_HEIGHT, USAGE_HEADER_LINE_GAP, VERIFIED_LABEL_Y, WEEKLY_ROW_Y, badge_text, format_post_time, full_post_text, notice_empty_state_bounds, notice_empty_state_placement, reset_spans, tooltip_content_size
from usage_overlay.reset_feed import ResetPost
from usage_overlay.config import ConfigStore
from usage_overlay.panel import PanelController
from usage_overlay.refresh import RefreshService


def test_full_post_text_does_not_repeat_equal_title_and_description() -> None:
    post = ResetPost("codex", "We've reset the weekly limit.", "We've reset the weekly limit.", "https://x.com/a/status/1", datetime.now(timezone.utc))

    assert full_post_text(post) == "We've reset the weekly limit."


def test_format_post_time_is_localized_without_changing_card_layout() -> None:
    published_at = datetime(2026, 7, 15, 5, 30, tzinfo=timezone.utc)

    assert format_post_time(published_at, "zh") == "7月15日 13:30"
    assert format_post_time(published_at, "en") == "Jul 15, 13:30"


def test_badge_text_caps_large_unread_counts() -> None:
    assert badge_text(0) == ""
    assert badge_text(7) == "7"
    assert badge_text(120) == "99+"


def test_message_context_menu_has_localized_label() -> None:
    assert text("en", "menu_messages") == "Messages"
    assert text("zh", "menu_messages") == "消息"


def test_usage_header_uses_compact_non_overlapping_line_boxes() -> None:
    assert USAGE_HEADER_LINE_HEIGHT == 26
    assert USAGE_HEADER_LINE_GAP == -4


def test_non_topmost_panel_closes_when_focus_moves_outside_it():
    assert PANEL_CLOSE_ON_FOCUS_LOSS is True


def test_verified_time_sits_below_the_logo_and_usage_rows_move_down() -> None:
    assert VERIFIED_LABEL_Y == 68
    assert SESSION_ROW_Y == 120
    assert WEEKLY_ROW_Y == 194


def test_notice_tooltip_uses_panel_color_family() -> None:
    assert TOOLTIP_BG == "#D9EEE3"
    assert TOOLTIP_TEXT == "#12382D"
    assert TOOLTIP_RESET_HIGHLIGHT == "#B8DDC9"


def test_reset_spans_marks_reset_words_only() -> None:
    assert reset_spans("We reset the limit after resetting it.") == [(3, 8), (25, 34)]
    assert reset_spans("Preset values are unchanged.") == []


def test_tooltip_container_fits_its_content_instead_of_default_frame_size() -> None:
    assert tooltip_content_size(360, 32) == (380, 48)


def test_notice_empty_state_covers_the_message_list_and_is_centered_in_the_panel() -> None:
    assert notice_empty_state_bounds() == (20, 70, PANEL_WIDTH - 40, PANEL_HEIGHT - 90)


def test_notice_empty_state_uses_tk_place_keyword_coordinates() -> None:
    assert notice_empty_state_placement() == {"x": 20, "y": 70}


def test_retry_notices_replaces_a_failed_snapshot_with_new_posts(tmp_path, monkeypatch) -> None:
    post = ResetPost("codex", "reset", "", "https://x.com/a/status/1", datetime.now(timezone.utc))

    class Feed:
        calls = 0

        def fetch(self):
            self.calls += 1
            if self.calls == 1:
                raise OSError("offline")
            return [post]

    class ImmediateThread:
        def __init__(self, target, **_kwargs):
            self.target = target

        def start(self):
            self.target()

    monkeypatch.setattr("usage_overlay.panel.threading.Thread", ImmediateThread)
    controller = PanelController(RefreshService({}), ConfigStore(tmp_path / "config.json"), lambda _language: None, lambda: None, lambda _action: None, lambda _count: None)
    controller.reset_feed = Feed()

    controller.load_startup_notices()
    assert controller.startup_notice_snapshot()[2] == ["offline"]

    controller.retry_notices()
    loaded, posts, errors = controller.startup_notice_snapshot()
    assert loaded is True
    assert posts == [post]
    assert errors == []


def test_open_notices_at_requests_a_visible_notices_panel(tmp_path) -> None:
    controller = PanelController(RefreshService({}), ConfigStore(tmp_path / "config.json"), lambda _language: None, lambda: None, lambda _action: None, lambda _count: None)

    controller.open_notices_at(420, 700, "zh")

    assert controller.is_open is True
    queued = [controller._queue.get_nowait(), controller._queue.get_nowait()]
    assert ("show_notices", 420, 700, "zh") in queued
