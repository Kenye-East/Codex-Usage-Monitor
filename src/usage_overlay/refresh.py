from __future__ import annotations

from threading import RLock
from typing import Callable, Protocol

from usage_overlay.models import ProviderResult


class Provider(Protocol):
    def fetch(self) -> ProviderResult: ...


class RefreshService:
    def __init__(self, providers: dict[str, Provider]) -> None:
        self.providers = providers
        self.results: dict[str, ProviderResult] = {}
        self._results_lock = RLock()

    def refresh_all(self, on_provider_done: Callable[[str], None] | None = None) -> dict[str, ProviderResult]:
        # The sole local Codex provider runs away from the UI threads and publishes
        # its result before the overlay redraw callback executes.
        for name, provider in self.providers.items():
            result = provider.fetch()
            with self._results_lock:
                if result.status == "ok" or name not in self.results:
                    self.results[name] = result
            if on_provider_done is not None:
                on_provider_done(name)
        return self.snapshot()

    def result_for(self, provider: str) -> ProviderResult:
        with self._results_lock:
            return self.results.get(provider, ProviderResult.failed(provider, "Request failed"))

    def snapshot(self) -> dict[str, ProviderResult]:
        with self._results_lock:
            return self.results.copy()
