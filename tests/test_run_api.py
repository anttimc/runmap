"""Run API validation, snapping and bounded-work outcomes."""

from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from pydantic import ValidationError
from fastapi import HTTPException
from starlette.requests import Request

from test_path_api import fixture
from runmap.api.paths import load_path_data
from runmap.api.app import run_route
from runmap.api.runs import PlannerConfiguration, PreferencesRequest, RunRequest, RunResponse, generate_run
from runmap.paths.graph import write_graph_outputs


class RunApiTests(unittest.TestCase):
    def setUp(self) -> None:
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        graph = fixture()
        graph["segments"][0]["backward"] = True
        graph["arcs"].append({"id": "s:b", "segment_id": "s", "from": "2", "to": "1", "length_m": 220})
        paths = write_graph_outputs(graph, directory.name)
        self.data = load_path_data(paths["graph"])
        self.options = RunRequest(dataset_id=self.data.dataset_id,
                                  home={"longitude": 24.801, "latitude": 60.2},
                                  turnaround={"longitude": 24.803, "latitude": 60.2})

    def test_round_trip_serializes_partial_physical_traversals(self) -> None:
        result = generate_run(self.data, self.options)
        self.assertEqual(result.status, "complete")
        self.assertEqual(len(result.candidates), 2)
        candidate = result.candidates[0]
        self.assertAlmostEqual(candidate.distance_m, 220, places=2)
        self.assertAlmostEqual(candidate.repeated_distance_m, 110, places=2)
        self.assertEqual(candidate.outbound.traversals[0].segment_id, "s")
        self.assertAlmostEqual(candidate.outbound.traversals[0].start_m, 55, places=2)
        self.assertAlmostEqual(candidate.returning.traversals[0].end_m, 55, places=2)
        self.assertEqual(result.candidates[1].equivalent_to, 0)
        self.assertIn('"coordinates"', result.model_dump_json())
        self.assertEqual(result.cost_model.units, "equivalent_metres")
        self.assertEqual(result.cost_model.preferences.settings.hills, "off")
        self.assertEqual(result.cost_model.preferences.weights.hills, 0)
        for leg in (candidate.outbound, candidate.returning):
            self.assertAlmostEqual(leg.cost_breakdown.total, leg.cost)
            self.assertAlmostEqual(leg.cost_breakdown.distance_m, leg.distance_m)
            self.assertIsNone(leg.observations.signal_passages)
            self.assertIsNone(leg.observations.busy_road_exposure_m)
            self.assertIsNone(leg.observations.green_context_distance_m)
        self.assertEqual(RunResponse.model_validate_json(result.model_dump_json()), result)
        self.assertEqual(candidate.outbound.cost_breakdown.reuse, 0)
        self.assertGreater(candidate.returning.cost_breakdown.reuse, 0)

    def test_missing_snap_is_explicit(self) -> None:
        options = self.options.model_copy(update={"home": self.options.home.model_copy(update={"longitude": 20})})
        result = generate_run(self.data, options)
        self.assertEqual(result.status, "no_eligible_snap")
        self.assertIsNone(result.home)
        self.assertEqual(result.cost_model.preferences.settings.hills, "off")

    def test_deadline_does_not_claim_unreachable(self) -> None:
        with patch("runmap.api.runs.plan_run", side_effect=TimeoutError):
            result = generate_run(self.data, self.options)
        self.assertEqual(result.status, "deadline")

    def test_busy_planner_rejects_excess_work(self) -> None:
        with patch("runmap.api.runs.RUN_SLOTS") as slots:
            slots.acquire.return_value = False
            with self.assertRaises(HTTPException) as error:
                generate_run(self.data, self.options)
            self.assertEqual(error.exception.status_code, 429)

    def test_invalid_inputs_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            RunRequest.model_validate({**self.options.model_dump(), "settings": {"avoidance_ratio": float("nan")}})
        options = self.options.model_copy(update={"turnaround": self.options.home})
        with self.assertRaises(ValueError):
            generate_run(self.data, options)

    def test_preference_capabilities_do_not_advertise_unimplemented_levels(self) -> None:
        config = PlannerConfiguration(deadline_s=15).model_dump(mode="json")
        self.assertEqual(config["preference_defaults"], {name: "off" for name in ("hills", "signals", "busy_roads", "greenery")})
        for capability in config["preference_capabilities"].values():
            self.assertEqual(capability["supported_levels"], ["off"])
            self.assertIsNone(capability["coverage_fraction"])
            self.assertIsNone(capability["feature_version"])
            self.assertTrue(capability["unavailable_reason"])

    def test_unsupported_preferences_fail_even_when_no_endpoint_would_snap(self) -> None:
        options = self.options.model_copy(update={"preferences": PreferencesRequest(signals="high"),
                                                  "home": self.options.home.model_copy(update={"longitude": 20})})
        with patch("runmap.api.runs.snap_candidates", side_effect=AssertionError):
            with self.assertRaisesRegex(ValueError, "Unsupported signals"):
                generate_run(self.data, options)
        for preferences in ({"hills": "maximum"}, {"unknown": "low"}):
            with self.assertRaises(ValidationError):
                RunRequest.model_validate({**self.options.model_dump(), "preferences": preferences})

    def test_explicit_all_off_preserves_existing_requests(self) -> None:
        implicit = generate_run(self.data, self.options)
        explicit = generate_run(self.data, self.options.model_copy(update={"preferences": PreferencesRequest()}))
        self.assertEqual(implicit.candidates, explicit.candidates)
        self.assertEqual(implicit.cost_model, explicit.cost_model)

    def test_route_returns_http_400_for_unsupported_preference(self) -> None:
        options = self.options.model_copy(update={"preferences": PreferencesRequest(hills="low")})
        with patch("runmap.api.app._path_data", return_value=self.data):
            with self.assertRaises(HTTPException) as error:
                run_route(Request({"type": "http"}), options)
        self.assertEqual(error.exception.status_code, 400)
        self.assertIn("Unsupported hills", error.exception.detail)
