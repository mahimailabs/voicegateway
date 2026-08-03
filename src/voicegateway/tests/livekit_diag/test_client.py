import struct

from voicegateway.livekit_diag.client import ReplyDetector, UtteranceSource


def _pcm(amplitude: int, samples: int = 160) -> bytes:
    return struct.pack(f"<{samples}h", *([amplitude] * samples))


def test_reply_detector_ignores_silence_then_fires_on_speech():
    d = ReplyDetector(threshold=0.02, min_frames=2)
    d.feed(_pcm(0), t=0.0)  # silence
    d.feed(_pcm(50), t=0.1)  # tiny blip, below min_frames
    assert d.first_reply_at is None
    d.feed(_pcm(8000), t=0.2)  # loud
    d.feed(_pcm(8000), t=0.3)  # loud, 2 in a row -> fires at first loud
    assert d.first_reply_at == 0.2


def test_utterance_source_reads_bundled_wav():
    from pathlib import Path

    import voicegateway.livekit_diag as pkg

    wav = Path(pkg.__file__).parent / "assets" / "probe.wav"
    src = UtteranceSource(str(wav))
    chunks = list(src.frames())
    assert chunks and all(isinstance(c[0], bytes) and c[1] > 0 for c in chunks)
    assert src.duration_s > 0.3
