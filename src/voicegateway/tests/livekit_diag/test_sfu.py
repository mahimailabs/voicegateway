from typing import Any

from voicegateway.livekit_diag.sfu import (
    MEAN_OF_N,
    NOT_MEASURED,
    RampStep,
    SfuProbe,
    find_knee,
)

# The CLI's default --target-rtt-ms, i.e. the budget the live run failed against.
TARGET_RTT_MS = 50.0
# Shaped like the live Hetzner run: the first data-channel message of a session
# costs ~129ms (TLS/ICE/DTLS setup), every one after it ~26ms.
COLD_MS = 129.3
WARM_MS = 26.0


def test_find_knee_at_first_threshold_break():
    steps = [
        RampStep(10, 5.0, 0.0, "Excellent"),
        RampStep(25, 9.0, 0.1, "Good"),
        RampStep(50, 22.0, 1.4, "Poor"),
    ]
    assert find_knee(steps, target_rtt_ms=20.0, max_loss=1.0) == 25  # last good before break


def test_find_knee_none_when_all_healthy():
    steps = [RampStep(10, 4.0, 0.0, "Excellent"), RampStep(25, 6.0, 0.0, "Good")]
    assert find_knee(steps, target_rtt_ms=20.0, max_loss=1.0) is None


class _FakeAdmin:
    url = "ws://fake"

    def __init__(self) -> None:
        self.deleted: list[str] = []

    def join_token(self, room: str, identity: str) -> str:
        return f"{room}/{identity}"

    async def delete_room(self, room: str) -> None:
        self.deleted.append(room)


class _FakeMonitor:
    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        return None

    def report_for(self, clients: int) -> dict[str, int]:
        return {"peak_clients": clients}


class _ColdStartClient:
    """A client whose FIRST ping pays the session's connection setup."""

    def __init__(self, *, cold_ms: float = COLD_MS, warm_ms: float = WARM_MS) -> None:
        self._cold_ms = cold_ms
        self._warm_ms = warm_ms
        self.pings = 0
        self.connected = False

    async def connect(self) -> None:
        self.connected = True

    async def ping(self) -> float | None:
        self.pings += 1
        ms = self._cold_ms if self.pings == 1 else self._warm_ms
        return ms / 1000.0

    def quality(self) -> str:
        return "Excellent"

    async def disconnect(self) -> None:
        self.connected = False


class _SilentClient(_ColdStartClient):
    """Connects, but no pong ever comes back: every ping times out."""

    async def ping(self) -> float | None:
        self.pings += 1
        return None


class _OneAnswersClient(_ColdStartClient):
    """Only the client named ``c0`` answers; the rest time out."""

    def __init__(self, identity: str) -> None:
        super().__init__()
        self._identity = identity

    async def ping(self) -> float | None:
        self.pings += 1
        if self._identity != "c0":
            return None
        return WARM_MS / 1000.0


def _probe(made: list[Any], factory=None) -> SfuProbe:
    def default_factory(url: str, token: str) -> Any:
        c = _ColdStartClient()
        made.append(c)
        return c

    return SfuProbe(_FakeAdmin(), factory or default_factory, _FakeMonitor())


async def test_baseline_excludes_the_cold_first_sample():
    """The measured rtt is the steady state, not the setup-inflated first ping.

    Before the warm-up, the two-client baseline averaged one cold ping with one
    warm one and reported ~77.6ms against a 50ms budget on a ~26ms server.
    """
    made: list[Any] = []
    step = await _probe(made).baseline("vg-t-baseline", seconds=0.0)

    assert step.rtt_ms == WARM_MS
    assert step.rtt_ms <= TARGET_RTT_MS
    # One discarded warm-up ping plus one measured ping, per client.
    assert [c.pings for c in made] == [2, 2]


async def test_step_says_how_many_samples_it_averaged():
    made: list[Any] = []
    step = await _probe(made).baseline("vg-t-samples", seconds=0.0)

    # The warm-up ping is discarded, so it is not counted as evidence.
    assert step.samples == 2
    assert step.rtt_stat == MEAN_OF_N


async def test_step_with_no_returned_ping_is_not_measured():
    """Nothing came back, so the step says so instead of naming a statistic.

    ``rtt_ms`` cannot carry None without changing the field type that
    gates/report/distributed read, so ``rtt_stat`` is what disambiguates a
    placeholder from a reading.
    """

    def factory(url: str, token: str) -> Any:
        return _SilentClient()

    step = await _probe([], factory).baseline("vg-t-silent", seconds=0.0)

    assert step.samples == 0
    assert step.rtt_stat == NOT_MEASURED


async def test_partially_answered_step_counts_only_the_pings_that_returned():
    def factory(url: str, token: str) -> Any:
        return _OneAnswersClient(token.rsplit("/", 1)[-1])

    steps, _resource = await _probe([], factory).ramp(
        "vg-t-partial", [4], 0.0, TARGET_RTT_MS, 1.0
    )

    # Three of the four clients never answered: the step is the mean of the one
    # that did, and it says one, rather than passing a lone reading off as the
    # tier's converged latency.
    assert steps[0].samples == 1
    assert steps[0].rtt_stat == MEAN_OF_N
    assert steps[0].rtt_ms == WARM_MS


async def test_ramp_tiers_are_not_dominated_by_setup_cost():
    """The live run's tell: 129.3ms at 2 clients against 26.1/25.7 at 10/25.

    More clients measuring faster is backwards for a capacity probe. With the
    cold sample out of the measurement the tiers sit flat, and the smallest tier
    (the only one sfu_capacity_gate reads) is inside the same budget it failed.
    """
    made: list[Any] = []
    steps, _resource = await _probe(made).ramp(
        "vg-t-ramp", [2, 10, 25], 0.0, TARGET_RTT_MS, 1.0
    )

    rtts = [s.rtt_ms for s in steps]
    assert [s.clients for s in steps] == [2, 10, 25]
    assert [s.samples for s in steps] == [2, 10, 25]
    assert max(rtts) <= 1.2 * min(rtts)  # flat across tiers, not 5x at the smallest
    assert all(rtt <= TARGET_RTT_MS for rtt in rtts)
    # Control: the un-warmed two-client tier is what breached the budget.
    unwarmed_smallest_tier = (COLD_MS + WARM_MS) / 2
    assert unwarmed_smallest_tier > TARGET_RTT_MS
    assert rtts[0] < unwarmed_smallest_tier / 2
    # Every client of every tier was warmed exactly once and measured once.
    assert {c.pings for c in made} == {2}
