from usage_overlay.config import AppConfig, ConfigStore, resolve_language


def test_config_defaults_and_language_override(tmp_path):
    store = ConfigStore(tmp_path / "config.json")

    assert store.load().refresh_seconds == 60
    assert store.load().launch_at_login is True

    store.save(AppConfig(refresh_seconds=120, launch_at_login=False, language="en", taskbar_x=420))

    assert store.load() == AppConfig(refresh_seconds=120, launch_at_login=False, language="en", taskbar_x=420)


def test_default_store_migrates_the_legacy_product_config(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    legacy = tmp_path / "UsageOverlay" / "config.json"
    legacy.parent.mkdir()
    legacy.write_text('{"refresh_seconds": 120, "launch_at_login": false, "language": "en", "taskbar_x": 420}', encoding="utf-8")

    store = ConfigStore()

    assert store.path == tmp_path / "Codex-Usage-Monitor" / "config.json"
    assert store.load() == AppConfig(refresh_seconds=120, launch_at_login=False, language="en", taskbar_x=420)
    assert store.path.exists()


def test_config_persists_read_message_urls(tmp_path):
    store = ConfigStore(tmp_path / "config.json")
    config = AppConfig(read_post_urls=("https://x.com/a/status/1", "https://x.com/b/status/2"))

    store.save(config)

    assert store.load().read_post_urls == config.read_post_urls


def test_resolve_language_uses_system_default_when_no_preference_is_saved(monkeypatch):
    monkeypatch.setattr("usage_overlay.config.default_language", lambda: "en")

    assert resolve_language(None) == "en"
    assert resolve_language("zh") == "zh"
