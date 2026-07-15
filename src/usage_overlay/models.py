from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal


@dataclass(frozen=True)
class WindowUsage:
    percent: int | None
    resets_at: datetime | None


@dataclass(frozen=True)
class ProviderResult:
    provider: Literal["codex"]
    status: Literal["ok", "failed"]
    source: str | None
    refreshed_at: datetime
    session: WindowUsage
    weekly: WindowUsage
    error: str | None = None

    @classmethod
    def failed(cls, provider: Literal["codex"], error: str) -> "ProviderResult":
        return cls(
            provider=provider,
            status="failed",
            source=None,
            refreshed_at=datetime.now(timezone.utc),
            session=WindowUsage(None, None),
            weekly=WindowUsage(None, None),
            error=error,
        )
