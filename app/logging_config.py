"""
Central logging setup. Imported once, at the top of app/main.py, before
anything else logs — every other module just does
`logger = logging.getLogger(__name__)` and inherits this configuration
(Python's logging is a single global hierarchy, so one setup call here is
enough for the whole app).

Two handlers, same records, different jobs:
  - console: human-readable, for watching a live `uvicorn --reload` session.
  - rotating file (logs/app.log): same content, kept on disk so you can go
    back and see exactly what SQL an agent ran / how it answered after the
    fact — this is the "full logs on the backend" ask: every request,
    every query, every agent's outcome, not just uvicorn's one-line access
    log.

Not using this for tracing the *agent's own reasoning* — that's what the
OpenAI Agents SDK's built-in tracing (platform.openai.com/traces) is for,
see AGENT_PLAN.md section 3. This logging is the ops-facing view: request
in, SQL run, response out, with timing, independent of whether tracing is
enabled.
"""

import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path

LOG_DIR = Path(__file__).resolve().parent.parent / "logs"
LOG_FILE = LOG_DIR / "app.log"

LOG_FORMAT = "%(asctime)s %(levelname)-8s %(name)s: %(message)s"


def setup_logging() -> None:
    LOG_DIR.mkdir(exist_ok=True)
    level = os.getenv("LOG_LEVEL", "INFO").upper()
    formatter = logging.Formatter(LOG_FORMAT)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)

    # 5MB per file, keep 3 old ones — enough for a local learning project
    # without the log directory growing unbounded.
    file_handler = RotatingFileHandler(
        LOG_FILE, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"
    )
    file_handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.setLevel(level)
    root_logger.handlers = [console_handler, file_handler]

    # These libraries log at INFO/DEBUG very verbosely (every HTTP
    # connection, every retry) — cap them at WARNING so app-level logs
    # aren't drowned out. Bump to the app's level explicitly if you're
    # debugging an OpenAI API or DB connection issue.
    for noisy_logger in ("httpx", "httpcore", "openai", "urllib3"):
        logging.getLogger(noisy_logger).setLevel(logging.WARNING)
