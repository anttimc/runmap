"""Length-aware flat-path display features, independent of viewport and tiles."""

from collections.abc import Iterator, Sequence
from dataclasses import dataclass
import math

from runmap.routing.types import Measurement

FLAT_MODEL_VERSION = "flat-run-thresholds-v1"
MINIMUM_FLAT_LENGTH_M = 50.0
FLAT_GRADE_THRESHOLDS = tuple(step / 2 for step in range(2, 21))


@dataclass(frozen=True)
class GradeInterval:
    start_m: float
    end_m: float
    grade_percent: float | None


def measurement_flat_thresholds(intervals: Sequence[Measurement]) -> list[float | None]:
    return flat_run_thresholds([
        GradeInterval(item["start_m"], item["end_m"], item["grade_percent"]
                      if item["valid"] and not {"bridge", "tunnel", "stairs"}.intersection(item["quality_flags"])
                      else None)
        for item in intervals
    ])


def flat_spans(intervals: Sequence[GradeInterval], threshold: float) -> Iterator[tuple[int, int]]:
    """Keep missing grades and chainage gaps from joining otherwise flat patches."""
    start: int | None = None
    for index, interval in enumerate(intervals):
        connected = index == 0 or abs(interval.start_m - intervals[index - 1].end_m) < 1e-6
        flat = interval.grade_percent is not None and abs(interval.grade_percent) <= threshold
        if start is not None and (not connected or not flat):
            yield start, index
            start = None
        if flat and start is None:
            start = index
    if start is not None:
        yield start, len(intervals)


def flat_run_thresholds(
    intervals: Sequence[GradeInterval], minimum_length_m: float = MINIMUM_FLAT_LENGTH_M,
) -> list[float | None]:
    """Find the first supported grade limit giving each sample a long enough flat run."""
    if not math.isfinite(minimum_length_m) or minimum_length_m <= 0:
        raise ValueError("Minimum flat length must be finite and positive")
    result: list[float | None] = [None] * len(intervals)
    for threshold in FLAT_GRADE_THRESHOLDS:
        for start, end in flat_spans(intervals, threshold):
            length = intervals[end - 1].end_m - intervals[start].start_m
            if length + 1e-6 >= minimum_length_m:
                for index in range(start, end):
                    if result[index] is None:
                        result[index] = threshold
    return result
