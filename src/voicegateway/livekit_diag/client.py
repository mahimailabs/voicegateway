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
- publish_data(payload, *, reliable=True) is async.
- capture_frame(frame) is async on AudioSource.
- AudioStream(track) yields AudioFrameEvent; .frame is AudioFrame; .data is memoryview.
- data_received callback receives DataPacket(data=bytes, kind, participant, topic).
- connection_quality_changed callback receives (quality, participant).
- track_subscribed callback receives (track, publication, participant).
- TrackKind.KIND_AUDIO confirmed present.
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


class SyntheticClient:
    def __init__(self, url: str, token: str) -> None:
        self._url = url
        self._token = token
        self._room = rtc.Room()
        self._detector = ReplyDetector()
        self._pong = asyncio.Event()
        self._quality = "Unknown"
        self._drain_tasks: set[asyncio.Task] = set()

    async def connect(self) -> None:
        self._room.on("data_received", self._on_data)
        self._room.on("connection_quality_changed", self._on_quality)
        self._room.on("track_subscribed", self._on_track)
        await self._room.connect(self._url, self._token)

    def _on_quality(self, quality: object, participant: object) -> None:
        # quality is a ConnectionQuality enum value; str() gives a readable label.
        self._quality = str(quality)

    def _on_data(self, data_packet: object) -> None:
        # data_packet is rtc.DataPacket; .data is bytes.
        if getattr(data_packet, "data", b"") == b"vg-ping":
            self._pong.set()

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
        await self._room.local_participant.publish_track(track)
        loop = asyncio.get_running_loop()
        for pcm, rate in src.frames():
            # AudioFrame(data, sample_rate, num_channels, samples_per_channel)
            frame = rtc.AudioFrame(pcm, rate, 1, len(pcm) // 2)
            await source.capture_frame(frame)
            await asyncio.sleep(0.01)
        return loop.time()  # t0 = end of playback

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
        except asyncio.TimeoutError:
            return None
        return loop.time() - start

    def quality(self) -> str:
        return self._quality

    async def disconnect(self) -> None:
        await self._room.disconnect()
