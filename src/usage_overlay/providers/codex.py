from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from usage_overlay.models import ProviderResult, WindowUsage


def classify_window(minutes: int | float | None) -> Literal["session", "weekly"] | None:
    if minutes is not None and 240 <= minutes <= 360:
        return "session"
    if minutes is not None and 10000 <= minutes <= 10160:
        return "weekly"
    return None


def _reverse_file_lines(path: Path, block_size: int = 64 * 1024):
    """Yield UTF-8 JSONL lines from the file tail without loading the whole log."""
    with path.open("rb") as handle:
        position = handle.seek(0, 2)
        remainder = b""
        while position > 0:
            chunk_size = min(block_size, position)
            position -= chunk_size
            handle.seek(position)
            data = handle.read(chunk_size) + remainder
            parts = data.split(b"\n")
            remainder = parts[0]
            for line in reversed(parts[1:]):
                if line:
                    yield line
        if remainder:
            yield remainder


def latest_rate_limits_from_file(path: Path) -> dict[str, Any] | None:
    for raw_line in _reverse_file_lines(path):
        if b'"rate_limits"' not in raw_line:
            continue
        try:
            payload = json.loads(raw_line.decode("utf-8")).get("payload", {})
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
        if isinstance(payload, dict) and isinstance(payload.get("rate_limits"), dict):
            return payload["rate_limits"]
    return None


class CodexProvider:
    def __init__(self, codex_directory: Path) -> None:
        self.codex_directory = codex_directory

    @classmethod
    def default(cls) -> "CodexProvider":
        return cls(Path.home() / ".codex")

    def parse_rate_limits(self, limits: dict[str, Any]) -> ProviderResult:
        windows = {"session": WindowUsage(None, None), "weekly": WindowUsage(None, None)}
        for raw in limits.values():
            if not isinstance(raw, dict):
                continue
            kind = classify_window(raw.get("window_minutes"))
            if kind is None:
                continue
            try:
                percent = max(0, min(100, int(raw["used_percent"])))
            except (KeyError, TypeError, ValueError):
                continue
            reset = raw.get("resets_at")
            reset_at = datetime.fromtimestamp(reset, timezone.utc) if isinstance(reset, (int, float)) else None
            windows[kind] = WindowUsage(percent, reset_at)
        if all(window.percent is None for window in windows.values()):
            return ProviderResult.failed("codex", "No recognized Codex rate-limit window")
        return ProviderResult(
            provider="codex", status="ok", source="Codex logs", refreshed_at=datetime.now(timezone.utc),
            session=windows["session"], weekly=windows["weekly"],
        )

    def fetch(self) -> ProviderResult:
        sessions = self.codex_directory / "sessions"
        if not sessions.exists():
            return ProviderResult.failed("codex", "Codex sessions directory was not found")
        files = sorted(sessions.rglob("*.jsonl"), key=lambda item: item.stat().st_mtime, reverse=True)
        for path in files:
            try:
                latest = latest_rate_limits_from_file(path)
            except OSError:
                continue
            if latest is not None:
                result = self.parse_rate_limits(latest)
                if result.status == "ok":
                    # A rate-limit record is a point-in-time snapshot.  Never fill a
                    # missing current window from older logs: retired limits would
                    # otherwise reappear with stale percentages and reset times.
                    return result
        return ProviderResult.failed("codex", "No Codex rate-limit records found")
