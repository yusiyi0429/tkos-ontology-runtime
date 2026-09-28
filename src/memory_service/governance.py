"""Error types for editing governed Context Graph proposals.

Proposal creation and confirm/reject are owned by the Context Graph write service
(``context_graph.service``); ``queries`` raises these errors for pending-content edits.
"""
from __future__ import annotations

from memory_service.context_graph.service import (
    ContextGraphError,
    GraphInvariantError,
)

GovernanceError = ContextGraphError


class ProposalNotFoundError(GovernanceError):
    """A proposal requested for editing does not exist."""


class ProposalAlreadyDecidedError(GovernanceError):
    """A proposal requested for editing is no longer pending."""


class InvalidProposalContentError(GraphInvariantError):
    """Edited proposal content violates the current graph contract."""


__all__ = [
    "GovernanceError",
    "InvalidProposalContentError",
    "ProposalAlreadyDecidedError",
    "ProposalNotFoundError",
]
