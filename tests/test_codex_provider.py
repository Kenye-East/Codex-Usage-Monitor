import json
from datetime import datetime, timezone

from usage_overlay.config import AppConfig, CachedUsageSnapshot, ConfigStore
from usage_overlay.providers.codex import CodexProvider, latest_rate_limit_event_from_file, latest_rate_limits_from_file


def test_primary_weekly_window_is_not_rendered_as_session(tmp_path):
    provider = CodexProvider(tmp_path)
    result = provider.parse_rate_limits({
        "primary": {"used_percent": 41, "window_minutes": 10080, "resets_at": 1784541691},
        "secondary": None,
    })

    assert result.session.percent is None
    assert result.weekly.percent == 41


def test_windows_are_classified_by_duration_not_position(tmp_path):
    provider = CodexProvider(tmp_path)
    result = provider.parse_rate_limits({
        "primary": {"used_percent": 9, "window_minutes": 300, "resets_at": 100},
        "secondary": {"used_percent": 42, "window_minutes": 10080, "resets_at": 200},
    })

    assert (result.session.percent, result.weekly.percent) == (9, 42)


def test_fetch_does_not_restore_a_missing_window_from_an_older_snapshot(tmp_path):
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    (sessions / "older.jsonl").write_text(json.dumps({"timestamp": "2026-07-14T12:00:00Z", "payload": {"rate_limits": {
        "primary": {"used_percent": 18, "window_minutes": 300, "resets_at": 100},
    }}}) + "\n", encoding="utf-8")
    newest = sessions / "newest.jsonl"
    newest.write_text(json.dumps({"timestamp": "2026-07-15T12:00:00Z", "payload": {"rate_limits": {
        "primary": {"used_percent": 49, "window_minutes": 10080, "resets_at": 200},
    }}}) + "\n", encoding="utf-8")
    newest.touch()

    result = CodexProvider(tmp_path).fetch()

    assert result.status == "ok"
    assert (result.session.percent, result.weekly.percent) == (None, 49)


def test_latest_rate_limits_is_read_from_the_end_without_reading_the_whole_file(tmp_path, monkeypatch):
    path = tmp_path / "session.jsonl"
    path.write_text(
        "\n".join([
            json.dumps({"payload": {"rate_limits": {"primary": {"used_percent": 1, "window_minutes": 10080}}}}),
            json.dumps({"payload": {"event": "other"}}),
            json.dumps({"payload": {"rate_limits": {"primary": {"used_percent": 58, "window_minutes": 10080}}}}),
        ]),
        encoding="utf-8",
    )
    monkeypatch.setattr(type(path), "read_text", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must stream from file tail")))

    limits = latest_rate_limits_from_file(path)

    assert limits == {"primary": {"used_percent": 58, "window_minutes": 10080}}


def test_latest_rate_limit_event_uses_the_event_timestamp_not_the_file_mtime(tmp_path):
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    old_file = sessions / "touched-last.jsonl"
    old_file.write_text(json.dumps({"timestamp": "2026-07-14T12:00:00Z", "payload": {"rate_limits": {
        "primary": {"used_percent": 17, "window_minutes": 10080, "resets_at": 100},
    }}}) + "\n", encoding="utf-8")
    new_file = sessions / "actually-new.jsonl"
    new_file.write_text(json.dumps({"timestamp": "2026-07-15T12:00:00Z", "payload": {"rate_limits": {
        "primary": {"used_percent": 42, "window_minutes": 10080, "resets_at": 200},
    }}}) + "\n", encoding="utf-8")
    old_file.touch()

    result = CodexProvider(tmp_path).fetch()

    assert result.weekly.percent == 42
    assert result.refreshed_at == datetime(2026, 7, 15, 12, tzinfo=timezone.utc)
    assert latest_rate_limit_event_from_file(new_file).timestamp == result.refreshed_at


def test_fetch_uses_the_cached_complete_snapshot_when_sessions_are_missing(tmp_path):
    store = ConfigStore(tmp_path / "config.json")
    cached = CachedUsageSnapshot("2026-07-15T12:00:00Z", None, None, 42, 200)
    store.save(AppConfig(usage_snapshot=cached))

    result = CodexProvider(tmp_path, store).fetch()

    assert result.status == "ok"
    assert result.source == "Cached Codex snapshot"
    assert (result.session.percent, result.weekly.percent) == (None, 42)
    assert result.refreshed_at == datetime(2026, 7, 15, 12, tzinfo=timezone.utc)


def test_an_older_log_event_does_not_replace_a_newer_cached_snapshot(tmp_path):
    store = ConfigStore(tmp_path / "config.json")
    store.save(AppConfig(usage_snapshot=CachedUsageSnapshot("2026-07-16T12:00:00Z", None, None, 81, 300)))
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    (sessions / "old.jsonl").write_text(json.dumps({"timestamp": "2026-07-15T12:00:00Z", "payload": {"rate_limits": {
        "primary": {"used_percent": 42, "window_minutes": 10080, "resets_at": 200},
    }}}) + "\n", encoding="utf-8")

    result = CodexProvider(tmp_path, store).fetch()

    assert result.source == "Cached Codex snapshot"
    assert result.weekly.percent == 81
