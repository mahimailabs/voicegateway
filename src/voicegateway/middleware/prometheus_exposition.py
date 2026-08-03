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


#: The suffix Prometheus gives a histogram's cumulative bucket series.
BUCKET_SUFFIX = "_bucket"

#: Suffixes that only ever exist on a histogram or summary, never alone. A map
#: entry naming the BASE of one of these matches no sample at all.
_AGGREGATE_SUFFIXES = ("_bucket", "_sum", "_count")


class HistogramMisuse(ValueError):
    """A series was requested in a way that cannot produce a meaningful number.

    Raised rather than returning None, because both cases this covers are
    programming errors in the metric map, not absences in the data, and a None
    would be indistinguishable from "this deployment does not export it".
    """


def histogram_bases(samples: Iterable[Sample]) -> set[str]:
    """Names that exist ONLY as ``_bucket``/``_sum``/``_count``.

    A map entry naming one of these matches nothing and stores NULL for the
    life of the deployment, while ``series_found`` still reads high because the
    other entries matched. That is invisible without this check.
    """
    present = {sample.name for sample in samples}
    bases: set[str] = set()
    for name in present:
        for suffix in _AGGREGATE_SUFFIXES:
            if name.endswith(suffix):
                base = name[: -len(suffix)]
                if base and base not in present:
                    bases.add(base)
    return bases


def sum_series(
    samples: Iterable[Sample],
    name: str,
    *,
    where: Mapping[str, str] | None = None,
    exclude: Mapping[str, str] | None = None,
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

    ``exclude`` is the complement, and it exists for the one shape ``where``
    cannot express: sum EVERY label value except a named one. A sample is
    skipped when it matches ALL of the pairs given, so ``{"device": "lo"}``
    drops loopback and keeps whatever the real NIC happens to be called
    (``enp39s0`` on one node, ``ens5`` on another). Pinning the device instead
    would empty the column on every host that spells it differently, which is
    the failure this whole module is written to avoid; ``lo`` is stable on every
    Linux host, so excluding it is safe where pinning is not. An exclusion that
    matches nothing changes nothing, and excluding every sample of a series
    leaves the result None rather than 0.0, exactly as an absent series does.

    Summing across BUCKETS is a different operation and is refused. Prometheus
    buckets are cumulative, so ``le=0.1`` is contained in ``le=0.5`` and so on
    up to ``+Inf``: adding them counts the same observation once per bucket. On
    a real capture this returns 11.0 for a histogram whose true ``_count`` is 1,
    and at load that charts as a smooth believable curve nobody questions. A
    bucket therefore requires an explicit ``le``, which selects ONE bucket and
    makes the result "observations at or under this bound".
    """
    if name.endswith(BUCKET_SUFFIX) and not (where and "le" in where):
        raise HistogramMisuse(
            f"{name!r} is a cumulative histogram bucket and summing across "
            "buckets counts each observation once per bucket. Pass an explicit "
            'le selector, e.g. where={"le": "1"}, to select a single bucket.'
        )
    total: float | None = None
    for sample in samples:
        if sample.name != name:
            continue
        if where and any(sample.labels.get(k) != v for k, v in where.items()):
            continue
        # ALL pairs must match to drop a sample, so an exclusion narrows what it
        # removes as it gains keys. `and exclude` rather than a bare all(): an
        # empty mapping matches vacuously, and reading that as "exclude
        # everything" would turn a caller's `exclude={}` into a NULL column.
        if exclude and all(sample.labels.get(k) == v for k, v in exclude.items()):
            continue
        total = sample.value if total is None else total + sample.value
    return total


__all__ = [
    "BUCKET_SUFFIX",
    "HistogramMisuse",
    "Sample",
    "histogram_bases",
    "parse_exposition",
    "sum_series",
]
