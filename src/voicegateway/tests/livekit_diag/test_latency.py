import asyncio

from voicegateway.livekit_diag.latency import ProbeRunner, summarize
from voicegateway.livekit_diag.config import LiveKitCreds


class _FakeAdmin:
    def __init__(self): self.created, self.deleted, self.dispatched = [], [], []
    async def create_room(self, n): self.created.append(n)
    async def delete_room(self, n): self.deleted.append(n)
    async def create_dispatch(self, r, a, metadata=""): self.dispatched.append((r, a))
    def join_token(self, r, i): return "tok"
    async def aclose(self): pass


class _FakeClient:
    seq = iter([1.40, 1.44, 1.42, 1.41])  # warmup + 3
    def __init__(self, url, token): pass
    async def connect(self): pass
    async def publish_utterance(self, src): return 0.0
    async def wait_reply(self, t0, timeout=15.0): return next(_FakeClient.seq)
    async def ping(self, timeout=2.0): return 0.03
    def quality(self): return "Excellent"
    async def disconnect(self): pass


async def test_probe_discards_warmup_and_aggregates():
    runner = ProbeRunner(_FakeAdmin(), _FakeClient, utterance=_StubUtterance())
    result = await runner.probe("realty", trials=3, warmup=True, room_name=None, metadata="")
    assert len(result.e2e_samples) == 3          # warmup discarded
    assert result.network_s == 0.03
    stats = summarize(result)
    assert round(stats["avg"], 2) == 1.42


class _StubUtterance:
    duration_s = 0.8
    def frames(self): return iter([(b"\x00\x00", 16000)])
