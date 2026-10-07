"""Synthetic metric stream with *known* anomalies.

Why a simulator? Real cluster metrics only arrive in the Kubernetes stage.
Until then we need data that behaves like a real CPU signal AND tells us the
truth about when we injected a problem. That ground truth lets us measure the
anomaly detector later (precision / recall) instead of just eyeballing a chart.

Signal = slow sine wave (daily load pattern) + Gaussian noise + random spikes.
"""
import math
import random
import time
from dataclasses import dataclass


@dataclass(frozen=True)
class Sample:
    ts: float        # unix timestamp
    value: float     # CPU utilisation, percent (0-100)
    anomaly: bool    # ground truth: True while an injected spike is active


class MetricSimulator:
    def __init__(
        self,
        seed=None,
        base=45.0,          # average CPU %
        amplitude=10.0,     # size of the slow wave around the base
        period=120,         # samples per full wave
        noise=2.0,          # std-dev of random jitter
        spike_prob=0.01,    # chance per sample that a spike starts
        spike_size=35.0,    # how many CPU points a spike adds
        spike_len=(1, 3),   # a spike lasts between 1 and 3 samples
        clock=time.time,    # injectable so tests don't depend on real time
    ):
        self._rng = random.Random(seed)
        self.base = base
        self.amplitude = amplitude
        self.period = period
        self.noise = noise
        self.spike_prob = spike_prob
        self.spike_size = spike_size
        self.spike_len = spike_len
        self._clock = clock
        self._i = 0
        self._spike_left = 0

    def next(self) -> Sample:
        wave = self.amplitude * math.sin(2 * math.pi * self._i / self.period)
        value = self.base + wave + self._rng.gauss(0, self.noise)

        if self._spike_left == 0 and self._rng.random() < self.spike_prob:
            self._spike_left = self._rng.randint(*self.spike_len)

        is_anomaly = self._spike_left > 0
        if is_anomaly:
            value += self.spike_size * self._rng.uniform(0.8, 1.2)
            self._spike_left -= 1

        self._i += 1
        return Sample(ts=self._clock(), value=min(100.0, max(0.0, value)), anomaly=is_anomaly)
