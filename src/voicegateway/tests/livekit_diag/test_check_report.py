from voicegateway.livekit_diag.admin import AgentRow
from voicegateway.livekit_diag.latency import LatencyResult, summarize
from voicegateway.livekit_diag.sfu import RampStep
from voicegateway.livekit_diag.report import check_json


def test_check_json_shape():
    agents = [AgentRow("realty", "r1", "x", "active", 1, 10.0)]
    lat = [LatencyResult("realty", [1.4, 1.42, 1.41], 0.03, {"stt": 0.2})]
    base = RampStep(2, 4.0, 0.0, "Excellent")
    js = check_json(agents, lat, base, [], None, None, summarize)
    assert js["agents"][0]["agent_name"] == "realty"
    assert js["latency"][0]["agent"] == "realty"
    assert js["latency"][0]["stats"]["trials"] == 3
    assert js["sfu"]["baseline"]["rtt_ms"] == 4.0
    assert js["verdict"] in {"PASS", "WARN", "FAIL"}
