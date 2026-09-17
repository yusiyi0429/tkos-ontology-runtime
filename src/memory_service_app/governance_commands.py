"""Single-instance, durable original-envelope journal. Receipts remain authoritative."""
from __future__ import annotations

from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import tempfile
import uuid

from pydantic import ValidationError

from memory_service_runtime.governed import db, method_access, method_readers, service, workspace_service, dashboard
from memory_service_runtime.governed.governance import HUMAN_ACTIONS
from memory_service_runtime.governed.models import ActionRequest
from memory_service_runtime.governed.workspace_models import WorkspaceCommand
from memory_service_runtime.governed.errors import GovernedError
from .settings import get_settings
from .governance_sessions import private_file

# Strict version-specific human allowlists.  Agent-only drafting, scene Agent
# runs and every other action stay outside the session facade.
V02_HUMAN_EVENTS = frozenset({
    'scene_create', 'source_add', 'source_version', 'source_correct',
    'source_withdraw', 'source_share', 'source_unshare', 'followup_draft',
    'draft_decision',
})

def v04_human_actions():
    from memory_service_runtime.governed.method_v04_models import HUMAN_ACTIONS as v04
    return v04


def root():
    value = get_settings().tkos_governance_commands_dir
    if not value:
        raise GovernedError('WORKBENCH_NOT_CONFIGURED', status=503)
    path = Path(value)
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    if path.is_symlink() or path.stat().st_mode & 0o077:
        raise GovernedError('WORKBENCH_NOT_CONFIGURED', status=503)
    return path


