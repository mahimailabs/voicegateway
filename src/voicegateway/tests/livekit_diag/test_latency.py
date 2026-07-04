from voicegateway.livekit_diag.latency import (
    ComponentReader,
    ProbeRunner,
    aggregate_components,
    summarize,
)


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
    assert out == {"eou": 0.30, "stt": 0.12, "llm_ttft": 0.45, "tts": 0.09}


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
    assert out == {"eou": 0.30, "stt": 0.08}  # no stt row -> transcription_delay


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
    assert out == {"eou": 0.30, "stt": 0.12, "llm_ttft": 0.45, "tts": 0.09}


async def test_component_reader_no_store_returns_none():
    assert await ComponentReader().read("vg-probe-x") is None


async def test_component_reader_polls_until_rows_appear():
    store = _FakeStore([[], [], _split_rows()])  # empty twice, then rows
    reader = ComponentReader(store, poll_attempts=5, poll_delay=0.0)
    out = await reader.read("vg-probe-x")
    assert out is not None and out["llm_ttft"] == 0.45
    assert store.calls == 3  # stopped as soon as rows landed


async def test_component_reader_gives_up_after_attempts():
    store = _FakeStore([])  # always empty
    reader = ComponentReader(store, poll_attempts=3, poll_delay=0.0)
    assert await reader.read("vg-probe-x") is None
    assert store.calls == 3


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
        "llm_ttft": 0.45,
        "tts": 0.09,
    }
