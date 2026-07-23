from voicegateway.livekit_diag.latency import (
    ComponentReader,
    LatencyResult,
    ProbeRunner,
    aggregate_components,
    summarize,
)
from voicegateway.livekit_diag.report import render_latency


class _FakeAdmin:
    def __init__(self):
        self.created, self.deleted, self.dispatched = [], [], []

    async def create_room(self, n):
        self.created.append(n)

    async def delete_room(self, n):
        self.deleted.append(n)

    async def create_dispatch(self, r, a, metadata=""):
        self.dispatched.append((r, a))

    def join_token(self, r, i):
        return "tok"

    async def aclose(self):
        pass


class _FakeClient:
    seq = iter([1.40, 1.44, 1.42, 1.41])  # warmup + 3

    def __init__(self, url, token):
        pass

    async def connect(self):
        pass

    async def publish_utterance(self, src):
        return 0.0

    async def wait_reply(self, t0, timeout=15.0):
        return next(_FakeClient.seq)

    def quality(self):
        return "Excellent"

    async def disconnect(self):
        pass


async def test_probe_discards_warmup_and_aggregates():
    runner = ProbeRunner(_FakeAdmin(), _FakeClient, utterance=_StubUtterance())
    result = await runner.probe(
        "realty", trials=3, warmup=True, room_name=None, metadata=""
    )
    assert len(result.e2e_samples) == 3  # warmup discarded
    stats = summarize(result)
    assert round(stats["avg"], 2) == 1.42


class _StubUtterance:
    duration_s = 0.8

    def frames(self):
        return iter([(b"\x00\x00", 16000)])


# --- component breakdown read-back (Phase 2b) ----------------------------


def _split_rows(room="vg-probe-x"):
    return [
        {
            "modality": "eou",
            "ttfb_ms": None,
            "metadata": {
                "room": room,
                "eou": {"end_of_utterance_delay": 0.30, "transcription_delay": 0.08},
            },
        },
        {"modality": "stt", "ttfb_ms": 120.0, "metadata": {"room": room}},
        {"modality": "llm", "ttfb_ms": 450.0, "metadata": {"room": room}},
        {"modality": "tts", "ttfb_ms": 90.0, "metadata": {"room": room}},
    ]


def test_aggregate_components_full_split():
    out = aggregate_components(_split_rows())
    # a per-call stt ttfb wins the headline; the tail is surfaced alongside it.
    assert out == {
        "eou": 0.30,
        "stt": 0.12,
        "stt_transcription_delay": 0.08,
        "llm_ttft": 0.45,
        "tts": 0.09,
    }


def test_aggregate_components_stt_falls_back_to_transcription_delay():
    rows = [
        {
            "modality": "eou",
            "ttfb_ms": None,
            "metadata": {
                "eou": {"end_of_utterance_delay": 0.30, "transcription_delay": 0.08}
            },
        }
    ]
    out = aggregate_components(rows)
    # no stt row and no first-partial -> headline falls back to the tail.
    assert out == {"eou": 0.30, "stt": 0.08, "stt_transcription_delay": 0.08}


def test_aggregate_components_stt_prefers_ttfp_over_transcription_delay():
    """The turn's time-to-first-partial (head) is the STT headline, not the tail."""
    rows = [
        {
            "modality": "eou",
            "ttfb_ms": None,
            "metadata": {
                "eou": {
                    "end_of_utterance_delay": 0.30,
                    "transcription_delay": 0.08,
                    "time_to_first_partial_ms": 280.0,
                }
            },
        }
    ]
    out = aggregate_components(rows)
    assert out == {
        "eou": 0.30,
        "stt": 0.28,  # first-partial, not the 0.08 transcription_delay tail
        "stt_ttfp": 0.28,
        "stt_transcription_delay": 0.08,
    }


def test_aggregate_components_percall_stt_ttfb_outranks_ttfp():
    """A real per-call STT ttfb, if a provider emits one, wins over the turn TTFP."""
    rows = [
        {
            "modality": "eou",
            "ttfb_ms": None,
            "metadata": {"eou": {"time_to_first_partial_ms": 280.0}},
        },
        {"modality": "stt", "ttfb_ms": 120.0, "metadata": {}},
    ]
    out = aggregate_components(rows)
    assert out["stt"] == 0.12  # per-call ttfb is the headline
    assert out["stt_ttfp"] == 0.28  # ...but the turn TTFP is still exposed


