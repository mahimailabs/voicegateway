"""Active per-agent latency probe: dispatch the agent to a throwaway room, call
it with a synthetic client, time the reply, and (when the agent is instrumented)
read the STT/LLM/TTS + turn-detection split from VoiceGateway telemetry.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from statistics import mean


@dataclass
class LatencyResult:
    agent: str
    e2e_samples: list[float] = field(default_factory=list)
    components: dict | None = None
    error: str | None = None


def aggregate_components(rows: list[dict]) -> dict | None:
    """Reduce the probe room's request rows to a turn-detect/STT/LLM/TTS split.

    ``rows`` are ``metadata.room``-tagged request records the instrumented agent
    wrote during the probe. Turn detection comes from the ``eou`` row's
    ``end_of_utterance_delay``; STT from the per-component ttfb (falling back to
    the eou row's ``transcription_delay``); LLM/TTS from their ttfb. Values are
    seconds, averaged across every row for the room (so a fixed ``--room-name``
    reused across turns averages them; the default per-trial rooms each hold one
    turn). Returns None when nothing usable is present (an uninstrumented agent,
    or rows without latencies).
    """
    eou: list[float] = []
    transcription: list[float] = []
    stt: list[float] = []
    llm: list[float] = []
    tts: list[float] = []
    for row in rows:
        metadata = row.get("metadata") or {}
        modality = row.get("modality")
        ttfb_ms = row.get("ttfb_ms")
        if modality == "eou":
            eou_meta = metadata.get("eou") or {}
            delay = eou_meta.get("end_of_utterance_delay")
            if delay is not None:
                eou.append(float(delay))
            td = eou_meta.get("transcription_delay")
            if td is not None:
                transcription.append(float(td))
        elif modality == "stt" and ttfb_ms is not None:
            stt.append(float(ttfb_ms) / 1000.0)
        elif modality == "llm" and ttfb_ms is not None:
            llm.append(float(ttfb_ms) / 1000.0)
        elif modality == "tts" and ttfb_ms is not None:
            tts.append(float(ttfb_ms) / 1000.0)

    out: dict[str, float] = {}
    if eou:
        out["eou"] = mean(eou)
    if stt:
        out["stt"] = mean(stt)
    elif transcription:
        out["stt"] = mean(transcription)
    if llm:
        out["llm_ttft"] = mean(llm)
    if tts:
        out["tts"] = mean(tts)
    return out or None


def _split_complete(components: dict) -> bool:
    """Whether a split has the always-present reply components (LLM + TTS).

    Every voice reply goes through the LLM then the TTS, so both should land for
    a finished turn. Turn detection (eou) and STT ttfb can legitimately be
    absent, so they do not gate completeness. Used to decide when a polled
    read-back has captured the whole turn versus a mid-flush fragment.
    """
    return "llm_ttft" in components and "tts" in components


class ComponentReader:
    """Reads the probe room's component + EOU rows from a VG store and reduces
    them to a latency split. Optional; returns None when no store is configured
    or the agent is not instrumented.

    Because the agent writes those rows from a separate process (and possibly
    still flushing when the probe finishes), ``read`` polls the store a bounded
    number of times. Defaults are a single no-wait read so unit tests and the
    uninstrumented path stay instant; the CLI opts into a short poll for the
    real cross-process case.
    """

    def __init__(
        self, store=None, *, poll_attempts: int = 1, poll_delay: float = 0.0
    ) -> None:
        self._store = store
        self._poll_attempts = max(1, poll_attempts)
        self._poll_delay = poll_delay

    async def read(self, room: str) -> dict | None:
        if self._store is None:
            return None
        latest: dict | None = None
        for attempt in range(self._poll_attempts):
            rows = await self._store.get_requests_for_room(room)
            if not rows:
                # A first empty read means no rows will ever correlate for this room
                # (uninstrumented agent, a remote agent writing elsewhere, or
                # collector mode): give up now rather than dead-wait the whole poll.
                if latest is None:
                    return None
            else:
                latest = aggregate_components(rows)
                # Return only once the split looks whole. Returning a partial split
                # mid cross-process flush would render missing components as 0.00
                # ("instant"), which is worse than waiting a beat for the rest.
                if latest is not None and _split_complete(latest):
                    return latest
            if attempt + 1 < self._poll_attempts and self._poll_delay > 0:
                await asyncio.sleep(self._poll_delay)
        return latest


class ProbeRunner:
    def __init__(
        self, admin, client_factory, utterance, reader: ComponentReader | None = None
    ) -> None:
        self._admin = admin
        self._url = getattr(admin, "url", "")
        self._make_client = client_factory
        self._utterance = utterance
        self._reader = reader or ComponentReader()

    async def probe(
        self,
        agent: str,
        trials: int,
        warmup: bool,
        room_name: str | None,
        metadata: str,
    ) -> LatencyResult:
        result = LatencyResult(agent=agent)
        total = trials + (1 if warmup else 0)
        last_room: str | None = None
        for i in range(total):
            room = room_name or f"vg-probe-{agent}-{uuid.uuid4().hex[:8]}"
            e2e = None
            try:
                await self._admin.create_room(room)
                await self._admin.create_dispatch(room, agent, metadata)
                client = self._make_client(
                    self._url, self._admin.join_token(room, "vg-probe")
                )
                await client.connect()
                try:
                    t0 = await client.publish_utterance(self._utterance)
                    e2e = await client.wait_reply(t0)
                finally:
                    await client.disconnect()
            except Exception as exc:  # noqa: BLE001 - isolate per-agent failures; caller loops on
                result.error = str(exc)
                break
            finally:
                await self._admin.delete_room(room)
            if i == 0 and warmup:
                continue  # discard cold start
            if e2e is not None:
                result.e2e_samples.append(e2e)
                last_room = room
        # Read the component split once, after the turns are done: the agent
        # writes those rows from its own process and may still be flushing, so
        # the reader polls. Correlate on the last successful room.
        if last_room is not None:
            result.components = await self._reader.read(last_room)
        return result


def _pct(xs: list[float], p: float) -> float:
    if not xs:
        return 0.0
    s = sorted(xs)
    k = min(len(s) - 1, int(round((p / 100) * (len(s) - 1))))
    return s[k]


def summarize(result: LatencyResult) -> dict:
    xs = result.e2e_samples
    return {
        "avg": mean(xs) if xs else 0.0,
        "p50": _pct(xs, 50),
        "p95": _pct(xs, 95),
        "min": min(xs) if xs else 0.0,
        "max": max(xs) if xs else 0.0,
        "trials": len(xs),
    }
