"""Building a Database must quiet aiosqlite/alembic, on every construction path.

Regression: the local-mode fleet heartbeat thread constructs a ``Database``
directly (not via the ``StorageService`` facade) and writes presence every 15s.
Under a LiveKit ``dev``/``console`` run (root logger at DEBUG) that flooded the
agent's terminal with per-query aiosqlite chatter, because only ``StorageService``
quieted the noisy loggers. The quieting now lives in ``Database.__init__`` so the
heartbeat path (and any other direct engine build) is covered too.
"""

from __future__ import annotations

import logging

import pytest

from voicegateway.core.config import GatewayConfig
from voicegateway.core.database import Database


def _config(tmp_path) -> GatewayConfig:
    return GatewayConfig(cost_tracking={"db_path": str(tmp_path / "hb.db")})


@pytest.mark.parametrize("name", ["aiosqlite", "alembic"])
def test_database_quiets_unconfigured_logger(tmp_path, name):
    lg = logging.getLogger(name)
    lg.setLevel(logging.NOTSET)  # inherits root (DEBUG under console mode)
    try:
        Database(_config(tmp_path))
        assert lg.level == logging.WARNING
    finally:
        lg.setLevel(logging.NOTSET)


def test_database_respects_an_explicit_aiosqlite_level(tmp_path):
    """A developer who deliberately turns aiosqlite up keeps their setting."""
    lg = logging.getLogger("aiosqlite")
    lg.setLevel(logging.DEBUG)
    try:
        Database(_config(tmp_path))
        assert lg.level == logging.DEBUG
    finally:
        lg.setLevel(logging.NOTSET)
