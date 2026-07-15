from __future__ import annotations

import sys
import ctypes
from pathlib import Path
from typing import Callable

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_NAME = "Codex-Usage-Monitor"
LEGACY_RUN_NAMES = ("Codex-Claude-Usage-Monitor", "UsageOverlay")
INSTANCE_MUTEX_NAME = r"Local\CodexUsageMonitor.SingleInstance"
ERROR_ALREADY_EXISTS = 183


class SingleInstance:
    """Hold a Windows mutex for the lifetime of the running monitor."""

    def __init__(
        self,
        create_mutex: Callable[[str], int] | None = None,
        get_last_error: Callable[[], int] | None = None,
        close_handle: Callable[[int], object] | None = None,
    ) -> None:
        self._create_mutex = create_mutex or self._create_windows_mutex
        self._get_last_error = get_last_error or self._get_windows_last_error
        self._close_handle = close_handle or self._close_windows_handle
        self._handle: int | None = None

    @staticmethod
    def _create_windows_mutex(name: str) -> int:
        return int(ctypes.windll.kernel32.CreateMutexW(None, False, name))

    @staticmethod
    def _get_windows_last_error() -> int:
        return int(ctypes.windll.kernel32.GetLastError())

    @staticmethod
    def _close_windows_handle(handle: int) -> object:
        return ctypes.windll.kernel32.CloseHandle(handle)

    def acquire(self) -> bool:
        handle = self._create_mutex(INSTANCE_MUTEX_NAME)
        if not handle:
            raise ctypes.WinError()
        if self._get_last_error() == ERROR_ALREADY_EXISTS:
            self._close_handle(handle)
            return False
        self._handle = handle
        return True

    def release(self) -> None:
        if self._handle is not None:
            self._close_handle(self._handle)
            self._handle = None


def run_value(executable: str) -> str:
    return f'"{executable}"'


def launch_command(executable: Path, frozen: bool) -> str:
    if frozen:
        return run_value(str(executable))
    return f'{run_value(str(executable.with_name("pythonw.exe")))} -m usage_overlay.main'


def set_launch_at_login(enabled: bool, command: str | None = None) -> None:
    import winreg

    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
        if enabled:
            default = launch_command(Path(sys.executable), bool(getattr(sys, "frozen", False)))
            winreg.SetValueEx(key, RUN_NAME, 0, winreg.REG_SZ, command or default)
            for name in LEGACY_RUN_NAMES:
                try:
                    winreg.DeleteValue(key, name)
                except FileNotFoundError:
                    pass
        else:
            for name in (RUN_NAME, *LEGACY_RUN_NAMES):
                try:
                    winreg.DeleteValue(key, name)
                except FileNotFoundError:
                    pass
