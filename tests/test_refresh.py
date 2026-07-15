from usage_overlay.models import ProviderResult, WindowUsage
from usage_overlay.refresh import RefreshService


class SuccessProvider:
    def fetch(self):
        return ProviderResult("codex", "ok", "Codex logs", __import__("datetime").datetime.now(__import__("datetime").timezone.utc), WindowUsage(None, None), WindowUsage(41, None))


class FailedProvider:
    def fetch(self):
        return ProviderResult.failed("codex", "network failure")


def test_failed_refresh_keeps_last_verified_result():
    service = RefreshService({"codex": SuccessProvider()})
    service.refresh_all()
    service.providers["codex"] = FailedProvider()
    service.refresh_all()

    assert service.result_for("codex").weekly.percent == 41


def test_callback_observes_result_after_it_is_published():
    service = RefreshService({"codex": SuccessProvider()})
    observed = []

    service.refresh_all(lambda provider: observed.append(service.result_for(provider).weekly.percent))

    assert observed == [41]


def test_snapshot_is_a_copy_not_the_mutable_internal_results_dictionary():
    service = RefreshService({"codex": SuccessProvider()})
    service.refresh_all()
    snapshot = service.snapshot()
    snapshot.clear()

    assert service.result_for("codex").weekly.percent == 41
