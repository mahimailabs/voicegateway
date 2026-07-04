from voicegateway.livekit_diag.admin import AgentRow
from voicegateway.livekit_diag.report import agents_json, render_agents


def _rows():
    return [
        AgentRow("realty", "r1", "agent-x", "active", 1, 44.0),
        AgentRow("concierge", "r2", None, "dispatched", 0, None),
    ]


def test_render_agents_lists_and_footers():
    out = render_agents(_rows())
    assert "realty" in out and "concierge" in out
    assert "2 agents" in out
    assert "not reported by LiveKit" in out  # honest roster-gap footer


def test_render_agents_empty():
    out = render_agents([])
    assert "0 agents" in out


def test_agents_json_shape():
    js = agents_json(_rows())
    assert js[0]["agent_name"] == "concierge"  # sorted
    assert set(js[0]) == {"agent_name", "room", "identity", "state", "humans", "age_s"}


def _roster():
    return [
        {"agent_name": "realty", "status": "busy", "region": "iad", "version": "0.13.0"},
        {"agent_name": "concierge", "status": "idle", "region": None, "version": ""},
        {"agent_name": "night-shift", "status": "offline", "region": "sjc", "version": "0.12.0"},
    ]


def test_render_agents_with_roster_shows_worker_table():
    out = render_agents(_rows(), _roster())
    # in-room view is still present
    assert "2 agents active" in out
    # roster section replaces the "not reported" note
    assert "not reported by LiveKit" not in out
    assert "Registered workers (heartbeat roster):" in out
    assert "night-shift" in out
    assert "3 workers registered (1 idle, 1 busy, 1 offline)." in out


def test_render_agents_empty_roster_is_configured_not_note():
    out = render_agents(_rows(), [])
    # collector configured but no workers reported: table header, no note
    assert "not reported by LiveKit" not in out
    assert "Registered workers (heartbeat roster):" in out
    assert "0 workers registered (0 idle, 0 busy, 0 offline)." in out
