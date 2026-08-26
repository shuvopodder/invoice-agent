from __future__ import annotations

import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path

LOG_DIR = Path(__file__).resolve().parent.parent / "logs"

# Anything that looks like a header value or API key gets masked before it
# can reach a log line. Keys in this repo are demo/local (the mock API's
# hardcoded X-API-Key, or a real ANTHROPIC_API_KEY) but the habit is the
# point -- nothing that looks like a secret should ever round-trip through
# a log file, in a demo or otherwise.
_SECRET_PATTERNS = [
    re.compile(r"(sk-ant-[A-Za-z0-9\-_]+)"),
    re.compile(r"(demo-key-\d+)"),
]


class _RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        for pattern in _SECRET_PATTERNS:
            msg = pattern.sub("[REDACTED]", msg)
        if msg != record.getMessage():
            record.msg = msg
            record.args = ()
        return True


def setup_logging(level: str | None = None) -> Path:
    """Configures root logging. Returns the path of the file handler's log
    file, so callers can print it for the user (e.g. "full log: logs/run-...log")."""
    level = level or os.environ.get("LOG_LEVEL", "WARNING")

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    log_path = LOG_DIR / f"run-{timestamp}.log"

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    root.handlers.clear()

    console = logging.StreamHandler()
    console.setLevel(getattr(logging, level.upper(), logging.INFO))
    console.setFormatter(logging.Formatter("%(levelname)s %(message)s"))
    console.addFilter(_RedactingFilter())
    root.addHandler(console)

    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(
        logging.Formatter(
            "%(asctime)s %(levelname)-8s %(name)s:%(lineno)d %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S%z",
        )
    )
    file_handler.addFilter(_RedactingFilter())
    root.addHandler(file_handler)

    return log_path
