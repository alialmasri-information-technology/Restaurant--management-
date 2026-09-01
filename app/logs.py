"""Rotating application log.

Anything the user is shown as an error is also written here with a traceback, so
a problem reported days later ("it crashed on Tuesday") can still be diagnosed.
"""

from __future__ import annotations

import logging
import logging.handlers
import sys

from app import config

LOG_FILE = "re4.log"
MAX_BYTES = 1_000_000
BACKUP_COUNT = 5

_configured = False


def setup(level: int = logging.INFO) -> logging.Logger:
    """Configure the root logger once; safe to call repeatedly."""
    global _configured
    logger = logging.getLogger(config.APP_NAME)
    if _configured:
        return logger

    config.ensure_directories()
    logger.setLevel(level)
    logger.propagate = False

    file_handler = logging.handlers.RotatingFileHandler(
        config.LOGS_DIR / LOG_FILE,
        maxBytes=MAX_BYTES,
        backupCount=BACKUP_COUNT,
        encoding="utf-8",
    )
    file_handler.setFormatter(
        logging.Formatter("%(asctime)s  %(levelname)-8s  %(name)s  %(message)s")
    )
    logger.addHandler(file_handler)

    # A console copy helps when running from a terminal; harmless when frozen
    # with console=False because stderr is then a null device.
    if sys.stderr is not None:
        stream = logging.StreamHandler(sys.stderr)
        stream.setLevel(logging.WARNING)
        stream.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))
        logger.addHandler(stream)

    _configured = True
    logger.info("%s %s starting — data in %s", config.APP_NAME, config.APP_VERSION, config.DATA_DIR)
    return logger


def get(name: str = "") -> logging.Logger:
    base = logging.getLogger(config.APP_NAME)
    return base.getChild(name) if name else base


def log_path():
    return config.LOGS_DIR / LOG_FILE


def exception(message: str, *args) -> None:
    get().exception(message, *args)


def info(message: str, *args) -> None:
    get().info(message, *args)


def warning(message: str, *args) -> None:
    get().warning(message, *args)
