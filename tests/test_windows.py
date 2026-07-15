from pathlib import Path

from usage_overlay.windows import ERROR_ALREADY_EXISTS, RUN_NAME, SingleInstance, launch_command, run_value


def test_startup_registry_name_uses_the_current_product_name():
    assert RUN_NAME == "Codex-Usage-Monitor"


def test_run_value_quotes_executable_path():
    assert run_value(r"C:\Program Files\UsageOverlay\UsageOverlay.exe") == r'"C:\Program Files\UsageOverlay\UsageOverlay.exe"'


def test_development_autostart_uses_pythonw_and_module_entrypoint():
    assert launch_command(Path(r"C:\Project\.venv\Scripts\python.exe"), frozen=False) == r'"C:\Project\.venv\Scripts\pythonw.exe" -m usage_overlay.main'


def test_frozen_autostart_runs_the_packaged_executable_directly():
    assert launch_command(Path(r"C:\Program Files\Codex-Usage-Monitor.exe"), frozen=True) == r'"C:\Program Files\Codex-Usage-Monitor.exe"'


def test_single_instance_releases_a_duplicate_mutex_handle():
    released: list[int] = []
    guard = SingleInstance(
        create_mutex=lambda _name: 42,
        get_last_error=lambda: ERROR_ALREADY_EXISTS,
        close_handle=released.append,
    )

    assert guard.acquire() is False
    assert released == [42]


def test_single_instance_releases_its_mutex_handle_on_shutdown():
    released: list[int] = []
    guard = SingleInstance(
        create_mutex=lambda _name: 42,
        get_last_error=lambda: 0,
        close_handle=released.append,
    )

    assert guard.acquire() is True
    guard.release()
    guard.release()

    assert released == [42]
