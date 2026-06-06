"""Embedded storage must not flood a host agent's DEBUG logs.

``voicegateway.attach()`` runs StorageService in-process inside a LiveKit
agent. The aiosqlite driver and alembic both log every operation at DEBUG,
which a LiveKit ``console``/``dev`` run (root logger at DEBUG) would surface
as a wall of per-query noise. StorageService quiets those two noisy
dependency loggers, but only when the caller has not set a level of their
own.
"""

from __future__ import annotations

import logging

from voicegateway.services.storage_service import StorageService


def test_embedded_storage_quiets_unconfigured_aiosqlite_logger(tmp_path):
    lg = logging.getLogger("aiosqlite")
    lg.setLevel(logging.NOTSET)  # default: inherits root (DEBUG under console mode)
    try:
        StorageService(tmp_path / "x.db")
        assert lg.level == logging.WARNING
    finally:
        lg.setLevel(logging.NOTSET)


def test_embedded_storage_quiets_unconfigured_alembic_logger(tmp_path):
    lg = logging.getLogger("alembic")
    lg.setLevel(logging.NOTSET)
    try:
        StorageService(tmp_path / "y.db")
        assert lg.level == logging.WARNING
    finally:
        lg.setLevel(logging.NOTSET)


def test_embedded_storage_respects_an_explicit_aiosqlite_level(tmp_path):
    """A developer who deliberately turns aiosqlite up keeps their setting."""
    lg = logging.getLogger("aiosqlite")
    lg.setLevel(logging.DEBUG)
    try:
        StorageService(tmp_path / "z.db")
        assert lg.level == logging.DEBUG
    finally:
        lg.setLevel(logging.NOTSET)
