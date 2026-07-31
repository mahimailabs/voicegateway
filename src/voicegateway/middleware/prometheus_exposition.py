"""Minimal parser for the Prometheus text exposition format (version 0.0.4).

Pure text in, samples out: no I/O, no clock, no state. It exists so the scrape
worker can be tested against a canned exposition body rather than a live
endpoint.

**Why not ``prometheus_client.parser``?** It is installed here only as a
transitive dependency and is not declared in ``pyproject.toml``, so importing it
would make an ingest path depend on a package that a resolver is free to drop.
The subset needed here (samples and their labels; no metric families, no
timestamps, no OpenMetrics exemplars) is small enough to own.

Tolerant by design in one direction only: an unparseable LINE is skipped, so one
malformed series cannot cost an operator the whole scrape. An unparseable
DOCUMENT yields no samples at all, which the worker records as ``unparseable``
rather than as a node with no traffic.
"""

from __future__ import annotations

import logging
import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# Exposition escapes, as defined by the text format: a label value may contain
# \\, \" and \n, and nothing else is special.
_ESCAPES = {"\\": "\\", '"': '"', "n": "\n"}


@dataclass(frozen=True)
class Sample:
    """One ``name{labels} value`` line."""

    name: str
    labels: Mapping[str, str]
    value: float


def _closing_brace(line: str, start: int) -> int:
    """Index of the ``}`` closing the block opened at ``start``, or -1.

    Scans rather than using ``str.find``/a regex because a label VALUE may
    legally contain ``}`` (``reason="unexpected }"``), and taking the first one
    would truncate the labels and mis-attribute the value.
    """
    in_quotes = False
    escaped = False
    for index in range(start + 1, len(line)):
        char = line[index]
        if escaped:
            escaped = False
            continue
        if char == "\\" and in_quotes:
            escaped = True
        elif char == '"':
            in_quotes = not in_quotes
        elif char == "}" and not in_quotes:
            return index
    return -1


def _unescape(raw: str) -> str:
    out: list[str] = []
    escaped = False
    for char in raw:
        if escaped:
            out.append(_ESCAPES.get(char, char))
            escaped = False
        elif char == "\\":
            escaped = True
        else:
            out.append(char)
    if escaped:
        # A trailing backslash: keep it rather than dropping a character.
        out.append("\\")
    return "".join(out)


def _parse_labels(block: str) -> dict[str, str] | None:
    """``name="value",...`` -> dict, or None when the block is malformed."""
    labels: dict[str, str] = {}
    index = 0
    length = len(block)
    while index < length:
        while index < length and block[index] in ", \t":
            index += 1
        if index >= length:
            break
        equals = block.find("=", index)
        if equals == -1:
            return None
        key = block[index:equals].strip()
        if not key:
            return None
        index = equals + 1
        while index < length and block[index] in " \t":
            index += 1
        if index >= length or block[index] != '"':
            return None
        index += 1
        value_chars: list[str] = []
        escaped = False
        closed = False
        while index < length:
            char = block[index]
            index += 1
            if escaped:
                value_chars.append(char)
                escaped = False
                continue
            if char == "\\":
                value_chars.append(char)
                escaped = True
                continue
            if char == '"':
                closed = True
                break
            value_chars.append(char)
        if not closed:
            return None
        labels[key] = _unescape("".join(value_chars))
    return labels


def _parse_value(rest: str) -> float | None:
    """The value token of a sample line, or None when it is not a measurement.

    ``NaN`` and ``+Inf`` are legal exposition values and are refused here: they
    are what an exporter emits for "no data" and for a divide-by-zero, and
    storing either as a number would put a fabricated point on a chart. An
    optional trailing exposition timestamp is ignored -- this product timestamps
    the scrape itself, so that every target in one tick shares an instant.
    """
    tokens = rest.split()
    if not tokens:
        return None
    try:
        value = float(tokens[0])
    except ValueError:
        return None
    return value if math.isfinite(value) else None


def parse_exposition(body: str) -> list[Sample]:
    """Every parseable sample line in ``body``, in document order.

    ``# HELP`` / ``# TYPE`` metadata is skipped: the metric TYPE is not trusted
    from the wire here, because which series are counters is a decision this
    product has already made and stored (``node_samples_repository``).
    """
    samples: list[Sample] = []
    for raw_line in body.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        brace = line.find("{")
        if brace == -1:
            parts = line.split(None, 1)
            if len(parts) != 2:
                continue
            name, rest = parts[0], parts[1]
            labels: dict[str, str] | None = {}
        else:
            name = line[:brace].strip()
            end = _closing_brace(line, brace)
            if end == -1 or not name:
                logger.debug("skipping unparseable exposition line: %r", raw_line)
                continue
            labels = _parse_labels(line[brace + 1 : end])
            rest = line[end + 1 :]
        if labels is None or not name:
            logger.debug("skipping unparseable exposition line: %r", raw_line)
            continue
        value = _parse_value(rest)
        if value is None:
            continue
        samples.append(Sample(name=name, labels=labels, value=value))
    return samples


def sum_series(
    samples: Iterable[Sample],
    name: str,
    *,
    where: Mapping[str, str] | None = None,
) -> float | None:
    """Sum every sample of ``name`` matching ``where``; None when there are none.

    None, not 0.0, is the whole point: "the series is not in this exposition" and
    "the series read zero" are different facts, and only the first one must
    leave the column NULL so a reader can suppress it instead of drawing a flat
    line at the bottom of the chart.

    Summing across label combinations is deliberate. ``livekit_packet_total`` is
    split by ``direction``/``transmission``/``country`` and
    ``node_cpu_seconds_total`` by ``cpu``/``mode``; a node-level series is the
    total, and filtering on a label VALUE would silently produce NULL forever if
    a release spelled it differently. ``where`` is used only where the value is
    part of the definition (``mode="idle"``), never to reconstruct a split this
    schema does not store.
    """
    total: float | None = None
    for sample in samples:
        if sample.name != name:
            continue
        if where and any(sample.labels.get(k) != v for k, v in where.items()):
            continue
        total = sample.value if total is None else total + sample.value
    return total


__all__ = [
    "Sample",
    "parse_exposition",
    "sum_series",
]
