"""Shared summaries for latency experiments with explicit millisecond units."""

import numpy as np


def latency_summary(milliseconds: list[float]) -> dict[str, float]:
    if not milliseconds:
        raise ValueError("Latency samples must not be empty")
    return {f"p{p}_ms": float(np.percentile(milliseconds, p)) for p in (50, 95, 99)}
