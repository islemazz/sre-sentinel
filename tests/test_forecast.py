import random

import pytest

from app.forecast import TrendForecaster
from app.simulator import LeakSimulator


def test_no_alert_during_warmup():
    f = TrendForecaster(min_samples=30)
    for i in range(29):
        r = f.update(50.0 + i)          # steep climb, but too few samples yet
        assert r.alert is False
        assert r.eta is None


def test_rising_line_gives_the_right_eta():
    f = TrendForecaster(limit=90.0, window=60, horizon=1000)
    for i in range(60):
        r = f.update(40.0 + 0.5 * i)    # exactly +0.5 per sample, ends at 69.5
    assert r.slope == pytest.approx(0.5)
    assert r.r2 == pytest.approx(1.0)
    assert r.eta == pytest.approx((90.0 - 69.5) / 0.5)   # 41 samples


def test_alert_only_when_breach_is_inside_the_horizon():
    far = TrendForecaster(limit=90.0, horizon=20)
    near = TrendForecaster(limit=90.0, horizon=100)
    for i in range(60):
        r_far = far.update(40.0 + 0.5 * i)
        r_near = near.update(40.0 + 0.5 * i)
    assert r_far.alert is False         # eta 41 > horizon 20
    assert r_near.alert is True         # eta 41 <= horizon 100


def test_flat_noisy_signal_never_alerts():
    rng = random.Random(1)
    f = TrendForecaster()
    alerts = sum(f.update(45.0 + rng.gauss(0, 2)).alert for _ in range(3000))
    assert alerts == 0


def test_falling_signal_never_alerts():
    f = TrendForecaster()
    alerts = sum(f.update(80.0 - 0.1 * i).alert for i in range(300))
    assert alerts == 0


def test_value_at_the_limit_is_an_immediate_alert():
    f = TrendForecaster(limit=90.0, min_samples=5)
    for _ in range(10):
        r = f.update(95.0)
    assert r.alert is True
    assert r.eta == 0.0


def test_invalid_config_is_rejected():
    with pytest.raises(ValueError):
        TrendForecaster(window=10, min_samples=30)


def test_leak_is_predicted_well_before_the_breach():
    """Ground truth: the leak simulator crosses 90 % at a known moment."""
    lead_times = []
    for seed in range(5):
        sim = LeakSimulator(seed=seed)
        f = TrendForecaster()
        first_alert = None
        for i in range(700):
            value = sim.next().value
            r = f.update(value)
            if r.alert and first_alert is None:
                first_alert = i
            if value >= 90.0:
                assert first_alert is not None and first_alert < i, "breach was not predicted"
                lead_times.append(i - first_alert)
                break
    assert min(lead_times) >= 60        # at least 60 samples of warning, every time


def test_no_false_alarms_on_the_leak_signal_far_from_the_limit():
    sim = LeakSimulator(seed=3)
    f = TrendForecaster()
    for _ in range(1500):
        r = f.update(sim.next().value)
        if sim._level < 66.0:           # breach more than 240 samples away
            assert r.alert is False