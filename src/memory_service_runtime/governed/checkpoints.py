"""No-op instrumentation hooks; no HTTP-controlled bypass belongs here."""
from typing import Any


def checkpoint(name: str, context: dict[str, Any] | None = None) -> None:
    """Acceptance wrappers may replace this hook without altering business rules."""
