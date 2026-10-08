"""Predictive alert: will this metric hit its limit soon?

The anomaly detector answers "is something wrong right now?". This answers a
different question: "if nothing changes, when will it go wrong?" - for example
memory that grows a little on every request (a leak) or a disk that fills up.

Method (deliberately simple and explainable):

  1. Fit a straight line through the last `window` samples (least squares).
  2. If the line is going UP, extend it until it reaches `limit`:
         eta = (limit - value_on_the_line_now) / slope        [in samples]
  3. Raise an alert only if all of these hold:
       - the slope is big enough to matter        (slope >= min_slope)
       - the line really explains the data        (r2 >= min_r2)
       - the breach is close                      (eta <= horizon)

Why r2? A noisy flat signal can produce a tiny positive slope by luck. r2 says
how much of the movement the line explains (1.0 = perfectly straight, 0.0 =
just noise), so noise does not trigger alerts.

Why a line and not machine learning? There is nothing to train on, it runs in
microseconds, and when it alerts you can explain exactly why. A model would
only be worth it for signals a line cannot describe (daily seasonality, ...).
"""
from collections import deque
from dataclasses import dataclass


@dataclass(frozen=True)
class Forecast:
    slope: float           # change per sample on the fitted line (0.0 during warm-up)
    r2: float              # how well the line fits, 0..1 (0.0 during warm-up)
    eta: float | None      # samples until the line reaches the limit; None = not heading there
    alert: bool            # True when the breach is predicted to happen within `horizon` samples


class TrendForecaster:
    def __init__(self, limit=90.0, window=60, horizon=120, min_samples=30,
                 min_slope=0.02, min_r2=0.5):
        if min_samples > window:
            raise ValueError("min_samples cannot exceed window")
        self.limit = limit
        self.horizon = horizon          # alert if the breach is predicted within this many samples
        self.min_samples = min_samples
        self.min_slope = min_slope      # ignore trends flatter than this (units per sample)
        self.min_r2 = min_r2
        self._values = deque(maxlen=window)

    def update(self, value: float) -> Forecast:
        self._values.append(value)
        n = len(self._values)
        if n < self.min_samples:
            return Forecast(0.0, 0.0, None, False)

        # Least squares line y = a + b*x with x = 0..n-1 (x is "samples ago").
        mean_x = (n - 1) / 2
        mean_y = sum(self._values) / n
        sxx = sum((x - mean_x) ** 2 for x in range(n))
        sxy = sum((x - mean_x) * (y - mean_y) for x, y in enumerate(self._values))
        syy = sum((y - mean_y) ** 2 for y in self._values)
        slope = sxy / sxx
        r2 = (sxy * sxy) / (sxx * syy) if syy > 0 else 0.0

        if value >= self.limit:         # already there: nothing left to predict
            return Forecast(slope, r2, 0.0, True)
        if slope < self.min_slope:
            return Forecast(slope, r2, None, False)

        now_on_line = mean_y + slope * ((n - 1) - mean_x)
        eta = max(0.0, (self.limit - now_on_line) / slope)
        return Forecast(slope, r2, eta, r2 >= self.min_r2 and eta <= self.horizon)