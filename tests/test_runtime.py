import logging

from usage_overlay.runtime import configure_logging


def test_configure_logging_writes_to_the_local_app_log(tmp_path):
    logger = configure_logging(tmp_path)
    logger.error("startup registry write failed")

    assert (tmp_path / "app.log").read_text(encoding="utf-8").endswith("startup registry write failed\n")
