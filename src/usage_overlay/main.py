import ctypes
import logging

from usage_overlay.config import ConfigStore
from usage_overlay.providers.codex import CodexProvider
from usage_overlay.refresh import RefreshService
from usage_overlay.runtime import configure_logging
from usage_overlay.native_ui import NativeOverlay
from usage_overlay.windows import SingleInstance, set_launch_at_login


def main() -> None:
    logger = configure_logging()
    instance = SingleInstance()
    if not instance.acquire():
        return
    try:
        try:
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Codex-Usage-Monitor.App")
        except OSError:
            pass
        store = ConfigStore()
        config = store.load()
        try:
            set_launch_at_login(config.launch_at_login)
        except OSError:
            logger.exception("Could not update the startup registry entry")
        service = RefreshService({"codex": CodexProvider.default(store)})
        overlay = NativeOverlay(service, store)
        overlay.start()
        try:
            overlay.panel.start()
        finally:
            overlay.stop()
    except Exception:
        logger.exception("Application stopped unexpectedly")
        raise
    finally:
        instance.release()


if __name__ == "__main__":
    main()
