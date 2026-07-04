from voicegateway.livekit_diag.resources import ResourceMonitor


def test_saturation_and_per_client():
    samples = iter([(10.0, 100.0, 0), (90.0, 120.0, 50_000), (91.0, 121.0, 100_000)])
    mon = ResourceMonitor(sampler=lambda: next(samples))
    mon.tick()
    mon.tick()
    mon.tick()
    rep = mon.report_for(n_clients=10)
    assert rep.cpu_peak == 91.0
    assert rep.saturated is True  # peak > 85
    assert rep.per_client["cpu_pct"] == round(91.0 / 10, 3)
    assert rep.sustainable_n is not None and rep.sustainable_n >= 1


def test_not_saturated_estimates_headroom():
    samples = iter([(20.0, 100.0, 0), (40.0, 110.0, 400_000)])
    mon = ResourceMonitor(sampler=lambda: next(samples))
    mon.tick()
    mon.tick()
    rep = mon.report_for(n_clients=10)
    assert rep.saturated is False
    # 40% for 10 clients -> ~4% each -> ~85/4 ~= 21 sustainable
    assert 15 <= rep.sustainable_n <= 30
