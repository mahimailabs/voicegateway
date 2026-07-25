"""transcript_turns must age out with its session, like turns/dead_air."""

from __future__ import annotations

from voicegateway.services.retention_service import _SESSION_CHILD_TABLES


def test_transcript_turns_is_a_session_child_table():
    assert "transcript_turns" in _SESSION_CHILD_TABLES
