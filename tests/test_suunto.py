"""Exercise duplicate prevention, durable recovery, and local export boundaries."""

from pathlib import Path
import asyncio
import json
import fcntl
import os
import shutil
import subprocess
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from uuid import uuid4

from fastapi import HTTPException
from starlette.requests import Request
from starlette.types import Message
from runmap.api.app import app

from runmap.api.suunto import (
    ImportRequest, SuuntoConfig, get_operation, import_route, integration_status,
    local_request, operation_lock, select_route, write_json,
)


def request(origin: str = "http://localhost:5173", action: str = "suunto",
            host: str = "localhost:8000", client: str = "127.0.0.1") -> Request:
    return Request({"type": "http", "scheme": "http", "method": "POST", "path": "/",
        "headers": [(b"host", host.encode()), (b"origin", origin.encode()),
                    (b"x-runmap-action", action.encode())],
        "server": ("127.0.0.1", 8000), "client": (client, 12345), "query_string": b""})


async def api_call(path: str, body: str | None = None, action: str = "suunto") -> tuple[int, str]:
    messages: list[Message] = []

    async def receive() -> Message:
        return {"type": "http.request", "body": (body or "").encode(), "more_body": False}

    async def send(message: Message) -> None:
        messages.append(message)

    scope = dict(request(action=action).scope)
    scope.update(path=path, raw_path=path.encode(), method="POST" if body is not None else "GET",
                 http_version="1.1", asgi={"version": "3.0"})
    scope["headers"] = [*scope["headers"], (b"content-type", b"application/json")]
    await app(scope, receive, send)
    code = next(message["status"] for message in messages if message["type"] == "http.response.start")
    content = b"".join(message.get("body", b"") for message in messages if message["type"] == "http.response.body")
    return code, content.decode()


class SuuntoTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.config = SuuntoConfig(self.root / "importer", self.root / "private", "node")
        self.body = ImportRequest(operation_id=uuid4(), name="Test route", gpx="<gpx/>")
        self.directory = self.config.storage / str(self.body.operation_id)
        self.config_patch = patch("runmap.api.suunto.configuration", return_value=self.config)
        self.config_patch.start()
        self.addCleanup(self.config_patch.stop)

    def completed(self, command: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        action, directory = command[-2:]
        write_json(Path(directory) / "receipt.json", {
            "state": "selected" if action == "select" else "created"})
        return subprocess.CompletedProcess(command, 0, b'{"error":null}', b"")

    def test_identical_submission_does_not_repeat_creation(self) -> None:
        with patch("runmap.api.suunto.subprocess.run", side_effect=self.completed) as run:
            first = import_route(self.body, request())
            second = import_route(self.body, request())
        self.assertEqual(first, second)
        self.assertEqual(first.state, "created")
        self.assertEqual(run.call_count, 1)
        self.assertTrue(run.call_args.kwargs["pass_fds"])
        self.assertEqual((self.directory / "route.gpx").stat().st_mode & 0o777, 0o600)
        self.assertEqual((self.directory / "operation.json").stat().st_mode & 0o777, 0o600)

    def test_http_registration_validation_and_export_contract(self) -> None:
        async def checks() -> None:
            prefix = "/api/integrations/suunto"
            code, content = await api_call(prefix)
            self.assertEqual((code, json.loads(content)), (200, {"configured": True}))
            code, _ = await api_call(f"{prefix}/imports", self.body.model_dump_json(), action="")
            self.assertEqual(code, 403)
            code, _ = await api_call(f"{prefix}/imports", '{}')
            self.assertEqual(code, 422)
            with patch("runmap.api.suunto.subprocess.run", side_effect=self.completed):
                code, content = await api_call(f"{prefix}/imports", self.body.model_dump_json())
            self.assertEqual(code, 200)
            self.assertEqual(json.loads(content)["state"], "created")
            code, content = await api_call(f"{prefix}/imports/{self.body.operation_id}")
            self.assertEqual(code, 200)
            self.assertEqual(json.loads(content)["name"], "Test route")

        asyncio.run(checks())

    def test_operation_id_cannot_be_reused_for_different_input(self) -> None:
        with patch("runmap.api.suunto.subprocess.run", side_effect=self.completed) as run:
            import_route(self.body, request())
            with self.assertRaises(HTTPException) as error:
                import_route(self.body.model_copy(update={"name": "Changed"}), request())
        self.assertEqual(error.exception.status_code, 409)
        self.assertEqual(run.call_count, 1)

    def test_timeout_preserves_uncertain_creation_without_retry(self) -> None:
        def timeout(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
            write_json(Path(command[-1]) / "receipt.json", {"state": "post-pending"})
            raise subprocess.TimeoutExpired(command, 90)

        with patch("runmap.api.suunto.subprocess.run", side_effect=timeout) as run:
            result = import_route(self.body, request())
            self.assertEqual(import_route(self.body, request()).state, "post-pending")
            self.assertEqual(run.call_count, 1)
        self.assertEqual((result.state, result.error), ("post-pending", "timeout"))
        with self.assertRaises(HTTPException):
            select_route(self.body.operation_id, request())

    def test_selection_recovery_never_recreates_route(self) -> None:
        with patch("runmap.api.suunto.subprocess.run", side_effect=self.completed) as run:
            import_route(self.body, request())
            write_json(self.directory / "receipt.json", {"state": "selection-pending"})
            self.assertEqual(select_route(self.body.operation_id, request()).state, "selected")
            select_route(self.body.operation_id, request())
        self.assertEqual([call.args[0][-2] for call in run.call_args_list], ["import", "select"])

    def test_status_recovers_receipt_after_restart_and_detects_running_process(self) -> None:
        with patch("runmap.api.suunto.subprocess.run", side_effect=self.completed):
            import_route(self.body, request())
        with operation_lock(self.directory) as acquired:
            self.assertIsNotNone(acquired)
            self.assertEqual(get_operation(self.body.operation_id, request()).state, "running")
            with self.assertRaises(HTTPException):
                import_route(self.body, request())
        self.assertEqual(get_operation(self.body.operation_id, request()).state, "created")

    @unittest.skipUnless(shutil.which("node"), "Node is required for inherited-lock validation")
    def test_node_retains_lock_after_parent_closes_descriptor(self) -> None:
        self.directory.mkdir(parents=True)
        descriptor = os.open(self.directory / "lock", os.O_CREAT | os.O_RDWR, 0o600)
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        process = subprocess.Popen(
            ["node", "-e", "console.log('ready'); setTimeout(() => {}, 10000)"],
            stdout=subprocess.PIPE, pass_fds=(descriptor,),
        )
        os.close(descriptor)
        try:
            assert process.stdout is not None
            self.assertEqual(process.stdout.readline().strip(), b"ready")
            with operation_lock(self.directory) as acquired:
                self.assertIsNone(acquired)
        finally:
            process.terminate()
            process.communicate(timeout=5)
        with operation_lock(self.directory) as acquired:
            self.assertIsNotNone(acquired)

    def test_failures_do_not_expose_process_output(self) -> None:
        result = subprocess.CompletedProcess([], 1, b"secret session token", b"password")
        with patch("runmap.api.suunto.subprocess.run", return_value=result):
            status = import_route(self.body, request())
        self.assertEqual(status.error, "installation")
        self.assertNotIn("secret", status.model_dump_json())

    def test_local_origin_and_action_header_are_required(self) -> None:
        local_request(request(), write=True)
        for candidate in [request(origin="https://evil.example"), request(action=""),
                          request(host="evil.example:8000"), request(client="192.0.2.1")]:
            with self.subTest(candidate=candidate), self.assertRaises(HTTPException):
                local_request(candidate, write=True)

    def test_disabled_configuration_and_input_validation_do_not_launch_node(self) -> None:
        with patch("runmap.api.suunto.configuration", return_value=None):
            self.assertFalse(integration_status(request())["configured"])
            with self.assertRaises(HTTPException):
                import_route(self.body, request())
        for body in [self.body.model_copy(update={"name": " "}),
                     self.body.model_copy(update={"gpx": "é" * (1024 * 1024 + 1)})]:
            with self.assertRaises(HTTPException), patch("runmap.api.suunto.subprocess.run") as run:
                import_route(body, request())
            run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
