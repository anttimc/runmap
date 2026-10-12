"""CLI preparation must preserve provenance checks and offline workflows."""

from contextlib import redirect_stderr, redirect_stdout
import hashlib
from io import StringIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from runmap.cli import main
from runmap.paths.experiments import find_experiment
from test_routing import graph_document


EXPERIMENT_ID = "leppavaara-flat-10k"


class CliTests(unittest.TestCase):
    def test_dry_runs_do_not_acquire_sources(self) -> None:
        with TemporaryDirectory() as directory, patch("runmap.cli.download_highways") as osm, patch("runmap.cli.download_elevation") as elevation:
            config = Path(directory) / "crop.json"
            config.write_text('{"bounds": [374000, 6674000, 375000, 6675000]}')
            for argv in (["--config", str(config), "--dry-run"], ["region", "prepare", "leppavaara", "--dry-run"],
                         ["paths", "prepare", EXPERIMENT_ID, "--dry-run"]):
                with redirect_stdout(StringIO()) as output:
                    main(argv)
                self.assertTrue(output.getvalue())
            osm.assert_not_called()
            elevation.assert_not_called()

    def test_explicit_input_prepares_topology_and_records_checksum(self) -> None:
        document = {"elements": [
            {"type": "node", "id": 1, "lon": 24.80, "lat": 60.21},
            {"type": "node", "id": 2, "lon": 24.81, "lat": 60.21},
            {"type": "way", "id": 10, "nodes": [1, 2], "tags": {"highway": "path"}},
        ]}
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "input.json"
            source.write_text(json.dumps(document))
            with redirect_stdout(StringIO()) as output, patch("runmap.cli.download_highways") as download:
                main(["paths", "--data-root", str(root), "prepare", EXPERIMENT_ID,
                      "--input", str(source), "--without-terrain"])
            download.assert_not_called()
            graph = json.loads((root / "prepared" / EXPERIMENT_ID / "graph.json").read_text())
            self.assertEqual(graph["source"]["osm"]["sha256"], hashlib.sha256(source.read_bytes()).hexdigest())
            self.assertEqual(graph["summary"]["segment_count"], 1)
            self.assertNotIn("terrain", graph)
            self.assertIn("1 segments, 2 directed arcs", output.getvalue())

    def test_cached_source_requires_sidecar_and_matching_checksum(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "raw" / EXPERIMENT_ID / "osm-network-boundaries-none.json"
            source.parent.mkdir(parents=True)
            source.write_text('{"elements": []}')
            sidecar = source.with_suffix(".json.json")
            for expected in ("no provenance sidecar", "checksum does not match"):
                with redirect_stderr(StringIO()) as errors, self.assertRaises(SystemExit) as exit_info:
                    main(["paths", "--data-root", str(root), "prepare", EXPERIMENT_ID, "--without-terrain"])
                self.assertEqual(exit_info.exception.code, 2)
                self.assertIn(expected, errors.getvalue())
                sidecar.write_text('{"sha256": "incorrect"}')
            self.assertFalse((root / "prepared").exists())

    def test_stale_graph_is_rejected_before_route_outputs(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            graph_path = root / EXPERIMENT_ID / "graph.json"
            graph_path.parent.mkdir()
            graph_path.write_text('{"source": {"experiment_config_digest": "stale"}}')
            with redirect_stderr(StringIO()) as errors, self.assertRaises(SystemExit) as exit_info:
                main(["routes", "--graph-root", str(root), "--output-root", str(root / "output"),
                      "baseline", EXPERIMENT_ID])
            self.assertEqual(exit_info.exception.code, 2)
            self.assertIn("run paths prepare again", errors.getvalue())
            self.assertFalse((root / "output").exists())

    def test_baseline_writes_routes_and_reports_feasibility(self) -> None:
        graph = graph_document(
            [("s", 24, 60), ("a", 24.01, 60.01), ("b", 24.01, 59.99), ("d", 24.02, 60)],
            [("sa", "s", "a", 2500, 10, {"highway": "path"}),
             ("ad", "a", "d", 2500, 10, {"highway": "path"}),
             ("sb", "s", "b", 2500, 5, {"highway": "path"}),
             ("bd", "b", "d", 2500, 5, {"highway": "path"})],
        )
        experiment = find_experiment(Path("config/routing-experiments"), EXPERIMENT_ID)
        graph["source"] = {"experiment_config_digest": experiment.config_digest}
        with TemporaryDirectory() as directory:
            root = Path(directory)
            graph_path = root / EXPERIMENT_ID / "graph.json"
            graph_path.parent.mkdir()
            graph_path.write_text(json.dumps(graph))
            with redirect_stdout(StringIO()) as output:
                main(["routes", "--graph-root", str(root), "--output-root", str(root / "output"),
                      "baseline", EXPERIMENT_ID])
            result = json.loads((root / "output" / EXPERIMENT_ID / "diverse-baseline.json").read_text())
            self.assertTrue(result["alternatives"][0]["feasible"])
            self.assertIn("10.00 km", output.getvalue())
            self.assertTrue((root / "output" / EXPERIMENT_ID / "diverse-baseline.geojson").exists())
