"""Score the detector against the simulator's ground truth.

    python -m app.evaluate

Two views of "did it work?":
  * sample level - every sample is a yes/no decision (precision / recall / F1)
  * event level  - a spike is one event (1-3 consecutive samples); it counts as
                   caught if the detector fires on ANY sample of it. This is
                   closer to what an on-call engineer cares about.
"""
from dataclasses import dataclass

from .detector import LocalResidualDetector
from .simulator import MetricSimulator


@dataclass
class Scores:
    tp: int = 0          # flagged and really anomalous
    fp: int = 0          # flagged but normal  (false alarm)
    fn: int = 0          # missed anomalous sample
    events: int = 0      # injected spikes
    events_caught: int = 0

    def __iadd__(self, o):
        self.tp += o.tp; self.fp += o.fp; self.fn += o.fn
        self.events += o.events; self.events_caught += o.events_caught
        return self

    @property
    def precision(self):
        return self.tp / (self.tp + self.fp) if self.tp + self.fp else 1.0

    @property
    def recall(self):
        return self.tp / (self.tp + self.fn) if self.tp + self.fn else 1.0

    @property
    def f1(self):
        p, r = self.precision, self.recall
        return 2 * p * r / (p + r) if p + r else 0.0

    @property
    def event_recall(self):
        return self.events_caught / self.events if self.events else 1.0


def evaluate_once(seed, n=5000, detector_kwargs=None, sim_kwargs=None) -> Scores:
    sim = MetricSimulator(seed=seed, clock=lambda: 0.0, **(sim_kwargs or {}))
    det = LocalResidualDetector(**(detector_kwargs or {}))
    s = Scores()
    in_event = caught = False
    for _ in range(n):
        sample = sim.next()
        flagged = det.update(sample.value).is_anomaly
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


def evaluate(seeds=range(100, 120), **kwargs) -> Scores:
    total = Scores()
    for seed in seeds:
        total += evaluate_once(seed, **kwargs)
    return total


def report():
    print("Detector evaluation - 20 seeds x 5000 samples each (ground truth = injected spikes)")
    print("Seeds 100-119: held out, NOT used when choosing the detector defaults.\n")
    print(f"{'spike size':>10} | {'precision':>9} {'recall':>7} {'F1':>6} | {'events caught':>13} | {'false alarms':>12}")
    print("-" * 72)
    for size in (10, 15, 20, 35):
        r = evaluate(sim_kwargs={"spike_size": size})
        print(
            f"{size:>10} | {r.precision:9.2%} {r.recall:7.2%} {r.f1:6.2f} | "
            f"{r.events_caught:>4}/{r.events:<4} {r.event_recall:5.0%} | {r.fp:>12}"
        )


if __name__ == "__main__":
    report()
