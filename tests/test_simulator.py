from app.simulator import MetricSimulator


def run(sim, n):
    return [sim.next() for _ in range(n)]


def test_same_seed_gives_same_stream():
    a = run(MetricSimulator(seed=7, clock=lambda: 0.0), 200)
    b = run(MetricSimulator(seed=7, clock=lambda: 0.0), 200)
    assert a == b


def test_values_stay_within_0_100():
    samples = run(MetricSimulator(seed=1, spike_prob=0.2), 2000)
    assert all(0.0 <= s.value <= 100.0 for s in samples)


def test_spikes_are_injected_and_labelled():
    samples = run(MetricSimulator(seed=3, spike_prob=0.05), 2000)
    anomalies = [s for s in samples if s.anomaly]
    normal = [s for s in samples if not s.anomaly]
    assert anomalies, "expected at least one injected spike in 2000 samples"
    avg = lambda xs: sum(x.value for x in xs) / len(xs)
    # labelled samples really are higher than normal ones
    assert avg(anomalies) > avg(normal) + 20


def test_no_spikes_when_probability_is_zero():
    samples = run(MetricSimulator(seed=5, spike_prob=0.0), 500)
    assert not any(s.anomaly for s in samples)
