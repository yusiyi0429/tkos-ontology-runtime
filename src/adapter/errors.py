"""Translate memory_service failures to the adapter's HTTP contract.

The 404/503 distinction is intentional and must not be weakened:
404 means an anchor/record is absent and lets clark choose ``external_only``;
503 means the service is unavailable and must be surfaced as an outage.
"""
from __future__ import annotations

import functools
import inspect
from collections.abc import Callable
from typing import Any, TypeVar, cast

import psycopg
from fastapi import HTTPException

from memory_service import actors, working
from memory_service.context_graph.query_contracts import (
    BudgetValidationError,
    EmbeddingValidationError,
    RetrievalModeError,
    SourceResolutionError,
)

from adapter.codes import UnknownCodeError


class EmbeddingUnavailableError(RuntimeError):
    """The embedding gateway could not serve a request (always HTTP 503)."""


# Mapping table, deliberately ordered by specificity.  In particular,
# HumanActorRequiredError is an ActorRequiredError subclass and is checked first.
#
# 503: DB connectivity (psycopg OperationalError/InterfaceError) and embedding
#      gateway failures.
# 404: ChainNotFoundError/ObjectNotFoundError/VersionNotFoundError.  The
#      no-current-generation query branch is handled by the dynamic route.
# 403: a non-human actor or an actor without permission for the operation.
# 409: state conflicts and source-hash fail-closed (SourceRefDriftError).
# 422: malformed/invalid operation contracts.
# Any unmapped exception, including WorkingMemoryError itself, remains 500 rather
# than being mistaken for a missing anchor.
_UNAVAILABLE: tuple[type[BaseException], ...] = (
    psycopg.OperationalError,
    psycopg.InterfaceError,
    EmbeddingUnavailableError,
    EmbeddingValidationError,
)
_NOT_FOUND: tuple[type[BaseException], ...] = (
    UnknownCodeError,
    working.ChainNotFoundError,
    working.ObjectNotFoundError,
    working.VersionNotFoundError,
)
_FORBIDDEN: tuple[type[BaseException], ...] = (
    actors.HumanActorRequiredError,
    working.NotHumanApproverError,
    working.NotAPartyError,
    working.ActorOutOfScopeError,
)
_CONFLICT: tuple[type[BaseException], ...] = (
    SourceResolutionError,
    working.AlreadyConfirmedError,
    working.AlreadyConfirmedByPartyError,
    working.ChainTitleConflictError,
    working.IssueStateTransitionError,
    working.NotConfirmedError,
    working.SourceRefImmutableError,
    working.SourceRefDriftError,
)
_UNPROCESSABLE: tuple[type[BaseException], ...] = (
    RetrievalModeError,
    BudgetValidationError,
    actors.ActorRequiredError,
    working.FormationError,
    working.InvalidObjectTypeError,
    working.InvalidDispositionError,
    working.EmptyPartiesError,
    working.CrossChainReferenceError,
    working.DispositionMismatchError,
    working.InvalidSourceRefError,
)

_MAPPING: tuple[tuple[tuple[type[BaseException], ...], int], ...] = (
    (_UNAVAILABLE, 503),
    (_NOT_FOUND, 404),
    (_FORBIDDEN, 403),
    (_CONFLICT, 409),
    (_UNPROCESSABLE, 422),
)


def status_for_exception(exc: BaseException) -> int:
    """Return the HTTP status for ``exc`` (500 when no contract row matches)."""
    for exception_types, status in _MAPPING:
        if isinstance(exc, exception_types):
            return status
    return 500


def map_exception(exc: BaseException) -> HTTPException:
    """Build a JSON-compatible FastAPI exception without changing its status semantics."""
    if isinstance(exc, HTTPException):
        return exc
    return HTTPException(status_code=status_for_exception(exc), detail=str(exc) or exc.__class__.__name__)


F = TypeVar("F", bound=Callable[..., Any])


def mapped(fn: F) -> F:
    """Decorator for endpoint bodies; dependencies may raise HTTPException directly."""
    if inspect.iscoroutinefunction(fn):

        @functools.wraps(fn)
        async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
            try:
                return await fn(*args, **kwargs)
            except HTTPException:
                raise
            except Exception as exc:
                raise map_exception(exc) from exc

        return cast(F, async_wrapper)

    @functools.wraps(fn)
    def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return fn(*args, **kwargs)
        except HTTPException:
            raise
        except Exception as exc:
            raise map_exception(exc) from exc

    return cast(F, sync_wrapper)
