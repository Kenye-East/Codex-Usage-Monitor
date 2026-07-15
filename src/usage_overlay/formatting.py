from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

from usage_overlay.i18n import text


def remaining(percent: int | None) -> int | None:
    return None if percent is None else max(0, min(100, 100 - percent))


def icon_name() -> str:
    return "openai-icon.png"


def asset_path(name: str) -> Path:
    # In a PyInstaller onefile build, __file__ resolves to a path inside the
    # temp extraction directory, not the project root, so bundled data (like
    # assets/) must be found via sys._MEIPASS instead when frozen.
    root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[2]))
    return root / "assets" / name


# Bahnschrift ships with Windows 10 1709+ and pairs well with Noto Sans SC's
# proportions for a clean, modern look without needing to bundle a Latin font.
FONT_EN = ("Bahnschrift", "Segoe UI", "Arial")
FONT_ZH = ("Noto Sans SC", "Microsoft YaHei UI", "Segoe UI")


def font_candidates(language: str) -> tuple[str, ...]:
    return FONT_ZH if language == "zh" else FONT_EN


def _font_is_installed(family: str) -> bool:
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Fonts") as key:
            index = 0
            needle = family.casefold()
            while True:
                try:
                    name, _, _ = winreg.EnumValue(key, index)
                except OSError:
                    return False
                if needle in name.casefold():
                    return True
                index += 1
    except OSError:
        return False


def ui_font_family(language: str) -> str:
    candidates = font_candidates(language)
    return next((family for family in candidates if _font_is_installed(family)), candidates[-1])


def format_reset(value: datetime | None, language: str) -> str:
    if value is None:
        return ""
    local = value.astimezone()
    stamp = f"{local:%m月%d日 %H:%M}" if language == "zh" else f"{local:%b %d, %H:%M}"
    return text(language, "resets").format(time=stamp)


def format_updated(value: datetime, language: str) -> str:
    local = value.astimezone()
    stamp = f"{local.month}月{local.day}日 {local:%H:%M}" if language == "zh" else f"{local:%b %d, %H:%M}"
    return text(language, "updated").format(time=stamp)