def write(path, data):
    fd, name = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(data, stream, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(name):
            os.unlink(name)


@contextmanager
def lock(command_id):
    command_id = str(uuid.UUID(str(command_id)))
    fd = os.open(root() / (command_id + '.lock'), os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield root() / (command_id + '.json')
    finally:
        os.close(fd)


def parse(body):
    if not isinstance(body, dict):
        raise GovernedError('INVALID_REQUEST')
    version = body.get('contract_version')
    if version == 'tkos.workspace/0.1':
        try:
            model = WorkspaceCommand.model_validate(body)
        except ValidationError:
            raise GovernedError('INVALID_REQUEST') from None
        if model.event.kind not in {'create', 'comment_anchor', 'diff_response'}:
            raise GovernedError('FORBIDDEN')
        if model.event.kind == 'create' and model.event.scene_type != 'monthly':
            raise GovernedError('FORBIDDEN')
        return model, 'scene'
    if version == 'tkos.workspace/0.2':
        from memory_service_runtime.governed.workspace_v02_models import SourceContextCreate, SourceSceneCommand
        if isinstance(body.get('event'), dict):
            event = body['event']
            if 'kind' not in event:
                raise GovernedError('INVALID_REQUEST')
            # Kind is checked before payload parsing: an Agent run or any other
            # non-human event is FORBIDDEN, never a misleading schema error.
            if event['kind'] not in V02_HUMAN_EVENTS:
                raise GovernedError('FORBIDDEN')
            try:
                model = SourceSceneCommand.model_validate(body)
            except ValidationError:
                raise GovernedError('INVALID_REQUEST') from None
            return model, 'scene_v02'
        # Context snapshots are a separate typed write with their own key.
        try:
            model = SourceContextCreate.model_validate(body)
        except ValidationError:
            raise GovernedError('INVALID_REQUEST') from None
        return model, 'context_v02'
    action = body.get('action_type')
    if version == 'tkos.method/0.3':
        if action not in HUMAN_ACTIONS:
            raise GovernedError('FORBIDDEN')
    elif version == 'tkos.method/0.4':
        if action not in v04_human_actions():
            raise GovernedError('FORBIDDEN')
    else:
        raise GovernedError('FORBIDDEN')
    try:
        model = ActionRequest.model_validate(body)
    except ValidationError:
        raise GovernedError('INVALID_REQUEST') from None
    return model, 'method'


def scene_check(conn, ctx, model):
    event = model.event.model_dump(mode='json', exclude_none=False)
    history = workspace_service.rows(conn, ctx, str(model.scene_id))
    if event['kind'] == 'create':
        if history:
            raise GovernedError('VERSION_CONFLICT')
        head = workspace_service.validate_create(conn, ctx, event)
    else:
        seed, head = workspace_service.authorize_scene(conn, ctx, history)
        if len(history) != model.expected_version:
            raise GovernedError('VERSION_CONFLICT')
        workspace_service.validate_event(conn, ctx, seed, head, history, event)
    obj = method_readers.object_state(conn, ctx, head['object_id'])
    if obj['protocol']['contract_version'] != 'tkos.method/0.3':
        raise GovernedError('PROTOCOL_NOT_SUPPORTED')
    return head['object_id']


def scene_v02_check(conn, ctx, model):
    """Standalone 0.2 scenes have no Method anchor; authority is re-read per event."""
    from memory_service_runtime.governed import workspace_v02_service as v02
    event = model.event.model_dump(mode='json', exclude_none=False)
    history = v02.rows(conn, ctx, str(model.scene_id))
    if event['kind'] == 'scene_create':
        if history:
            raise GovernedError('VERSION_CONFLICT')
        v02.validate_create(conn, ctx, history, event)
    else:
        seed, reader = v02.scene_of(conn, ctx, str(model.scene_id), history)
        if len(history) != model.expected_version:
            raise GovernedError('VERSION_CONFLICT')
        v02.validate_event(conn, ctx, seed, reader, history, event)
    return str(model.scene_id)


def _scene_reader(conn, ctx, scene_id):
    from memory_service_runtime.governed import workspace_v02_service as v02
    from memory_service_runtime.governed import workspace_v02_collaboration as collab
    history = v02.rows(conn, ctx, scene_id)
    if not history:
        raise GovernedError('NOT_FOUND')
    seed, reader = v02.scene_of(conn, ctx, scene_id, history)
    return history, reader, collab.source_states(history)


def context_v02_check(conn, ctx, model):
    """Context creation preview: current readability of every cited version, no write."""
    from memory_service_runtime.governed import workspace_v02_collaboration as collab
    _history, reader, states = _scene_reader(conn, ctx, str(model.scene_id))
    for item in model.items:
        state = states.get(str(item.source_id)) or {}
        if not collab.version_readable(state, str(item.version_event_id), reader, str(item.payload_hash)):
            raise GovernedError('DEPENDENCY_MISSING',
                                'A selected source version is missing, changed or not currently readable.')
    return str(model.scene_id)


def _private(command_id, identity):
    """The private on-disk original envelope, never replaced by a redacted view."""
    try:
        data = json.loads(private_file(root() / (str(uuid.UUID(str(command_id))) + '.json')))
    except (OSError, ValueError):
        raise GovernedError('NOT_FOUND') from None
    if any(data[k] != identity[k] for k in ('scope_id', 'principal_id')):
        raise GovernedError('NOT_FOUND')
    return data


def get(command_id, identity, token):
    data = _private(command_id, identity)
    # Local snapshots never extend current read authorization.
    with db.transaction(token) as (conn, ctx):
        if data.get('kind') in {'scene_v02', 'context_v02'}:
            from memory_service_runtime.governed import workspace_v02_collaboration as collab
            withholder = None
            envelope = data.get('envelope') or {}
            event = envelope.get('event') if isinstance(envelope.get('event'), dict) else {}
            reader, states, history = None, {}, []
            if data.get('kind') == 'scene_v02' and event.get('kind') == 'scene_create' and not data.get('receipt'):
                # Only a create with no persisted scene is re-verified from the
                # original envelope; once the scene exists (committed, possibly
                # with an unknown journal status) normal current-scene
                # authorization applies and external_id uniqueness is not
                # re-imposed.
                try:
                    history, reader, states = _scene_reader(conn, ctx, data['scene_id'])
                except GovernedError as exc:
                    if exc.code != 'NOT_FOUND':
                        raise
                    from memory_service_runtime.governed import workspace_v02_service as v02
                    v02.validate_create(conn, ctx, [], event)
            else:
                history, reader, states = _scene_reader(conn, ctx, data['scene_id'])
            if event.get('kind') == 'followup_draft':
                for item in event.get('items', []):
                    for citation in item.get('citations', []):
                        state = states.get(str(citation.get('source_id'))) or {}
                        if not collab.version_readable(state, str(citation.get('version_event_id')), reader,
                                                       citation.get('payload_hash')):
                            withholder = 'source_derived_payload_withheld'
            if event.get('kind') in {'source_version', 'source_correct'} and data.get('receipt'):
                # A withdrawn version's original text must not be recoverable
                # from the private journal copy through the public view.
                result = (data['receipt'] or {}).get('result') or {}
                event_id = str(result.get('event_id') or '')
                state = states.get(str(event.get('source_id'))) or {}
                if event_id and state and not collab.version_readable(state, event_id, reader):
                    data = {**data, 'envelope': {**envelope, 'event': {**event, 'segments': None}},
                            'payload_withheld': 'withdrawn_source_text_withheld'}
            if event.get('kind') == 'draft_decision':
                draft = next((row for row in history
                              if str(row.get('event_id')) == str(event.get('draft_event_id'))), None)
                unreadable = draft is None
                if draft is not None:
                    unreadable = not collab.draft_readable(states, draft['payload'], reader)
                    for item in draft['payload'].get('items', []):
                        for citation in item.get('citations', []):
                            state = states.get(str(citation.get('source_id'))) or {}
                            if not collab.version_readable(state, str(citation.get('version_event_id')),
                                                           reader, citation.get('payload_hash')):
                                unreadable = True
                if unreadable:
                    data = {**data, 'envelope': {**envelope, 'event': {**event, 'note': None}},
                            'payload_withheld': 'source_derived_payload_withheld'}
            elif data.get('kind') == 'context_v02':
                for item in envelope.get('items', []):
                    state = states.get(str(item.get('source_id'))) or {}
                    if not collab.version_readable(state, str(item.get('version_event_id')), reader,
                                                   item.get('payload_hash')):
                        withholder = 'source_derived_payload_withheld'
            if withholder:
                # A revoked grantee must not recover quoted private source body
                # from their own local journal copy; keep only safe metadata.
                data = {**data, 'envelope': None, 'payload_withheld': withholder}
            if data.get('kind') == 'scene_v02' and data.get('receipt'):
                # 0.2 scene receipts have a real receipt_id; restore them through
                # the core-authorized receipt read (source-fence aware).
                receipt_id = (data['receipt'] or {}).get('receipt_id')
                if receipt_id:
                    from memory_service_runtime.governed.readers import action_receipt
                    try:
                        data = {**data, 'receipt': action_receipt(conn, ctx, str(receipt_id))}
                    except GovernedError:
                        data = {**data, 'receipt': None, 'receipt_status': 'withheld'}
            if data.get('kind') == 'context_v02' and data.get('receipt'):
                # create_context returns the context view itself (context_id/items),
                # never a receipt envelope.  Replace the stored result with a
                # freshly authorized read instead of trusting the old snapshot.
                context_id = (data['receipt'].get('context_id')
                              or (data['receipt'].get('result') or {}).get('context_id'))
                if context_id:
                    from memory_service_runtime.governed import workspace_v02_readers
                    data = {**data, 'receipt': workspace_v02_readers.read_context(conn, ctx, str(context_id))}
                else:
                    data = {**data, 'receipt': None, 'receipt_status': 'not_recorded'}
        else:
            method_access.head(conn, ctx, data['anchor_id'])
            for ref in method_readers.refs(data['envelope']):
                method_access.revision(conn, ctx, ref['object_id'], ref['revision_id'])
            for member in data.get('preview', {}).get('members', []):
                ref = member['ref']
                method_access.revision(conn, ctx, ref['object_id'], ref['revision_id'])
            if data.get('receipt'):
                from memory_service_runtime.governed.readers import action_receipt
                action_receipt(conn, ctx, data['receipt']['receipt_id'])
    return data


def prepare(token, identity, body):
    model, kind = parse(body)
    command_id = str(uuid.uuid5(uuid.NAMESPACE_URL, identity['scope_id'] + '/' + identity['principal_id'] + '/' + model.idempotency_key))
    input_hash = workspace_service.digest(model.model_dump(mode='json', exclude_none=True))
    with lock(command_id) as path:
        if path.exists():
            data = get(command_id, identity, token)
            if data['input_hash'] != input_hash:
                raise GovernedError('IDEMPOTENCY_CONFLICT')
            return data
        with db.transaction(token) as (conn, ctx):
            if ctx.principal_type != 'human':
                raise GovernedError('FORBIDDEN')
            envelope = model.model_dump(mode='json', exclude_none=True)
            scene_id = None
            if kind == 'method':
                prepared = service.prepare_action(conn, ctx, model)
                if model.target:
                    if prepared['target']['expected_version'] != model.target.expected_version:
                        raise GovernedError('VERSION_CONFLICT')
                    anchor_id = str(model.target.object_id)
                else:
                    # Targetless human actions need an exact read anchor from
                    # their own params, never a generic target override.
                    params = envelope.get('params') or {}
                    if model.action_type == 'method_open_problem':
                        anchor_id = str((params.get('payload') or {}).get('state_ref', {}).get('object_id') or '')
                    else:
                        anchor_id = ''
                    if not anchor_id:
                        raise GovernedError('INVALID_REQUEST',
                                            'This action needs an exact subject or state reference.')
                envelope['expected_versions'] = prepared['expected_versions']
            elif kind == 'scene_v02':
                anchor_id = scene_v02_check(conn, ctx, model)
                scene_id = anchor_id
                preview = {'title': f"独立来源场景 · {model.event.kind}",
                           'object_type': 'SourceScene', 'members': [],
                           'formal_effect': 'none'}
            elif kind == 'context_v02':
                anchor_id = context_v02_check(conn, ctx, model)
                scene_id = anchor_id
                preview = {'title': f"来源 Context · {len(model.items)} 项精确版本",
                           'object_type': 'SourceContext', 'members': [],
                           'formal_effect': 'none'}
            else:
                anchor_id = scene_check(conn, ctx, model)
            if kind not in {'scene_v02', 'context_v02'}:
                anchor = method_readers.object_state(conn, ctx, anchor_id)
                preview = {'title': anchor['latest_revision']['payload'].get('title', anchor['object_type']),
                           'object_type': anchor['object_type'], 'members': []}
                if anchor['object_type'] == 'CandidateSet':
                    for ref in anchor['latest_revision']['payload']['target_refs']:
                        revision = method_access.revision(conn, ctx, ref['object_id'], ref['revision_id'])
                        preview['members'].append({'ref': ref, 'title': revision['payload']['title'], 'payload': revision['payload'], 'responsibilities': dashboard._responsibility_entries(conn, ctx, method_access.head(conn, ctx, ref['object_id']), revision['payload'])})
        data = {'command_id': command_id, 'scope_id': identity['scope_id'], 'principal_id': identity['principal_id'],
                'input_hash': input_hash, 'envelope': envelope, 'kind': kind, 'anchor_id': anchor_id,
                'status': 'prepared', 'receipt': None, 'error': None, 'preview': preview}
        if scene_id:
            data['scene_id'] = scene_id
        write(path, data)
        return data


def commit(command_id, identity, token, *, retry=False):
    with lock(command_id) as path:
        # Replay and audit use the preserved private envelope; only the read
        # view is redacted by current source authorization.
        data = _private(command_id, identity)
        if data['status'] == 'rejected':
            # A rejected command still needs current authorization before its
            # saved body is shown again.
            return get(command_id, identity, token)
        if data['status'] == 'unknown' and not retry:
            raise GovernedError('RESULT_UNKNOWN', status=409)
        model, kind = parse(data['envelope'])
        already_committed = data['status'] == 'committed'
        # A previously committed local record is still reauthorized by core replay.
        data['status'] = 'unknown'
        write(path, data)
        try:
            with db.transaction(token) as (conn, ctx):
                if ctx.principal_type != 'human':
                    raise GovernedError('FORBIDDEN')
                if kind == 'method':
                    receipt = service.execute_action(conn, ctx, model)
                elif kind == 'scene_v02':
                    from memory_service_runtime.governed import workspace_v02_service as v02
                    receipt = v02.execute(conn, ctx, model)
                elif kind == 'context_v02':
                    from memory_service_runtime.governed import workspace_v02_service as v02
                    receipt = v02.create_context(conn, ctx, model)
                else:
                    receipt = workspace_service.execute(conn, ctx, model)
                receipt = db.jsonable(receipt)
            data.update(status='committed', receipt=receipt, error=None)
        except GovernedError as exc:
            if already_committed:
                data['status'] = 'committed'
                write(path, data)
                raise
            if exc.status >= 500:
                data.update(error=exc.code)
            else:
                data.update(status='rejected', error=exc.code)
            write(path, data)
            if exc.status in {401, 403, 404}:
                raise
            return get(command_id, identity, token)
        except Exception:
            if already_committed:
                data['status'] = 'committed'
                write(path, data)
                raise
            # The database commit might have succeeded: do not manufacture a rejection.
            data.update(error='RESULT_UNKNOWN')
            write(path, data)
            return get(command_id, identity, token)
        write(path, data)
        return get(command_id, identity, token)


def listing(identity, token):
    items = []
    for path in sorted(root().glob('*.json'), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            items.append(get(path.stem, identity, token))
        except GovernedError as exc:
            if exc.code not in {'FORBIDDEN', 'NOT_FOUND'}:
                raise
    return {'items': items}
