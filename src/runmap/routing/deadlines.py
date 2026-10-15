"""Shared deadline guard for request preparation and routing work."""

from time import monotonic


def check_deadline(deadline: float | None) -> None:
    if deadline is not None and monotonic() >= deadline:
        raise TimeoutError("Run planning exceeded its deadline")
