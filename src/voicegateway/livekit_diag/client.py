"""A synthetic LiveKit participant: publish a probe utterance, subscribe to a
remote audio track, time the first reply, ping over the data channel, and read
connection quality. Reused by the latency probe (call an agent) and the sfu
probe (talk to other synthetic clients).

The pure timing/audio helpers (ReplyDetector, UtteranceSource) are separated so
they unit-test without a live server; the WebRTC glue is behind SyntheticClient.

SDK surface verified against livekit>=1.0 (installed version):
- AudioFrame(data, sample_rate, num_channels, samples_per_channel) positional.
- AudioSource(sample_rate, num_channels) positional.
- LocalAudioTrack.create_audio_track(name, source) classmethod.
- publish_track(track, options=TrackPublishOptions()) is async.
- TrackPublishOptions(source=TrackSource.SOURCE_MICROPHONE): required so an agent's
  RoomIO (accepted_sources=[SOURCE_MICROPHONE]) routes the probe audio to STT.
- publish_data(payload, *, reliable=True) is async.
- capture_frame(frame) is async on AudioSource.
- AudioStream(track) yields AudioFrameEvent; .frame is AudioFrame; .data is memoryview.
- data_received callback receives DataPacket(data=bytes, kind, participant, topic).
- connection_quality_changed callback receives (participant, quality).
- track_subscribed callback receives (track, publication, participant).
- TrackKind.KIND_AUDIO confirmed present.
- ConnectionQuality enum members: QUALITY_EXCELLENT, QUALITY_GOOD, QUALITY_POOR, QUALITY_LOST.
"""

from __future__ import annotations

import array
import asyncio
import wave
from collections.abc import Iterator

from livekit import rtc


class ReplyDetector:
    """Marks the timestamp of the first sustained speech in a PCM stream."""

    def __init__(self, threshold: float = 0.02, min_frames: int = 3) -> None:
        self._threshold = threshold
        self._min = min_frames
        self._run = 0
        self._candidate: float = 0.0
        self.first_reply_at: float | None = None

    def feed(self, pcm: bytes, t: float) -> None:
        if self.first_reply_at is not None or not pcm:
            return
        samples = array.array("h")
        samples.frombytes(pcm)
        peak = max((abs(s) for s in samples), default=0) / 32768.0
        if peak >= self._threshold:
            self._run += 1
            if self._run == 1:
                self._candidate = t
            if self._run >= self._min:
                self.first_reply_at = self._candidate
        else:
            self._run = 0


class UtteranceSource:
    """Reads a mono 16-bit PCM WAV into fixed 10ms chunks for publishing."""

    def __init__(self, path: str) -> None:
        with wave.open(path, "rb") as w:
            self._rate = w.getframerate()
            self._data = w.readframes(w.getnframes())
        self.duration_s = len(self._data) / 2 / self._rate

    def frames(self) -> Iterator[tuple[bytes, int]]:
        chunk = int(self._rate * 0.01) * 2  # 10ms, 2 bytes/sample
        for i in range(0, len(self._data), chunk):
            yield self._data[i : i + chunk], self._rate


def _quality_label(q: object) -> str:
    from livekit import rtc as _rtc

    labels: dict[object, str] = {
        _rtc.ConnectionQuality.QUALITY_EXCELLENT: "Excellent",
        _rtc.ConnectionQuality.QUALITY_GOOD: "Good",
        _rtc.ConnectionQuality.QUALITY_POOR: "Poor",
        _rtc.ConnectionQuality.QUALITY_LOST: "Lost",
    }
    return labels.get(q, "Unknown")


