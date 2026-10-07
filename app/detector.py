"""Streaming anomaly detector: local baseline + robust residual score.

Two questions decide whether a value is anomalous:

  1. How far is it from what the metric was doing *just now*?
     -> residual = value - median(last `baseline_window` samples)
        (a short window follows slow trends, so a normal rise or fall of the
        signal produces only a small residual)

  2. Is that gap unusual compared with the gaps we normally see?
     -> robust z-score of the residual against the last `scale_window`
        residuals, using median/MAD instead of mean/std

            score = 0.6745 * (residual - median(res)) / MAD(res)

Why median/MAD? A spike inflates the mean and especially the standard deviation
of a window, which then hides the NEXT spike. The median and MAD barely move
when a few outliers enter the window, so the notion of "normal" stays honest.
(0.6745 rescales MAD so the score is comparable to an ordinary z-score.)

History of this design: our first attempt compared each value with the median
of a long 60-sample window. The slow wave in the signal dragged that baseline
and widened the scale, giving ~1.4% false alarms (F1 0.65 at best). See
`python -m app.evaluate` and the README for the measured comparison.
"""
from collections import deque
from dataclasses import dataclass
from statistics import median


@dataclass(frozen=True)
class Detection:
    is_anomaly: bool
    score: float        # signed robust z-score (0.0 during warm-up)
    baseline: float     # local median the value was compared against


class LocalResidualDetector:
    def __init__(self, baseline_window=9, scale_window=120, threshold=3.5,
                 min_samples=30, min_mad=0.5):
        if min_samples > scale_window:
            raise ValueError("min_samples cannot exceed scale_window")
        self.threshold = threshold
        self.min_samples = min_samples      # residuals needed before we start flagging
        self.min_mad = min_mad              # floor: a flat signal must not divide by ~0
        self._recent = deque(maxlen=baseline_window)
        self._residuals = deque(maxlen=scale_window)

    def update(self, value: float) -> Detection:
        """Score `value` against the recent past, THEN add it to the history."""
        if len(self._recent) < self._recent.maxlen:      # still filling the baseline
            self._recent.append(value)
            return Detection(False, 0.0, value)

        baseline = median(self._recent)
        residual = value - baseline
        result = Detection(False, 0.0, baseline)

        if len(self._residuals) >= self.min_samples:
            centre = median(self._residuals)
            mad = max(median(abs(r - centre) for r in self._residuals), self.min_mad)
            score = 0.6745 * (residual - centre) / mad
            result = Detection(abs(score) >= self.threshold, score, baseline)

        # Anomalies stay in the history on purpose: median/MAD tolerate them,
        # and dropping them could lock the detector out after a real level shift.
        self._recent.append(value)
        self._residuals.append(residual)
        return result