def test_aggregate_components_averages_multiple_turns():
    rows = [
        {"modality": "llm", "ttfb_ms": 400.0, "metadata": {}},
        {"modality": "llm", "ttfb_ms": 600.0, "metadata": {}},
    ]
    assert aggregate_components(rows) == {"llm_ttft": 0.5}


def test_aggregate_components_empty_is_none():
    assert aggregate_components([]) is None
    assert (
        aggregate_components([{"modality": "llm", "ttfb_ms": None, "metadata": {}}])
        is None
    )


class _FakeStore:
    """Returns queued row-batches per get_requests_for_room call (poll sim)."""

    def __init__(self, batches):
        self._batches = list(batches)
        self.calls = 0

    async def get_requests_for_room(self, room):
        self.calls += 1
        return self._batches.pop(0) if self._batches else []


async def test_component_reader_reads_and_aggregates():
    reader = ComponentReader(_FakeStore([_split_rows()]))
    out = await reader.read("vg-probe-x")
    assert out == {
        "eou": 0.30,
        "stt": 0.12,
        "stt_transcription_delay": 0.08,
        "llm_ttft": 0.45,
        "tts": 0.09,
    }


async def test_component_reader_no_store_returns_none():
    assert await ComponentReader().read("vg-probe-x") is None


def _partial_rows(room="vg-probe-x"):
    # STT + LLM present but no TTS yet: a mid cross-process flush.
    return [
        {
            "modality": "eou",
            "ttfb_ms": None,
            "metadata": {
                "room": room,
                "eou": {"end_of_utterance_delay": 0.30, "transcription_delay": 0.08},
            },
        },
        {"modality": "stt", "ttfb_ms": 120.0, "metadata": {"room": room}},
        {"modality": "llm", "ttfb_ms": 450.0, "metadata": {"room": room}},
    ]


async def test_component_reader_polls_until_split_is_complete():
    # Partial (no TTS) twice, then the full split: keep polling until whole.
    store = _FakeStore([_partial_rows(), _partial_rows(), _split_rows()])
    reader = ComponentReader(store, poll_attempts=5, poll_delay=0.0)
    out = await reader.read("vg-probe-x")
    assert out is not None and out["tts"] == 0.09
    assert store.calls == 3  # did not return the TTS-less partial early


async def test_component_reader_gives_up_immediately_on_first_empty_read():
    # No rows for the room (uninstrumented / remote / collector mode): return
    # None on the first read, do NOT dead-wait the whole poll budget.
    store = _FakeStore([])  # always empty
    reader = ComponentReader(store, poll_attempts=6, poll_delay=0.0)
    assert await reader.read("vg-probe-x") is None
    assert store.calls == 1


async def test_component_reader_returns_partial_after_exhausting_polls():
    # Rows exist but the split never completes (agent emits no TTS ttfb): return
    # what we have rather than None, and let the renderer show only those.
    store = _FakeStore([_partial_rows(), _partial_rows()])
    reader = ComponentReader(store, poll_attempts=2, poll_delay=0.0)
    out = await reader.read("vg-probe-x")
    # no tts key; the eou row's transcription_delay still surfaces as the tail
    assert out == {
        "eou": 0.30,
        "stt": 0.12,
        "stt_transcription_delay": 0.08,
        "llm_ttft": 0.45,
    }
    assert store.calls == 2


async def test_probe_reads_components_after_loop():
    reader = ComponentReader(_FakeStore([_split_rows()]))
    _FakeClient.seq = iter([2.0, 2.0])  # warmup + 1
    runner = ProbeRunner(_FakeAdmin(), _FakeClient, _StubUtterance(), reader)
    result = await runner.probe(
        "realty", trials=1, warmup=True, room_name="vg-probe-x", metadata=""
    )
    assert result.components == {
        "eou": 0.30,
        "stt": 0.12,
        "stt_transcription_delay": 0.08,
        "llm_ttft": 0.45,
        "tts": 0.09,
    }


def test_render_latency_shows_only_present_components():
    # A partial split (no TTS) must not fabricate "TTS 0.00" (reads as instant).
    r = LatencyResult(
        agent="a",
        e2e_samples=[0.8],
        components={"eou": 0.30, "stt": 0.12, "llm_ttft": 0.45},
    )
    out = render_latency([r], target_ms=1500, summarize=summarize)
    assert "LLM-ttft 0.45" in out
    assert "TTS" not in out  # missing component omitted, not shown as 0.00
    assert "0.00" not in out
