"""Native delivery and offline preparation must not load reference algorithms."""

import ast
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import textwrap
import unittest

from test_path_database import publish_fixture


class RoutingBoundaryTests(unittest.TestCase):
    def test_shared_and_native_modules_do_not_import_reference(self) -> None:
        package = Path(__file__).resolve().parents[1] / "src" / "runmap"
        for directory in (package / "routing", package / "paths"):
            for path in directory.glob("*.py"):
                for node in ast.walk(ast.parse(path.read_text())):
                    if isinstance(node, ast.ImportFrom):
                        modules = [node.module or ""]
                    elif isinstance(node, ast.Import):
                        modules = [alias.name for alias in node.names]
                    else:
                        continue
                    with self.subTest(path=path.name, line=node.lineno):
                        self.assertFalse(any(name.startswith("runmap.routing_reference") for name in modules))

    def test_native_app_and_exports_work_with_reference_imports_blocked(self) -> None:
        code = textwrap.dedent('''
            from collections.abc import Sequence
            from contextlib import closing
            from importlib.abc import MetaPathFinder
            from importlib.machinery import ModuleSpec
            from pathlib import Path
            import sys
            from types import ModuleType

            class RejectReference(MetaPathFinder):
                def find_spec(self, fullname: str, path: Sequence[str] | None = None,
                              target: ModuleType | None = None) -> ModuleSpec | None:
                    if fullname.startswith("runmap.routing_reference"):
                        raise ModuleNotFoundError(f"Native path imported {fullname}")
                    return None

            sys.meta_path.insert(0, RejectReference())

            from rasterio.warp import transform
            from runmap.api.app import app
            from runmap.api.path_tiles import PathTileService
            from runmap.api.path_shells import PathShellService
            from runmap.api.runs import EndpointRequest, PreferencesRequest, RunRequest, SettingsRequest
            from runmap.api.sqlite_data import SQLitePathData
            from runmap.api.sqlite_paths import database_inspection, database_inspect_outing
            from runmap.api.sqlite_runs import generate_database_run
            from runmap.cli import main

            source = Path(sys.argv[1])
            database = source.with_name("network.sqlite")
            main(["paths", "export-sqlite", str(source), str(database)])
            main(["paths", "export-numeric", str(database), str(source.with_name("numeric-routing-v2.sqlite"))])
            lon, lat = transform("EPSG:3067", "EPSG:4326", [375025, 375075], [6675000, 6675000])
            with closing(SQLitePathData(database)) as data:
                for mode in ("one_way", "round_trip"):
                    for hills in ("off", "high"):
                        request = RunRequest(dataset_id=data.dataset_id, mode=mode,
                            home=EndpointRequest(longitude=lon[0], latitude=lat[0]),
                            turnaround=EndpointRequest(longitude=lon[1], latitude=lat[1]),
                            settings=SettingsRequest(), preferences=PreferencesRequest(hills=hills))
                        response = generate_database_run(data, request)
                        assert response.status == "complete", response
                        assert all(len(c.legs) == (1 if mode == "one_way" else 2) for c in response.candidates)
                paths = PathTileService(data)
                prepared = paths.prepare(lon[0], lat[0], .1, "all", True, 50, None)
                snapshot = paths.snapshot(prepared["snapshot_id"])
                inspection = database_inspect_outing(data, database_inspection(data, "bc"),
                    prepared, snapshot, .1, "all", True)
                assert inspection["segment"]["id"] == "bc", inspection
                shells = PathShellService(paths)
                for method in ("grid", "triangulation"):
                    payload, _, _ = shells.render(snapshot, method)
                    assert payload
                assert not any(name.startswith("runmap.routing_reference") for name in sys.modules)
        ''')
        with TemporaryDirectory() as directory:
            source, _, _ = publish_fixture(Path(directory))
            result = subprocess.run([sys.executable, "-c", code, str(source)],
                                    capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
