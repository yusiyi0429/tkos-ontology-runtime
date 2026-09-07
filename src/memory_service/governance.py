"""Public confirmation boundary for governed Context Graph proposals.

Proposal creation is owned by the Context Graph write service and higher-level ingestion
interfaces.  This module keeps the host-facing confirm/reject seam small while ensuring that
all semantic writes still pass through a human decision.
"""
from __future__ import annotations

from typing import Any, Callable

from memory_service.context_graph.service import (
    ContextGraphError,
    GraphInvariantError,
    NotHumanConfirmerError,
    ProposalConflictError,
    confirm as confirm_context_graph,
    reject as reject_context_graph,
)

GovernanceError = ContextGraphError
NotHumanApproverError = NotHumanConfirmerError
RevisionConflictError = ProposalConflictError


class ProposalNotFoundError(GovernanceError):
    """A proposal requested for editing does not exist."""


class ProposalAlreadyDecidedError(GovernanceError):
    """A proposal requested for editing is no longer pending."""


class InvalidProposalContentError(GraphInvariantError):
    """Edited proposal content violates the current graph contract."""


def confirm(
    connect_factory: Callable[..., Any],
    proposal_id: str,
    decided_by: str,
    decision_note: str | None = None,
    *,
    gateway: Any,
) -> dict[str, Any]:
    """Confirm a pending proposal through the governed Context Graph write service."""
    return confirm_context_graph(
        proposal_id,
        decided_by=decided_by,
        decision_note=decision_note,
        gateway=gateway,
        _connect=connect_factory,
    )


def reject(
    connect_factory: Callable[..., Any],
    proposal_id: str,
    decided_by: str,
    decision_note: str | None = None,
) -> dict[str, Any]:
    """Reject a pending proposal after validating a human decision actor."""
    return reject_context_graph(
        proposal_id,
        decided_by=decided_by,
        decision_note=decision_note,
        _connect=connect_factory,
    )


__all__ = [
    "GovernanceError",
    "InvalidProposalContentError",
    "NotHumanApproverError",
    "ProposalAlreadyDecidedError",
    "ProposalNotFoundError",
    "RevisionConflictError",
    "confirm",
    "reject",
]
