import random

import pytest

from app.detector import LocalResidualDetector
from app.evaluate import evaluate


def noisy_flat(n, level=50.0, noise=2.0, seed=0):
    rng = random.Random(seed)
    return [level + rng.gauss(0, noise) for _ in range(n)]


def feed(det, values):
    return [det.update(v) for v in values]


def test_never_flags_during_warmup():
    det = LocalResidualDetector()
    # baseline_window (9) + min_samples (30) values are needed before any decision
    results = feed(det, [50.0] * 8 + [500.0])
    assert not any(r.is_anomaly for r in results)


def test_flags_an_upward_spike():
    det = LocalResidualDetector()
    feed(det, noisy_flat(150))
    r = det.update(50.0 + 40.0)
    assert r.is_anomaly and r.score > 0


def test_flags_a_downward_drop_with_negative_score():
    det = LocalResidualDetector()
    feed(det, noisy_flat(150))
    r = det.update(50.0 - 40.0)
    assert r.is_anomaly and r.score < 0


def test_quiet_on_normal_noise():
    det = LocalResidualDetector()
    results = feed(det, noisy_flat(3000, seed=42))
    false_alarm_rate = sum(r.is_anomaly for r in results) / len(results)
    assert false_alarm_rate < 0.01


def test_flat_signal_does_not_crash_or_flag():
    det = LocalResidualDetector()
    results = feed(det, [50.0] * 300)          # MAD would be 0 without the floor
    assert not any(r.is_anomaly for r in results)


def test_invalid_config_is_rejected():
    with pytest.raises(ValueError):
        LocalResidualDetector(scale_window=20, min_samples=30)


def test_quality_regression_guard():
    """If a future change makes the detector worse, CI fails here."""
    r = evaluate(seeds=range(100, 104), n=3000, sim_kwargs={"spike_size": 35})
    assert r.f1 >= 0.90
    assert r.event_recall >= 0.95
