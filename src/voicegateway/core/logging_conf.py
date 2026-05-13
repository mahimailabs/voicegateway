"""Loguru configuration for the HTTP server stack.

Plain stdlib logging keeps working for the CLI / LiveKit / providers.
This module is only imported by the server lifespan to wire structured
output and pipe stdlib log records into loguru for one consistent sink.
"""

from __future__ import annotations

import logging
import sys

from loguru import logger


class _InterceptHandler(logging.Handler):
    """Forward stdlib log records into loguru, preserving level + caller."""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            level: str | int = logger.level(record.levelname).name
        except ValueError:
            level = record.levelno

        frame, depth = logging.currentframe(), 2
        while frame and frame.f_code.co_filename == logging.__file__:
            frame = frame.f_back
            depth += 1

        logger.opt(depth=depth, exception=record.exc_info).log(
            level, record.getMessage()
        )


def configure_logging(level: str = "INFO") -> None:
    """Replace loguru's default sink and pipe stdlib log records in.

    Idempotent. Safe to call multiple times across reloads / tests.
    """
    logger.remove()
    logger.add(
        sys.stderr,
        level=level,
        format=(
            "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | "
            "<level>{level: <8}</level> | "
            "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - "
            "<level>{message}</level>"
        ),
    )
    logging.basicConfig(handlers=[_InterceptHandler()], level=0, force=True)
