"""JSON logs also go to a daily-rotating file when LOG_DIR is set (spec v0.8.0 §4)."""

import json
import logging
import logging.handlers
import os
from pathlib import Path

import pytest

from app.core.config import get_settings
from app.core.logging import configure_logging, get_logger


@pytest.fixture
def restore_root_logger() -> object:
    root = logging.getLogger()
    handlers = list(root.handlers)
    yield
    for handler in list(root.handlers):
        root.removeHandler(handler)
        if isinstance(handler, logging.FileHandler):
            handler.close()
    for handler in handlers:
        root.addHandler(handler)


def test_a_log_dir_adds_a_rotating_json_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, restore_root_logger: object
) -> None:
    monkeypatch.setenv("LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("LOG_FILE", "api.log")
    monkeypatch.setenv("LOG_KEEP_DAYS", "14")
    monkeypatch.setenv("JWT_SECRET", "log-file-secret-value-0123456789")
    get_settings.cache_clear()

    configure_logging()
    get_logger("app.test").info("hello file", extra={"password": "hunter2-not-for-logs"})

    file_handlers = [
        h
        for h in logging.getLogger().handlers
        if isinstance(h, logging.handlers.TimedRotatingFileHandler)
    ]
    assert len(file_handlers) == 1
    handler = file_handlers[0]
    assert handler.when == "MIDNIGHT"
    assert handler.backupCount == 14
    handler.flush()

    path = tmp_path / "logs" / "api.log"
    assert path.is_file()
    lines = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    record = next(line for line in lines if line["message"] == "hello file")
    assert record["level"] == "INFO"
    assert record["password"] == "[REDACTED]"
    assert "hunter2" not in path.read_text()
    assert "log-file-secret-value" not in path.read_text()


def test_no_log_dir_means_stdout_only(
    monkeypatch: pytest.MonkeyPatch, restore_root_logger: object
) -> None:
    monkeypatch.setenv("LOG_DIR", "")
    get_settings.cache_clear()

    configure_logging()

    assert not any(isinstance(h, logging.FileHandler) for h in logging.getLogger().handlers)
    assert os.environ.get("LOG_DIR") == ""
