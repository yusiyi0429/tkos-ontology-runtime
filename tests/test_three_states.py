"""Structural tests for the adapter's 404/503/422/403/409 mapping."""
from __future__ import annotations

import psycopg
from fastapi import APIRouter, FastAPI

from adapter.codes import UnknownCodeError
from adapter.errors import EmbeddingUnavailableError, mapped, status_for_exception
from memory_service import actors, working
from tests.asgi_client import get_json as _request
from memory_service.context_graph.query_contracts import (
    BudgetValidationError,
    EmbeddingValidationError,
    NoCurrentGenerationError,
    QueryError,
    RetrievalModeError,
    SourceResolutionError,
)


EXPECTED: dict[type[BaseException], int] = {
    psycopg.OperationalError: 503,
    psycopg.InterfaceError: 503,
    EmbeddingUnavailableError: 503,
    EmbeddingValidationError: 503,
    UnknownCodeError: 404,
    working.ChainNotFoundError: 404,
    working.ObjectNotFoundError: 404,
    working.VersionNotFoundError: 404,
    working.NotHumanApproverError: 403,
    working.NotAPartyError: 403,
    working.ActorOutOfScopeError: 403,
    actors.HumanActorRequiredError: 403,
    working.AlreadyConfirmedError: 409,
    working.AlreadyConfirmedByPartyError: 409,
    working.ChainTitleConflictError: 409,
    working.IssueStateTransitionError: 409,
    working.NotConfirmedError: 409,
    working.SourceRefImmutableError: 409,
    working.SourceRefDriftError: 409,
    SourceResolutionError: 409,
    actors.ActorRequiredError: 422,
    working.FormationError: 422,
    working.InvalidObjectTypeError: 422,
    working.InvalidDispositionError: 422,
    working.EmptyPartiesError: 422,
    working.CrossChainReferenceError: 422,
    working.DispositionMismatchError: 422,
    working.InvalidSourceRefError: 422,
    RetrievalModeError: 422,
    BudgetValidationError: 422,
}


def _app_for_mapping() -> FastAPI:
    app = FastAPI()
    router = APIRouter()

    for exception_type in EXPECTED:
        path = "/boom/" + exception_type.__name__

        def make_endpoint(exc_type: type[BaseException]):
            @mapped
            def endpoint() -> None:
                if exc_type is SourceResolutionError:
                    raise exc_type([])
                raise exc_type("mapping test")

            return endpoint

        router.add_api_route(path, make_endpoint(exception_type), methods=["GET"])

    @router.get("/boom/WorkingMemoryError")
    @mapped
    def unmapped_base() -> None:
        raise working.WorkingMemoryError("unmapped base")

    @router.get("/boom/QueryError")
    @mapped
    def unmapped_query_base() -> None:
        raise QueryError("unmapped query base")

    app.include_router(router)
    return app


def test_mapping_covers_every_working_exception() -> None:
    declared = {
        cls
        for cls in working.WorkingMemoryError.__subclasses__()
        if cls.__module__ == working.__name__
    }
    mapped_working = {cls for cls in EXPECTED if cls.__module__ == working.__name__}
    assert mapped_working == declared


def test_fake_routes_preserve_three_states() -> None:
    application = _app_for_mapping()
    for exception_type, expected_status in EXPECTED.items():
        status, body = _request(application, "/boom/" + exception_type.__name__)
        assert status == expected_status, exception_type.__name__
        if exception_type is SourceResolutionError:
            assert body["detail"].startswith("selected owners have unresolvable source refs")
        else:
            assert body["detail"] == "mapping test"


def test_operational_error_is_503_not_404() -> None:
    assert status_for_exception(psycopg.OperationalError("database down")) == 503
    assert status_for_exception(psycopg.OperationalError("database down")) != 404
    assert status_for_exception(working.ChainNotFoundError("missing")) == 404
    assert status_for_exception(NoCurrentGenerationError("empty graph")) == 500


def test_unmapped_bases_fail_loudly_as_500() -> None:
    application = _app_for_mapping()
    status, body = _request(application, "/boom/WorkingMemoryError")
    assert status == 500
    assert status != 404
    assert body["detail"] == "unmapped base"

    status, body = _request(application, "/boom/QueryError")
    assert status == 500
    assert status != 404
    assert body["detail"] == "unmapped query base"
