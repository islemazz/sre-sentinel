"""Compare the statistical detector with a machine-learning one, same data.

    pip install -r requirements-ml.txt
    python -m experiments.evaluate_ml

Detector A: LocalResidualDetector (robust z-score, median/MAD) - the one in production.
Detector B: Isolation Forest (scikit-learn) - unsupervised machine learning.

Fairness rules:
  * Both see the SAME simulated streams (seeds 100-119, held out like evaluate.py).
  * Both use only information available at that moment (past + current samples).
  * The Isolation Forest is trained on DIFFERENT seeds (1-10) and never sees the
    ground-truth labels. Only its expected anomaly rate (2%) is set by hand: the
    simulator injects a spike with probability 1% lasting 1-3 samples.
  * Same scoring as evaluate.py (sample-level precision/recall/F1, event recall).

How the forest's settings were chosen: on validation seeds 20-29, never on the
held-out seeds. A first version (two features, trained only on large spikes)
scored F1 0.00-0.77. Training on a mix of spike sizes and using the residual
alone gave the best validation result, so that is what runs here.
"""
from collections import deque
from statistics import median

import numpy as np
from sklearn.ensemble import IsolationForest

from app.detector import LocalResidualDetector
from app.evaluate import Scores
from app.simulator import MetricSimulator

BASELINE_WINDOW = 9      # same short window the z-score detector uses
TRAIN_SEEDS = range(1, 11)
TRAIN_SPIKE_SIZES = (10, 15, 20, 35)   # a real system does not know the spike size
TEST_SEEDS = range(100, 120)
N = 5000
EXPECTED_ANOMALY_RATE = 0.02


def stream(seed, spike_size=35.0):
    sim = MetricSimulator(seed=seed, clock=lambda: 0.0, spike_size=spike_size)
    return [sim.next() for _ in range(N)]


def features(values):
    """One causal feature per sample, None during warm-up.

    residual = value - median(last 9 samples)   (how far from "just now")
    """
    recent = deque(maxlen=BASELINE_WINDOW)
    rows = []
    for v in values:
        if len(recent) < BASELINE_WINDOW:
            rows.append(None)
        else:
            rows.append((v - median(recent),))
        recent.append(v)
    return rows


def train_model():
    X = []
    for seed in TRAIN_SEEDS:
        for size in TRAIN_SPIKE_SIZES:
            values = [s.value for s in stream(seed, size)]
            X += [r for r in features(values) if r is not None]
    model = IsolationForest(n_estimators=200, contamination=EXPECTED_ANOMALY_RATE,
                            random_state=0)
    model.fit(np.array(X))
    return model


def flags_zscore(samples):
    det = LocalResidualDetector()
    return [det.update(s.value).is_anomaly for s in samples]


def flags_forest(model, samples):
    rows = features([s.value for s in samples])
    flags = [False] * len(rows)
    idx = [i for i, r in enumerate(rows) if r is not None]
    predictions = model.predict(np.array([rows[i] for i in idx]))   # -1 = anomaly
    for i, p in zip(idx, predictions):
        flags[i] = p == -1
    return flags


def score(samples, flags):
    """Same counting rules as evaluate.evaluate_once."""
    s = Scores()
    in_event = caught = False
    for sample, flagged in zip(samples, flags):
        s.tp += flagged and sample.anomaly
        s.fp += flagged and not sample.anomaly
        s.fn += (not flagged) and sample.anomaly
        if sample.anomaly:
            if not in_event:
                in_event, caught = True, False
                s.events += 1
            caught = caught or flagged
        elif in_event:
            s.events_caught += caught
            in_event = False
    if in_event:
        s.events_caught += caught
    return s


def report():
    model = train_model()
    print("Statistical vs machine-learning detector - same data, 20 held-out seeds x 5000 samples")
    print("Isolation Forest trained on seeds 1-10 without labels; z-score has no training.\n")
    print(f"{'spike':>5} | {'detector':<16} | {'precision':>9} {'recall':>7} {'F1':>6} | {'events caught':>13} | {'false alarms':>12}")
    print("-" * 84)
    for size in (10, 15, 20, 35):
        totals = {"z-score": Scores(), "isolation forest": Scores()}
        for seed in TEST_SEEDS:
            samples = stream(seed, spike_size=size)
            totals["z-score"] += score(samples, flags_zscore(samples))
            totals["isolation forest"] += score(samples, flags_forest(model, samples))
        for name, r in totals.items():
            print(f"{size:>5} | {name:<16} | {r.precision:9.2%} {r.recall:7.2%} {r.f1:6.2f} | "
                  f"{r.events_caught:>4}/{r.events:<4} {r.event_recall:5.0%} | {r.fp:>12}")
        print("-" * 84)


if __name__ == "__main__":
    report()