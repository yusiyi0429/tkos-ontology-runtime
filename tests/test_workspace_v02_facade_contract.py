"""Static contract checks for the local session facade over workspace/0.2.

No database or server; the real-HTTP behaviour lives in
``acceptance/workspace_v02/facade_sessions.py``.
"""
import uuid

import pytest

from memory_service_app.governance_commands import V02_HUMAN_EVENTS, parse
from memory_service_runtime.governed.errors import GovernedError
from memory_service_runtime.governed.workspace_v02_models import SourceContextView


def _context_body():
    return {'contract_version': 'tkos.workspace/0.2', 'scene_id': str(uuid.uuid4()),
            'idempotency_key': str(uuid.uuid4()), 'purpose': 'facade contract check',
            'items': [{'source_id': str(uuid.uuid4()), 'version_event_id': str(uuid.uuid4()),
                       'payload_hash': 'a' * 64}]}


def _scene_body(kind, event):
    return {'contract_version': 'tkos.workspace/0.2', 'scene_id': str(uuid.uuid4()),
            'expected_version': 1, 'idempotency_key': str(uuid.uuid4()),
            'event': {'kind': kind, **event}}


def test_context_save_is_classified_as_context_v02():
    _model, kind = parse(_context_body())
    assert kind == 'context_v02'


def test_source_scene_event_is_classified_as_scene_v02():
    _model, kind = parse(_scene_body('source_add', {
        'system': 'local-fixture', 'external_id': 'contract-source',
        'title': 'Contract source', 'media_type': 'text/plain',
        'acquired_at': '2026-01-01T00:00:00+00:00'}))
    assert kind == 'scene_v02'


def test_agent_only_events_stay_outside_the_human_facade():
    assert 'agent_run' not in V02_HUMAN_EVENTS
    assert 'source_version' in V02_HUMAN_EVENTS and 'source_share' in V02_HUMAN_EVENTS
    with pytest.raises(GovernedError) as exc:
        parse(_scene_body('agent_run', {}))
    assert exc.value.code == 'FORBIDDEN'


def test_facade_routes_and_top_level_context_shape_are_exposed():
    # The facade router is mounted by the dashboard host; check it directly so the
    # contract is testable without enabling the workbench settings.
    from memory_service_app.governance import router as facade_router
    routes = {route.path for route in facade_router.routes}
    for path in ('/dashboard/api/v1/session',
                 '/dashboard/api/v1/commands/prepare',
                 '/dashboard/api/v1/commands',
                 '/dashboard/api/v1/commands/{command_id}',
                 '/dashboard/api/v1/commands/{command_id}/commit',
                 '/dashboard/api/v1/commands/{command_id}/retry'):
        assert path in routes, path
    from memory_service_app.main import app
    assert '/v1/workspace-sources/contexts' in app.openapi()['paths']
    fields = SourceContextView.model_fields
    assert 'context_id' in fields and 'result' not in fields
