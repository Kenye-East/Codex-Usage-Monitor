from usage_overlay.models import ProviderResult


def test_missing_windows_are_not_zero():
    result = ProviderResult.failed("codex", "No weekly window")

    assert result.weekly.percent is None
    assert result.status == "failed"
