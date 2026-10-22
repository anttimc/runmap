"""Measure one local SQLite graph in a fresh reader process."""

import argparse
from contextlib import closing
import json
from pathlib import Path
import resource
import sys
from time import perf_counter
from typing import cast

from runmap.paths.database import open_network_database
from runmap.paths.local_network import load_local_network
from runmap.routing.geometry import indexed_node_point
from runmap.routing.search import indexed_shortest_distances
from runmap.routing.types import Bounds


def probe_local_network(database: Path, bounds: Bounds) -> dict[str, object]:
    started = perf_counter()
    with closing(open_network_database(database)) as connection:
        local = load_local_network(connection, bounds)
    load_ms = (perf_counter() - started) * 1000
    index = local.routing.integer
    if index is None:
        raise ValueError("Local graph has no integer index")
    midpoint = (bounds[0] + bounds[2]) / 2, (bounds[1] + bounds[3]) / 2
    start = min(range(len(index.node_ids)), key=lambda node: sum(
        (a - b) ** 2 for a, b in zip(indexed_node_point(local.routing.projected, node), midpoint)), default=None)
    started = perf_counter()
    distances = indexed_shortest_distances(local.routing, start, 1000) if start is not None else None
    search_ms = (perf_counter() - started) * 1000
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return {
        "bounds_epsg3067": bounds, "dataset_id": local.metadata.dataset_id,
        "feature_identity": local.metadata.feature_identity, "load_ms": load_ms,
        "nodes": len(index.node_ids), "segments": len(index.segment_ids), "arcs": len(index.arc_ids),
        "measurement_intervals": 0 if local.measurements is None else len(local.measurements.values),
        "frontier_nodes": len(local.frontier_nodes), "coverage_limited_segments": len(local.coverage_limited_segments),
        "search_start": None if start is None else index.node_ids[start],
        "search_budget_m": 1000, "search_ms": search_ms,
        "reached_nodes": 0 if distances is None else len(distances.reached),
        "reader_peak_rss_bytes": rss if sys.platform == "darwin" else rss * 1024,
        "note": "Fresh process; warm OS cache after export. Load includes open/query/packing. RSS includes imports and temporary decode buffers. Search starts at the nearest loaded node to box centre; it can hit unloaded connections. This is not an API or route-completeness benchmark.",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("database", type=Path)
    parser.add_argument("--bounds", type=float, nargs=4, required=True)
    args = parser.parse_args()
    print(json.dumps(probe_local_network(args.database, cast(Bounds, tuple(args.bounds)))))


if __name__ == "__main__":
    main()
