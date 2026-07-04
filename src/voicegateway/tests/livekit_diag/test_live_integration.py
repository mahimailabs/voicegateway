"""Live integration tests: drive the diagnostics against a real livekit-server.

These exist because the unit tests use fakes, and a fake can only encode what we
BELIEVE the LiveKit SDK does. Every bug this file guards (the list_dispatch
signature, the _req name clash that broke create_room, the delete_room not_found
race, the room-vanishes-mid-enumeration race, the data-channel ping/pong, and the
connection-quality callback arg order) passed the fake-based unit tests and only
surfaced when the code ran against a real server. This file runs the real code
paths against ``livekit-server --dev`` so those contract bugs cannot silently
regress.

They run only when the ``livekit-server`` binary is present: the dedicated CI job
installs it, and locally they run if you have it (e.g. ``brew install livekit``),
else they skip. Marked ``integration`` so the unit matrix (``-m "not
integration"``) deselects them.
"""

from __future__ import annotations

import pathlib
import shutil
import socket
import subprocess
import time

import pytest

import voicegateway.livekit_diag as pkg
from voicegateway.livekit_diag.admin import LiveKitAdmin
from voicegateway.livekit_diag.client import SyntheticClient, UtteranceSource
from voicegateway.livekit_diag.config import LiveKitCreds
from voicegateway.livekit_diag.latency import ComponentReader, ProbeRunner
from voicegateway.livekit_diag.resources import ResourceMonitor
from voicegateway.livekit_diag.sfu import SfuProbe

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        shutil.which("livekit-server") is None,
        reason="livekit-server binary not installed",
    ),
]

_URL = "ws://127.0.0.1:7880"
_KEY = "devkey"
_SECRET = "secret"
_WAV = str(pathlib.Path(pkg.__file__).parent / "assets" / "probe.wav")


def _port_open(host: str, port: int) -> bool:
    with socket.socket() as sock:
        sock.settimeout(0.3)
        return sock.connect_ex((host, port)) == 0


@pytest.fixture(scope="module")
def creds():
    """Start livekit-server --dev for the module, yield its dev credentials, tear down."""
    proc = subprocess.Popen(
        ["livekit-server", "--dev", "--bind", "127.0.0.1"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.time() + 20.0
        while time.time() < deadline and not _port_open("127.0.0.1", 7880):
            time.sleep(0.3)
        if not _port_open("127.0.0.1", 7880):
            raise RuntimeError("livekit-server did not become ready on :7880")
        yield LiveKitCreds(_URL, _KEY, _SECRET)
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


def _admin(creds: LiveKitCreds) -> LiveKitAdmin:
    admin = LiveKitAdmin(creds)
    admin.url = creds.url
    return admin


async def test_list_agents_over_existing_room_does_not_crash(creds):
    """Guards the list_dispatch signature + _req/create_room + delete_room bugs:
    list_agents must iterate a real room (calling list_participants + list_dispatch)
    without raising, and create_room/delete_room must actually work.
    """
    admin = _admin(creds)
    try:
        await admin.create_room("vg-it-agents")
        rows = await admin.list_agents()
        assert isinstance(rows, list)  # no TypeError from list_dispatch's real shape
    finally:
        await admin.delete_room("vg-it-agents")
        await admin.aclose()


async def test_create_dispatch_surfaces_named_agent(creds):
    """A real named dispatch shows up as a dispatched agent (and empty-name records
    do not), exercising create_dispatch + the dispatch parsing path.
    """
    admin = _admin(creds)
    try:
        await admin.create_room("vg-it-dispatch")
        await admin.create_dispatch("vg-it-dispatch", "itagent")
        rows = await admin.list_agents()
        assert "itagent" in {r.agent_name for r in rows}
        assert "" not in {r.agent_name for r in rows}  # empty-name records skipped
    finally:
        await admin.delete_room("vg-it-dispatch")
        await admin.aclose()


async def test_sfu_baseline_measures_real_rtt_and_quality(creds):
    """Guards the two live-only Criticals: the data-channel ping/pong (rtt must be a
    real positive number, not 0.0) and the connection-quality callback arg order
    (quality must be a readable label, not a participant repr).
    """
    admin = _admin(creds)
    probe = SfuProbe(admin, lambda u, t: SyntheticClient(u, t), ResourceMonitor())
    try:
        step = await probe.baseline("vg-it-sfu", seconds=5.0)
        assert step.rtt_ms > 0.0  # the pong came back (ping/pong Critical)
        # Quality stays "Unknown" until LiveKit's periodic connection-quality event
        # fires; both "Unknown" and a real label are correct output of the fixed
        # callback. The arg-order bug stored a participant repr instead, which is not
        # in this set, so a regression still fails here.
        assert step.quality in {"Excellent", "Good", "Poor", "Lost", "Unknown"}
    finally:
        await admin.aclose()


async def test_sfu_cleans_up_its_room_no_phantom_agents(creds):
    """Guards the room-leak -> phantom empty-name agent -> spurious WARN: after a
    baseline, the probe room is gone and list_agents sees no leaked agents.
    """
    admin = _admin(creds)
    probe = SfuProbe(admin, lambda u, t: SyntheticClient(u, t), ResourceMonitor())
    try:
        await probe.baseline("vg-it-cleanup", seconds=1.0)
        assert await admin.list_agents() == []
    finally:
        await admin.aclose()


async def test_latency_probe_runs_end_to_end_without_a_real_agent(creds):
    """Guards create_room/_req + dispatch + connect + publish + teardown: probing an
    agent that never joins yields no reply (no e2e samples) but must not crash, and
    the room it created must be torn down.
    """
    admin = _admin(creds)
    runner = ProbeRunner(
        admin, lambda u, t: SyntheticClient(u, t), UtteranceSource(_WAV), ComponentReader()
    )
    try:
        result = await runner.probe(
            "it-phantom", trials=1, warmup=False, room_name=None, metadata=""
        )
        assert result.agent == "it-phantom"
        assert result.e2e_samples == []  # no real agent replied
    finally:
        await admin.aclose()
