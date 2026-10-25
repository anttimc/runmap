"""Compare grid contours with fresh or reused Delaunay mesh contours."""

import argparse
from contextlib import closing
from dataclasses import asdict
import json
from pathlib import Path
from time import monotonic, perf_counter

from matplotlib.figure import Figure
from matplotlib.tri import Triangulation
import numpy as np
from scipy.spatial import Delaunay

from runmap.api.sqlite_data import SQLitePathData
from runmap.api.sqlite_exploration import ExplorationLimits, NumericExploration, prepare_local_exploration
from runmap.api.sqlite_paths import database_snap
from runmap.experiments.benchmark_distance_shells import shell_samples
from runmap.paths.exploration import Snap
from runmap.paths.numeric_database import open_numeric_database
from runmap.routing.distance_shells import (DistanceSamples, ShellSettings, build_distance_grid, distance_bands)
from runmap.routing.numeric_search import NumericEndpoint
from runmap.routing.profiles import exploration_profile
from runmap.routing.distance_mesh import mesh_sites


def measure_mesh(samples: DistanceSamples, maximum_m: float, settings: ShellSettings,
                 *, exact_sites: bool = False, boundary_window_m: float | None = None) -> dict[str, object]:
    started = perf_counter()
    sites = mesh_sites(samples, None if exact_sites else settings.cell_m, interval_m=settings.interval_m,
                       boundary_window_m=boundary_window_m, maximum_m=maximum_m)
    triangulate_started = perf_counter()
    delaunay = Delaunay(sites.xy)
    mask_started = perf_counter()
    vertices = sites.xy[delaunay.simplices]
    sides = np.linalg.norm(vertices - np.roll(vertices, 1, axis=1), axis=2)
    mask = sides.max(axis=1) > settings.radius_m * 2
    triangles = Triangulation(sites.xy[:, 0], sites.xy[:, 1], delaunay.simplices, mask=mask)
    contour_started = perf_counter()
    levels = np.r_[np.arange(0., maximum_m, settings.interval_m), maximum_m]
    axes = Figure().subplots()
    contours = axes.tricontourf(triangles, sites.distances, levels=levels)
    ended = perf_counter()
    # A second extraction isolates an already constructed mesh and connectivity.
    reused_started = perf_counter()
    reused = axes.tricontourf(triangles, sites.distances, levels=levels)
    reused_ended = perf_counter()
    result: dict[str, object] = {
        "site_reduction_ms": (triangulate_started - started) * 1000,
        "triangulation_ms": (mask_started - triangulate_started) * 1000,
        "triangle_mask_ms": (contour_started - mask_started) * 1000,
        "mesh_contours_ms": (ended - contour_started) * 1000,
        "mesh_build_contour_ms": (ended - started) * 1000,
        "reused_mesh_contours_ms": (reused_ended - reused_started) * 1000,
        "sites": len(sites.xy), "triangles": len(delaunay.simplices),
        "masked_triangles": int(mask.sum()),
        "mesh_contour_vertices": sum(len(path.vertices) for path in contours.get_paths()),
    }
    axes.figure.clear()
    del reused
    return result


def benchmark(database: Path, budgets: list[float], cell_m: float, radius_m: float,
              repeats: int, exact_sites: bool, boundary_window_m: float | None = None) -> dict[str, object]:
    settings = ShellSettings(cell_m=cell_m, radius_m=radius_m)
    rows: list[dict[str, object]] = []
    with closing(SQLitePathData(database)) as data, closing(
            open_numeric_database(database.with_name("numeric-routing-v2.sqlite"))) as connection:
        snap = Snap(**database_snap(data, 24.795, 60.216)["snap"])
        profile = data.metadata.models.profiles.index(exploration_profile())
        segment = connection.execute("SELECT number FROM identities WHERE kind='segment' AND external_id=?",
                                     (snap.segment_id,)).fetchone()[0]
        start = NumericEndpoint(segment_id=segment, graph_offset_m=snap.graph_offset_m,
                                chainage_m=snap.chainage_m)
        for budget in budgets:
            exploration = prepare_local_exploration(data, snap, budget, "all", True,
                                                    monotonic() + 30, ExplorationLimits())
            if not isinstance(exploration, NumericExploration):
                raise ValueError("Expected native exploration")
            for repeat in range(repeats):
                started = perf_counter()
                samples, stages = shell_samples(connection, exploration, start, profile, settings)
                samples_ms = (perf_counter() - started) * 1000
                grid_started = perf_counter()
                grid = build_distance_grid(samples, settings)
                contours_started = perf_counter()
                bands = distance_bands(grid, budget * 1000, settings)
                ended = perf_counter()
                mesh = measure_mesh(samples, budget * 1000, settings, exact_sites=exact_sites,
                                    boundary_window_m=boundary_window_m)
                rows.append({"budget_km": budget, "repeat": repeat, **stages, **mesh,
                             "grid_ms": (contours_started - grid_started) * 1000,
                             "grid_contours_ms": (ended - contours_started) * 1000,
                             "grid_build_contour_ms": (ended - grid_started) * 1000,
                             "grid_total_ms": samples_ms + (ended - grid_started) * 1000,
                             "mesh_total_ms": samples_ms + float(mesh["mesh_build_contour_ms"]),
                             "grid_contour_vertices": sum(len(ring) for band in bands
                                                          for polygon in band.polygons for ring in polygon)})
        return {"dataset_id": data.dataset_id, "start": asdict(snap), "settings": asdict(settings),
                "exact_sites": exact_sites, "boundary_window_m": boundary_window_m, "notes": [
                    "Same reachable network samples for both methods; regeneration on every repeat.",
                    "Cell-reduced mesh keeps the minimum-distance sample per occupied cell; exact-sites only merges duplicate coordinates.",
                    "Boundary reduction keeps normal cell resolution near thresholds and min/max anchors in four-times-coarser cells elsewhere.",
                    "Point reduction is approximate; it does not guarantee preservation of contours or network barriers.",
                    "Mesh masks triangles with any edge longer than twice the interpolation radius; no barrier constraints.",
                    "Grid and mesh have different interpolation/support semantics and are not equivalent outputs.",
                    "Totals exclude Dijkstra, GeoJSON serialization, HTTP, and browser rendering.",
                    "Reused mesh timing repeats contour extraction on the same distances; it is only an optimistic lower bound.",
                    "Matplotlib public tricontourf includes artist/axes setup; no image drawing is timed."], "rows": rows}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("database", type=Path)
    parser.add_argument("--budgets", type=float, nargs="+", default=[2, 5, 10, 20])
    parser.add_argument("--cell", type=float, default=50)
    parser.add_argument("--radius", type=float, default=150)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--exact-sites", action="store_true")
    parser.add_argument("--boundary-window", type=float)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    report = benchmark(args.database, args.budgets, args.cell, args.radius, args.repeats, args.exact_sites,
                       args.boundary_window)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2) + "\n")
    print(args.report)


if __name__ == "__main__":
    main()
