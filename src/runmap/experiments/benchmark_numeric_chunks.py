"""Isolate numeric-buffer access; these measurements do not include route search."""

import argparse
from contextlib import closing
import json
from pathlib import Path
import resource
import sqlite3
import sys
from time import perf_counter
import tracemalloc
from typing import cast

import numpy as np

from runmap.experiments.latency import latency_summary
from runmap.paths.database import network_metadata
from runmap.paths.numeric_database import StoredNumericChunk, iter_numeric_chunks, open_numeric_database
from runmap.routing.types import Bounds


def _load_window(connection: sqlite3.Connection, bounds: Bounds) -> list[StoredNumericChunk]:
    return list(iter_numeric_chunks(connection, bounds))


def _selected_segments(chunks: list[StoredNumericChunk], bounds: Bounds) -> int:
    west, south, east, north = bounds
    count = 0
    for stored in chunks:
        boxes = stored.chunk.arrays["segment_bounds"]
        count += int(np.count_nonzero((boxes[:, 0] <= east) & (boxes[:, 2] >= west)
                                      & (boxes[:, 1] <= north) & (boxes[:, 3] >= south)))
    return count


def probe_numeric_window(database: Path, bounds: Bounds, repeats: int = 10, *,
                         trace_allocations: bool = False) -> dict[str, object]:
    if repeats < 1:
        raise ValueError("Repeat count must be positive")
    started = perf_counter()
    with closing(open_numeric_database(database)) as connection:
        open_ms = (perf_counter() - started) * 1000
        metadata = network_metadata(connection)
        started = perf_counter()
        chunks = _load_window(connection, bounds)
        first_ms = (perf_counter() - started) * 1000
        selected = _selected_segments(chunks, bounds)
        byte_size = sum(chunk.byte_size for chunk in chunks)
        count = len(chunks)
        del chunks
        samples: list[float] = []
        for _ in range(repeats):
            started = perf_counter()
            chunks = _load_window(connection, bounds)
            samples.append((perf_counter() - started) * 1000)
            del chunks
        allocations: dict[str, int] = {}
        if trace_allocations:
            tracemalloc.start()
            try:
                chunks = _load_window(connection, bounds)
                retained, peak = tracemalloc.get_traced_memory()
                allocations = dict(traced_retained_bytes=retained, traced_peak_bytes=peak)
                del chunks
            finally:
                tracemalloc.stop()
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return dict(dataset_id=metadata.dataset_id, bounds_epsg3067=bounds, chunks=count,
                selected_segments=selected, buffer_bytes=byte_size, open_ms=open_ms,
                first_load_ms=first_ms, repeated_load_ms=samples, summary=latency_summary(samples),
                allocations=allocations, peak_rss_bytes=rss if sys.platform == "darwin" else rss * 1024,
                note="Chunk retrieval and immutable array-view construction only; no search, dense graph assembly, metadata decoding, or response serialization. Every sample releases its chunk references. OS cache is uncontrolled; no application chunk cache. Allocation measurement is a separate untimed load.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("database", type=Path)
    parser.add_argument("--bounds", type=float, nargs=4, required=True)
    parser.add_argument("--repeats", type=int, default=10)
    parser.add_argument("--trace-allocations", action="store_true")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    report = probe_numeric_window(args.database, cast(Bounds, tuple(args.bounds)), args.repeats,
                                  trace_allocations=args.trace_allocations)
    payload = json.dumps(report, indent=2) + "\n"
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(payload)
    print(payload, end="")


if __name__ == "__main__":
    main()