class SyntheticClient:
    def __init__(self, url: str, token: str) -> None:
        self._url = url
        self._token = token
        self._room = rtc.Room()
        self._detector = ReplyDetector()
        self._pong = asyncio.Event()
        self._quality = "Unknown"
        self._drain_tasks: set[asyncio.Task] = set()
        self._data_tasks: set[asyncio.Task] = set()
        self._silence_task: asyncio.Task | None = None

    async def connect(self) -> None:
        self._room.on("data_received", self._on_data)
        self._room.on("connection_quality_changed", self._on_quality)
        self._room.on("track_subscribed", self._on_track)
        await self._room.connect(self._url, self._token)

    def _on_quality(self, participant: object, quality: object) -> None:
        # SDK emits (participant, quality); quality is a ConnectionQuality enum value.
        self._quality = _quality_label(quality)

    def _on_data(self, data: object) -> None:
        payload = getattr(data, "data", b"")
        if payload == b"vg-ping":
            t = asyncio.ensure_future(self._send_pong())
            self._data_tasks.add(t)
            t.add_done_callback(self._data_tasks.discard)
        elif payload == b"vg-pong":
            self._pong.set()

    async def _send_pong(self) -> None:
        try:
            await self._room.local_participant.publish_data(b"vg-pong", reliable=True)
        except Exception:  # noqa: BLE001 - best-effort echo
            pass

    def _on_track(
        self, track: object, publication: object, participant: object
    ) -> None:
        if getattr(track, "kind", None) == rtc.TrackKind.KIND_AUDIO:
            t = asyncio.ensure_future(self._drain(track))  # type: ignore[arg-type]
            self._drain_tasks.add(t)
            t.add_done_callback(self._drain_tasks.discard)

    async def _drain(self, track: rtc.Track) -> None:
        loop = asyncio.get_running_loop()
        stream = rtc.AudioStream(track)
        async for event in stream:
            # event is AudioFrameEvent; event.frame.data is memoryview.
            self._detector.feed(bytes(event.frame.data), loop.time())

    async def publish_utterance(self, src: UtteranceSource) -> float:
        source = rtc.AudioSource(src._rate, 1)
        track = rtc.LocalAudioTrack.create_audio_track("probe", source)
        # Publish as SOURCE_MICROPHONE, not the default SOURCE_UNKNOWN. A LiveKit
        # AgentSession's RoomIO only routes microphone-sourced tracks to STT
        # (its accepted_sources is [SOURCE_MICROPHONE]); an unsourced track is
        # ignored, so the agent hears nothing, never transcribes, and never
        # replies. This is what a probe needs the agent to actually process.
        await self._room.local_participant.publish_track(
            track,
            rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_MICROPHONE),
        )
        loop = asyncio.get_running_loop()
        for pcm, rate in src.frames():
            # AudioFrame(data, sample_rate, num_channels, samples_per_channel)
            frame = rtc.AudioFrame(pcm, rate, 1, len(pcm) // 2)
            await source.capture_frame(frame)
            await asyncio.sleep(0.01)
        t0 = loop.time()  # t0 = end of speech
        # Keep the mic open with trailing silence so the agent's VAD / turn
        # detector sees speech -> silence and fires end-of-turn: a track that just
        # stops emitting never triggers a response, so STT transcribes but the LLM
        # and TTS never run. Runs in the background so wait_reply can time a reply
        # that arrives DURING the silence; t0 stays at end-of-speech so the silence
        # does not inflate the measured latency.
        self._silence_task = asyncio.ensure_future(
            self._emit_silence(source, src._rate)
        )
        return t0

    async def _emit_silence(
        self, source: rtc.AudioSource, rate: int, seconds: float = 12.0
    ) -> None:
        chunk = int(rate * 0.01)  # 10ms of samples
        silence = bytes(chunk * 2)  # 16-bit zeros
        try:
            for _ in range(int(seconds / 0.01)):
                await source.capture_frame(rtc.AudioFrame(silence, rate, 1, chunk))
                await asyncio.sleep(0.01)
        except (asyncio.CancelledError, Exception):  # noqa: BLE001 - best-effort tail
            return

    async def wait_reply(self, t0: float, timeout: float = 15.0) -> float | None:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while loop.time() < deadline:
            if self._detector.first_reply_at is not None:
                return self._detector.first_reply_at - t0
            await asyncio.sleep(0.02)
        return None

    async def ping(self, timeout: float = 2.0) -> float | None:
        loop = asyncio.get_running_loop()
        self._pong.clear()
        start = loop.time()
        # publish_data(payload, *, reliable=True, destination_identities=[], topic='')
        await self._room.local_participant.publish_data(b"vg-ping", reliable=True)
        try:
            await asyncio.wait_for(self._pong.wait(), timeout)
        except TimeoutError:
            return None
        return loop.time() - start

    def quality(self) -> str:
        return self._quality

    async def disconnect(self) -> None:
        if self._silence_task is not None:
            self._silence_task.cancel()
        await self._room.disconnect()
