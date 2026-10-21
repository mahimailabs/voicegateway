"""Field validation: invalid keys and values are dropped and counted, never raised."""

from __future__ import annotations

from typing import Any

import pytest

from voicegateway import Attachment
from voicegateway._delivery import Delivery

CALL: dict[str, str | None] = {"jobId": "AJ_1", "roomName": None, "roomSid": None}


def attachment() -> tuple[Attachment, Delivery]:
    d = Delivery(url="http://127.0.0.1:9", token=lambda: "t", call=CALL, framework_version="x")
    return Attachment(d), d


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("Has Space", "v"),
        ("UPPER", "v"),
        ("", "v"),
        ("k" * 65, "v"),
        ("ok", "v" * 257),
        ("ok", 3),
        (7, "v"),
    ],
)
def test_invalid_field_is_dropped_and_counted(key: Any, value: Any) -> None:
    a, d = attachment()
    a.set_field(key, value)
    assert a.dropped_fields == 1
    assert list(d._queue) == []


def test_valid_fields_are_sent_in_chunks_of_32() -> None:
    a, d = attachment()
    a._send_fields({f"k{i}": "v" * 256 for i in range(40)} | {"a.b-c_d": ""})
    sizes = [len(e["data"]["fields"]) for e in d._queue]
    assert sizes == [32, 9]
    assert a.dropped_fields == 0


def test_no_op_attachment_never_raises() -> None:
    a = Attachment()
    a.set_field("Bad Key", "x")
    assert not a.enabled
    assert a.dropped == 0
