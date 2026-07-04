from voicegateway.livekit_diag.sfu import RampStep, find_knee


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
