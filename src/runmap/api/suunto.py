"""Optional local Suunto exports with durable receipts and explicit recovery."""

from contextlib import contextmanager
from dataclasses import dataclass
from hashlib import sha256
import fcntl
import json
import os
from pathlib import Path
import subprocess
from typing import Iterator, Literal
from urllib.parse import urlsplit
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parents[3]
MAX_GPX_BYTES = 2 * 1024 * 1024


def private_response(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"


router = APIRouter(prefix="/api/integrations/suunto", tags=["suunto"],
                   dependencies=[Depends(private_response)])


@dataclass(frozen=True)
class SuuntoConfig:
    installation: Path
    storage: Path
    node: str


class ImportRequest(BaseModel):
    operation_id: UUID
    name: str = Field(min_length=1, max_length=200)
    gpx: str = Field(min_length=1, max_length=MAX_GPX_BYTES)
    use_in_watch: bool = True


class Operation(BaseModel):
    id: str
    name: str
    use_in_watch: bool
    state: Literal["running", "failed", "post-pending", "created-unverified",
                   "created", "selection-pending", "selected"]
    error: str | None = None


def configuration() -> SuuntoConfig | None:
    installation = os.environ.get("RUNMAP_SUUNTO_IMPORTER")
    if not installation:
        return None
    return SuuntoConfig(Path(installation).expanduser().resolve(),
        Path(os.environ.get("RUNMAP_SUUNTO_STORAGE", ROOT / "data/private/suunto")).resolve(),
        os.environ.get("RUNMAP_SUUNTO_NODE", "node"))


def require_config() -> SuuntoConfig:
    config = configuration()
    if config is None:
        raise HTTPException(503, "Suunto integration is not configured.")
    return config


def local_request(request: Request, *, write: bool = False) -> None:
    """Keep a single-account credential bridge confined to local browser use."""
    allowed_hosts = {"localhost", "127.0.0.1", "::1"}
    if request.url.hostname not in allowed_hosts or (
        request.client and request.client.host not in allowed_hosts
    ):
        raise HTTPException(403, "Suunto integration requires local access.")
    origin = request.headers.get("origin")
    if origin:
        parsed = urlsplit(origin)
        if parsed.scheme != "http" or parsed.netloc not in {
            request.url.netloc, "localhost:5173", "127.0.0.1:5173", "[::1]:5173"
        }:
            raise HTTPException(403, "Untrusted request origin.")
    if write and request.headers.get("x-runmap-action") != "suunto":
        raise HTTPException(403, "Missing Suunto action header.")


@contextmanager
def operation_lock(directory: Path) -> Iterator[int | None]:
    with (directory / "lock").open("a", encoding="utf-8") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield None
            return
        try:
            yield handle.fileno()
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def write_json(path: Path, value: dict[str, str | bool | None]) -> None:
    temporary = path.with_name(f"{path.name}.{uuid4()}.tmp")
    with temporary.open("x", encoding="utf-8") as handle:
        os.chmod(temporary, 0o600)
        json.dump(value, handle)
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(path)


def operation_status(directory: Path, *, running: bool = False) -> Operation:
    state = "running" if running else "failed"
    if not running and (directory / "receipt.json").exists():
        receipt = json.loads((directory / "receipt.json").read_text())
        state = receipt["state"]
    error = None
    if not running and (directory / "result.json").exists():
        error = json.loads((directory / "result.json").read_text()).get("error")
    metadata = json.loads((directory / "operation.json").read_text())
    return Operation(id=directory.name, name=metadata["name"],
                     use_in_watch=metadata["use_in_watch"], state=state, error=error)


def run_operation(config: SuuntoConfig, directory: Path, action: str, lock_fd: int) -> Operation:
    """Never retry a network write; the importer receipt determines recovery."""
    write_json(directory / "result.json", {"error": None})
    loader = config.installation / "node_modules/tsx/dist/loader.mjs"
    command = [config.node, f"--env-file={config.installation / '.env'}",
        "--import", str(loader), str(ROOT / "integrations/suunto/runner.mjs"),
        str(config.installation), action, str(directory)]
    try:
        result = subprocess.run(command, cwd=config.installation, capture_output=True,
                                timeout=90, check=False, pass_fds=(lock_fd,))
        output = json.loads(result.stdout)
        error = output.get("error")
        allowed = {"installation", "validation", "authentication", "creation", "selection",
                   "receipt", "create"}
        if (error is not None and (not isinstance(error, str) or error not in allowed)) or (
            result.returncode and error is None
        ):
            error = "installation"
    except subprocess.TimeoutExpired:
        error = "timeout"
    except (OSError, ValueError, AttributeError):
        error = "installation"
    write_json(directory / "result.json", {"error": error})
    return operation_status(directory)


@router.get("")
def integration_status(request: Request) -> dict[str, bool]:
    local_request(request)
    return {"configured": configuration() is not None}


@router.post("/imports")
def import_route(body: ImportRequest, request: Request) -> Operation:
    local_request(request, write=True)
    config = require_config()
    if not body.name.strip() or len(body.gpx.encode()) > MAX_GPX_BYTES:
        raise HTTPException(422, "Invalid route name or GPX exceeds 2 MiB.")
    fingerprint = sha256(json.dumps([body.name, body.gpx, body.use_in_watch]).encode()).hexdigest()
    config.storage.mkdir(parents=True, exist_ok=True, mode=0o700)
    directory = config.storage / str(body.operation_id)
    directory.mkdir(exist_ok=True, mode=0o700)
    with operation_lock(directory) as acquired:
        if acquired is None:
            raise HTTPException(409, "Export is already running. Retrieve its status.")
        metadata = directory / "operation.json"
        if metadata.exists():
            previous = json.loads(metadata.read_text())
            if previous["fingerprint"] != fingerprint:
                raise HTTPException(409, "Operation ID already belongs to a different export.")
            return operation_status(directory)
        write_json(metadata, {"fingerprint": fingerprint, "name": body.name,
                              "use_in_watch": body.use_in_watch})
        route = directory / "route.gpx"
        with route.open("x", encoding="utf-8") as handle:
            os.chmod(route, 0o600)
            handle.write(body.gpx)
        return run_operation(config, directory, "import", acquired)


def existing_directory(config: SuuntoConfig, operation_id: UUID) -> Path:
    directory = config.storage / str(operation_id)
    if not (directory / "operation.json").is_file():
        raise HTTPException(404, "Suunto export was not found.")
    return directory


@router.get("/imports/{operation_id}")
def get_operation(operation_id: UUID, request: Request) -> Operation:
    local_request(request)
    directory = existing_directory(require_config(), operation_id)
    with operation_lock(directory) as acquired:
        return operation_status(directory, running=acquired is None)


@router.post("/imports/{operation_id}/select")
def select_route(operation_id: UUID, request: Request) -> Operation:
    local_request(request, write=True)
    config = require_config()
    directory = existing_directory(config, operation_id)
    with operation_lock(directory) as acquired:
        if acquired is None:
            raise HTTPException(409, "Export is already running. Retrieve its status.")
        status = operation_status(directory)
        if status.state == "selected":
            return status
        if status.state not in {"created", "selection-pending"}:
            raise HTTPException(409, "Creation must be verified before watch selection.")
        return run_operation(config, directory, "select", acquired)
