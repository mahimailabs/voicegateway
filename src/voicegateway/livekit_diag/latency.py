"""Active per-agent latency probe: dispatch the agent to a throwaway room, call
it with a synthetic client, time the reply, and (when the agent is instrumented)
read the STT/LLM/TTS + turn-detection split from VoiceGateway telemetry.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from statistics import mean

# Identity the synthetic caller joins the probe room under. Anything else in the
# room is the agent we dispatched, which is how the probe confirms a worker
# actually answered.
_CALLER_IDENTITY = "vg-probe"
# How long to wait for a dispatched worker to join before calling the room empty.
# A worker on LiveKit Cloud is dispatched in well under a second; this is slack
# for a cold self-hosted worker, not a value the happy path ever spends (the
# poll returns the instant the agent appears).
_AGENT_JOIN_TIMEOUT = 8.0
_AGENT_POLL_INTERVAL = 0.25
# After the agent joins, its session spends ~1-2s bringing up STT before it can
# hear anything. Speak a moment later so the utterance lands on a listening STT
# instead of a still-initializing one (a one-shot utterance played into a
# not-yet-ready pipeline is never transcribed). It sits before t0, so it does not
# inflate the measured reply latency.
_AGENT_SETTLE_SECONDS = 2.0
# The reply is detected on its FIRST audio, so wait_reply returns while the agent
# is still speaking. Tearing the room down then cuts the agent off before TTS
# finishes and flushes its metric, so the split loses its TTS leg even though the
# call replied. Hold on briefly after the reply to let the agent finish and write
# that row. It sits after t0, so it does not inflate the measured reply latency.
_REPLY_GRACE_SECONDS = 4.0


@dataclass
class LatencyResult:
    agent: str
    e2e_samples: list[float] = field(default_factory=list)
    components: dict | None = None
    error: str | None = None
    # The room the counted (non-warmup) turns ran in, so a caller can correlate
    # the probe's own cost rows back to it. None when no turn completed.
    room: str | None = None


def aggregate_components(rows: list[dict]) -> dict | None:
    """Reduce the probe room's request rows to a turn-detect/STT/LLM/TTS split.

    ``rows`` are ``metadata.room``-tagged request records the instrumented agent
    wrote during the probe. Turn detection comes from the ``eou`` row's
    ``end_of_utterance_delay``. STT responsiveness is the turn's
    time-to-first-partial (the ``eou`` row's ``time_to_first_partial_ms``: speech
    onset -> first interim, the head / first-byte metric). It is preferred, as
    the STT headline, over the ``transcription_delay`` tail (end-of-speech ->
    final, which folds in the provider's endpointing) and over a per-call STT
    ``ttfb`` if a provider ever emits one. LLM/TTS come from their ttfb. Values
    are seconds, averaged across every row for the room (so a fixed
    ``--room-name`` reused across turns averages them; the default per-trial
    rooms each hold one turn). ``stt`` is the headline number; ``stt_ttfp`` (head)
    and ``stt_transcription_delay`` (tail) are also surfaced when present so a
    caller can show both ends of the utterance. Returns None when nothing usable
    is present (an uninstrumented agent, or rows without latencies).
    """
    eou: list[float] = []
    transcription: list[float] = []
    first_partial: list[float] = []
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
            ttfp_ms = eou_meta.get("time_to_first_partial_ms")
            if ttfp_ms is not None:
                first_partial.append(float(ttfp_ms) / 1000.0)
        elif modality == "stt" and ttfb_ms is not None:
            stt.append(float(ttfb_ms) / 1000.0)
        elif modality == "llm" and ttfb_ms is not None:
            llm.append(float(ttfb_ms) / 1000.0)
        elif modality == "tts" and ttfb_ms is not None:
            tts.append(float(ttfb_ms) / 1000.0)

    out: dict[str, float] = {}
    if eou:
        out["eou"] = mean(eou)
    # STT headline: prefer a real first-response (a per-call ttfb, else the
    # turn-level time-to-first-partial) over the endpointing-laden
    # transcription_delay tail. Expose the head and tail separately too.
    if stt:
        out["stt"] = mean(stt)
    elif first_partial:
        out["stt"] = mean(first_partial)
    elif transcription:
        out["stt"] = mean(transcription)
    if first_partial:
        out["stt_ttfp"] = mean(first_partial)
    if transcription:
        out["stt_transcription_delay"] = mean(transcription)
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

    async def _await_agent(self, room: str, agent: str, *, explicit: bool) -> None:
        """Block until a worker joins ``room``, or raise a named failure.

        A dispatch to a name no running worker registered, and automatic dispatch
        with no worker online, both look identical downstream: the caller speaks
        into an empty room and ``wait_reply`` simply times out, producing an
        all-null sample with no error. That reads as "measured nothing" when the
        truth is "nobody answered". Polling the participant list turns that silent
        empty into a reason the operator can act on.

        Fails OPEN on an inconclusive check. If every participant read itself
        errored (a transient control-plane hiccup, or an ``admin`` with no
        ``room_participant_identities``) we never learned the room was empty, so
        we let the call proceed and speak for itself rather than block a probe
        that may well be fine. It fails CLOSED only on a clean read that shows no
        one but the caller in the room by the deadline.
        """
        read = getattr(self._admin, "room_participant_identities", None)
        if not callable(read):
            return  # cannot verify presence on this admin; do not block the probe
        loop = asyncio.get_running_loop()
        deadline = loop.time() + _AGENT_JOIN_TIMEOUT
        saw_empty = False
        # Read, THEN decide whether to break on the deadline, so the last thing
        # before giving up is a fresh read: a worker that joins just before the
        # deadline is still seen, rather than missed because the final decision
        # rode on a snapshot taken a poll-interval earlier.
        while True:
            try:
                idents = await read(room)
                if any(i and i != _CALLER_IDENTITY for i in idents):
                    return  # a worker joined
                saw_empty = True
            except Exception:  # noqa: BLE001 - an unreadable roster is inconclusive
                pass
            if loop.time() >= deadline:
                break
            await asyncio.sleep(_AGENT_POLL_INTERVAL)
        if not saw_empty:
            return  # never got a clean read; do not fail on an inconclusive check
        secs = int(_AGENT_JOIN_TIMEOUT)
        if explicit:
            raise RuntimeError(
                f"dispatched to {agent!r} but no worker joined within {secs}s: "
                "that name is how the worker registered (register_worker / "
                "@server.rtc_session agent_name); check a worker with that name "
                "is running"
            )
        raise RuntimeError(
            f"created a probe room but no automatic-dispatch worker joined "
            f"within {secs}s: check a worker for this agent is running"
        )

    async def probe(
        self,
        agent: str,
        trials: int,
        warmup: bool,
        room_name: str | None,
        metadata: str,
        *,
        dispatch: bool = True,
    ) -> LatencyResult:
        """Place ``trials`` real calls to ``agent`` and time the reply.

        ``dispatch=False`` creates the room but issues no explicit dispatch. That
        is the path for a worker registered WITHOUT an agent_name: LiveKit puts
        such a worker on automatic dispatch, so it joins every new room on its
        own and there is no name to dispatch to. Explicit dispatch remains the
        default, since it is the only way to target one named agent.
        """
        result = LatencyResult(agent=agent)
        total = trials + (1 if warmup else 0)
        last_room: str | None = None
        for i in range(total):
            room = room_name or f"vg-probe-{agent}-{uuid.uuid4().hex[:8]}"
            if i == 0 and warmup and room_name:
                # The warmup turn is discarded from the timings, so it must not
                # land in the room the caller reads back: cost is SUMMED and the
                # component split AVERAGED over every row tagged with that room,
                # so sharing one room folds a cold start the e2e number
                # deliberately threw away into both. Per-trial rooms (room_name
                # is None) isolate it already; a caller-fixed room needs this.
                # The vg-probe- prefix survives, so the warmup's own rows stay
                # out of the agent's rollups just the same.
                room = f"{room_name}-warmup"
            e2e = None
            try:
                await self._admin.create_room(room)
                if dispatch:
                    await self._admin.create_dispatch(room, agent, metadata)
                client = self._make_client(
                    self._url, self._admin.join_token(room, _CALLER_IDENTITY)
                )
                await client.connect()
                try:
                    await self._await_agent(room, agent, explicit=dispatch)
                    # Let the agent's STT finish coming up before speaking, so a
                    # one-shot utterance is not lost into a still-initializing
                    # pipeline. Skipped when the room read is unavailable only in
                    # the sense that _await_agent already returned; the wait is
                    # unconditional because readiness is not otherwise observable.
                    await asyncio.sleep(_AGENT_SETTLE_SECONDS)
                    t0 = await client.publish_utterance(self._utterance)
                    e2e = await client.wait_reply(t0)
                    if e2e is not None:
                        # Let the agent finish speaking so its TTS metric is
                        # written before the room is torn down below.
                        await asyncio.sleep(_REPLY_GRACE_SECONDS)
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
            result.room = last_room
            try:
                result.components = await self._reader.read(last_room)
            except Exception:  # noqa: BLE001 - the call itself already succeeded
                # The read-back is a local convenience, not the measurement. A
                # store that errors (locked file, collector mode, a schema this
                # host has not migrated) costs the component split, which is
                # reported as None, and nothing else: throwing here would
                # discard an end-to-end time that was genuinely measured.
                result.components = None
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
