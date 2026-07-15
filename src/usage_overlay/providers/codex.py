from __future__ import annotations

import json
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from usage_overlay.config import CachedUsageSnapshot, ConfigStore
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


@dataclass(frozen=True)
class RateLimitEvent:
    timestamp: datetime
    limits: dict[str, Any]


def latest_rate_limit_event_from_file(path: Path) -> RateLimitEvent | None:
    for raw_line in _reverse_file_lines(path):
        if b'"rate_limits"' not in raw_line:
            continue
        try:
            record = json.loads(raw_line.decode("utf-8"))
            timestamp = datetime.fromisoformat(record["timestamp"].replace("Z", "+00:00")).astimezone(timezone.utc)
            limits = record.get("payload", {}).get("rate_limits")
        except (AttributeError, KeyError, TypeError, UnicodeDecodeError, ValueError, json.JSONDecodeError):
            continue
        if isinstance(limits, dict):
            return RateLimitEvent(timestamp, limits)
    return None


class CodexProvider:
    def __init__(self, codex_directory: Path, store: ConfigStore | None = None) -> None:
        self.codex_directory = codex_directory
        self.store = store

    @classmethod
    def default(cls, store: ConfigStore | None = None) -> "CodexProvider":
        return cls(Path.home() / ".codex", store)

    def parse_rate_limits(self, limits: dict[str, Any], refreshed_at: datetime | None = None) -> ProviderResult:
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
            provider="codex", status="ok", source="Codex logs", refreshed_at=refreshed_at or datetime.now(timezone.utc),
            session=windows["session"], weekly=windows["weekly"],
        )

    @staticmethod
    def _snapshot_timestamp(snapshot: CachedUsageSnapshot) -> datetime:
        return datetime.fromisoformat(snapshot.timestamp.replace("Z", "+00:00")).astimezone(timezone.utc)

    def _cached_result(self, snapshot: CachedUsageSnapshot) -> ProviderResult:
        def window(percent: int | None, resets_at: int | float | None) -> WindowUsage:
            reset = datetime.fromtimestamp(resets_at, timezone.utc) if isinstance(resets_at, (int, float)) else None
            return WindowUsage(percent, reset)

        return ProviderResult(
            provider="codex", status="ok", source="Cached Codex snapshot", refreshed_at=self._snapshot_timestamp(snapshot),
            session=window(snapshot.session_percent, snapshot.session_resets_at),
            weekly=window(snapshot.weekly_percent, snapshot.weekly_resets_at),
        )

    def _save_snapshot(self, result: ProviderResult) -> None:
        if self.store is None:
            return

        def epoch(window: WindowUsage) -> int | None:
            return int(window.resets_at.timestamp()) if window.resets_at is not None else None

        snapshot = CachedUsageSnapshot(
            result.refreshed_at.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
            result.session.percent,
            epoch(result.session),
            result.weekly.percent,
            epoch(result.weekly),
        )
        config = self.store.load()
        self.store.save(replace(config, usage_snapshot=snapshot))

    def fetch(self) -> ProviderResult:
        sessions = self.codex_directory / "sessions"
        cached = self.store.load().usage_snapshot if self.store is not None else None
        latest: RateLimitEvent | None = None
        if sessions.exists():
            for path in sessions.rglob("*.jsonl"):
                try:
                    event = latest_rate_limit_event_from_file(path)
                except OSError:
                    continue
                if event is not None and (latest is None or event.timestamp > latest.timestamp):
                    latest = event
        if latest is not None:
            if cached is not None and latest.timestamp <= self._snapshot_timestamp(cached):
                return self._cached_result(cached)
            result = self.parse_rate_limits(latest.limits, latest.timestamp)
            if result.status == "ok":
                self._save_snapshot(result)
                return result
        if cached is not None:
            return self._cached_result(cached)
        if not sessions.exists():
            return ProviderResult.failed("codex", "Codex sessions directory was not found")
        return ProviderResult.failed("codex", "No Codex rate-limit records found")
