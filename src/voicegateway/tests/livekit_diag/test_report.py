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
