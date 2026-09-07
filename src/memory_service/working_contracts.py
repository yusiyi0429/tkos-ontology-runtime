"""Frozen v1 contracts for the six Working Memory object types.

The governed write service consumes these declarations directly.  Catalog tests also compare them
with the published DDL so runtime validation and database constraints cannot drift silently.

Field provenance (working.py @ v1):

- ``required_content`` : the non-empty content fields each ``create_*`` validates
  (Signal working.py:493, Issue form_issue, Judgment, Agreement, Mission, Close).
- ``unique_per_chain`` : the per-chain partial unique indexes (0006:83-87).
  Signal is the only type allowed multiple stable objects per chain.
- ``confirmation_mode`` : how a version reaches ``confirmation_status='confirmed'``:
    * ``never``                        — no code path confirms it (Signal)
    * ``in_place``                     — one human confirm_* flips the confirmation
                                          metadata (Judgment working.py:639, Close
                                          working.py:880)
    * ``on_transition``                — Issue: advance_issue_to_strategic /
                                          confirm_close insert already-confirmed versions
    * ``multi_party``                  — Agreement: confirm_agreement completes on the
                                          last party signature (working.py:769)
- ``requires_issue_id`` : identity row must anchor to the chain's stable Issue
  (Signal excludes it; Issue self-anchors its own id).
- ``carries_issue_state`` / ``ISSUE_STATES`` : only Issue versions carry
  ``issue_state`` (0006 ck_wm_issue_state_type); the tuple order is the
  forward-only transition order (potential → strategic → closed).
- ``exclusive_ref`` : the version-row reference column reserved for exactly one
  downstream type (Agreement → confirmed_judgment_record_id;
  Strategic Mission / Close → agreement_record_id).
- ``requires_confirmed_disposition`` : that reference must target a confirmed
  Agreement whose ``content.disposition`` equals the declared value
  (working.py _validate_confirmed_agreement_ref).
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

CONFIRMATION_NEVER = "never"
CONFIRMATION_IN_PLACE = "in_place"
CONFIRMATION_ON_TRANSITION = "on_transition"
CONFIRMATION_MULTI_PARTY = "multi_party"
CONFIRMATION_MODES = frozenset(
    {
        CONFIRMATION_NEVER,
        CONFIRMATION_IN_PLACE,
        CONFIRMATION_ON_TRANSITION,
        CONFIRMATION_MULTI_PARTY,
    }
)

CHAIN_STATUSES: tuple[str, ...] = ("open", "monitoring", "closed")
ISSUE_STATES: tuple[str, ...] = ("potential", "strategic", "closed")
CONFIRMATION_STATUSES: tuple[str, ...] = ("unconfirmed", "confirmed")
AGREEMENT_DISPOSITIONS: tuple[str, ...] = ("strategic_mission", "close")

# Canonical order: the sole hand-written enumeration point for the six types.
OBJECT_TYPE_ORDER: tuple[str, ...] = (
    "Signal", "Issue", "Judgment", "Agreement", "Strategic Mission", "Close",
)


@dataclass(frozen=True)
class WorkingObjectSpec:
    """One object type's v1 contract, frozen against service + DDL behavior."""

    object_type: str
    required_content: tuple[str, ...]
    unique_per_chain: bool
    confirmation_mode: str
    requires_issue_id: bool
    carries_issue_state: bool
    exclusive_ref: str | None = None
    requires_confirmed_disposition: str | None = None


def _spec(
    object_type: str,
    *,
    required_content: tuple[str, ...],
    confirmation_mode: str,
    unique_per_chain: bool = True,
    requires_issue_id: bool = True,
    carries_issue_state: bool = False,
    exclusive_ref: str | None = None,
    requires_confirmed_disposition: str | None = None,
) -> WorkingObjectSpec:
    return WorkingObjectSpec(
        object_type=object_type,
        required_content=required_content,
        unique_per_chain=unique_per_chain,
        confirmation_mode=confirmation_mode,
        requires_issue_id=requires_issue_id,
        carries_issue_state=carries_issue_state,
        exclusive_ref=exclusive_ref,
        requires_confirmed_disposition=requires_confirmed_disposition,
    )


SPECS: Mapping[str, WorkingObjectSpec] = MappingProxyType({
    item.object_type: item
    for item in (
        _spec(
            "Signal",
            required_content=("title", "description"),
            confirmation_mode=CONFIRMATION_NEVER,
            unique_per_chain=False,
            requires_issue_id=False,
        ),
        _spec(
            "Issue",
            required_content=("key_question",),
            confirmation_mode=CONFIRMATION_ON_TRANSITION,
            requires_issue_id=False,
            carries_issue_state=True,
        ),
        _spec(
            "Judgment",
            required_content=("statement", "responsible_party"),
            confirmation_mode=CONFIRMATION_IN_PLACE,
        ),
        _spec(
            "Agreement",
            required_content=("statement", "disposition"),
            confirmation_mode=CONFIRMATION_MULTI_PARTY,
            exclusive_ref="confirmed_judgment_record_id",
        ),
        _spec(
            "Strategic Mission",
            required_content=("title", "outcome", "owner", "boundary"),
            confirmation_mode=CONFIRMATION_NEVER,
            exclusive_ref="agreement_record_id",
            requires_confirmed_disposition="strategic_mission",
        ),
        _spec(
            "Close",
            required_content=("reason", "assumptions", "monitor", "reopen_condition"),
            confirmation_mode=CONFIRMATION_IN_PLACE,
            exclusive_ref="agreement_record_id",
            requires_confirmed_disposition="close",
        ),
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
