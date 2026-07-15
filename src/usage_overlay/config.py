from __future__ import annotations

import json
import locale
import os
import shutil
from threading import RLock
from dataclasses import asdict, dataclass
from pathlib import Path


# ~4cm from the left edge of the screen at standard 96 DPI (100% display scaling).
DEFAULT_TASKBAR_X = 151


@dataclass(frozen=True)
class AppConfig:
    refresh_seconds: int = 60
    launch_at_login: bool = True
    language: str | None = None
    taskbar_x: int = DEFAULT_TASKBAR_X
    read_post_urls: tuple[str, ...] = ()


def default_language() -> str:
    name = (locale.getlocale()[0] or "").lower()
    return "zh" if name.startswith("zh") else "en"


def resolve_language(language: str | None) -> str:
    return language if language in {"zh", "en"} else default_language()


class ConfigStore:
    def __init__(self, path: Path | None = None) -> None:
        app_data = Path(os.environ.get("LOCALAPPDATA", Path.home()))
        default_path = app_data / "Codex-Usage-Monitor" / "config.json"
        self.path = path or default_path
        self._lock = RLock()
        if path is None and not self.path.exists():
            for legacy_name in ("Codex-Claude-Usage-Monitor", "UsageOverlay"):
                legacy_path = app_data / legacy_name / "config.json"
                if legacy_path.exists():
                    self.path.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(legacy_path, self.path)
                    break

    def load(self) -> AppConfig:
        with self._lock:
            if not self.path.exists():
                return AppConfig()
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
                refresh = int(data.get("refresh_seconds", 60))
                if refresh < 30:
                    refresh = 30
                language = data.get("language")
                urls = data.get("read_post_urls", [])
                read_urls = tuple(url for url in urls if isinstance(url, str)) if isinstance(urls, list) else ()
                return AppConfig(refresh, bool(data.get("launch_at_login", True)), language if language in {"zh", "en", None} else None, int(data.get("taskbar_x", DEFAULT_TASKBAR_X)), read_urls)
            except (OSError, ValueError, json.JSONDecodeError):
                return AppConfig()

    def save(self, config: AppConfig) -> None:
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix(f".{os.getpid()}.tmp")
            temporary.write_text(json.dumps(asdict(config), ensure_ascii=False, indent=2), encoding="utf-8")
            temporary.replace(self.path)
