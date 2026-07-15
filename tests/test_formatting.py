from datetime import datetime, timezone

from usage_overlay.formatting import font_candidates, format_reset, icon_name, remaining


def test_remaining_is_none_when_percent_is_unknown():
    assert remaining(None) is None
    assert remaining(30) == 70


def test_provider_icon_uses_the_original_project_asset_names():
    assert icon_name() == "openai-icon.png"


def test_format_reset_is_empty_when_no_reset_time_is_known():
    assert format_reset(None, "en") == ""


def test_format_reset_renders_localized_reset_time():
    value = datetime(2026, 7, 14, 18, 30, tzinfo=timezone.utc)

    assert format_reset(value, "en").startswith("Resets ")


def test_chinese_font_keeps_noto_as_default_but_has_system_fallbacks():
    assert font_candidates("zh") == ("Noto Sans SC", "Microsoft YaHei UI", "Segoe UI")
