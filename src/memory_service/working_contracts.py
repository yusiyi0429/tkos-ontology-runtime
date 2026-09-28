"""Frozen v1 contracts for the six Working Memory object types.

The governed write service consumes these declarations directly.

- ``required_content`` : the non-empty content fields each object type must carry; every create and
  version append checks them via ``working._validate_object_content``.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

AGREEMENT_DISPOSITIONS: tuple[str, ...] = ("strategic_mission", "close")


@dataclass(frozen=True)
class WorkingObjectSpec:
    """One object type's v1 contract, frozen against service behavior."""

    object_type: str
    required_content: tuple[str, ...]


def _spec(object_type: str, *, required_content: tuple[str, ...]) -> WorkingObjectSpec:
    return WorkingObjectSpec(object_type=object_type, required_content=required_content)


SPECS: Mapping[str, WorkingObjectSpec] = MappingProxyType({
    item.object_type: item
    for item in (
        _spec("Signal", required_content=("title", "description")),
        _spec("Issue", required_content=("key_question",)),
        _spec("Judgment", required_content=("statement", "responsible_party")),
        _spec("Agreement", required_content=("statement", "disposition")),
        _spec("Strategic Mission", required_content=("title", "outcome", "owner", "boundary")),
        _spec("Close", required_content=("reason", "assumptions", "monitor", "reopen_condition")),
    )
})

OBJECT_TYPES = frozenset(SPECS)


def spec(object_type: str) -> WorkingObjectSpec:
    """Return the frozen contract for one object type (KeyError on unknown)."""
    return SPECS[object_type]


def missing_required_content(object_type: str, content: Mapping[str, object] | None) -> tuple[str, ...]:
    """Return required fields whose persisted values are absent or blank strings."""
    if content is None:
        return spec(object_type).required_content
    missing: list[str] = []
    for field in spec(object_type).required_content:
        value = content.get(field)
        if value is None or (isinstance(value, str) and not value.strip()):
            missing.append(field)
    return tuple(missing)
