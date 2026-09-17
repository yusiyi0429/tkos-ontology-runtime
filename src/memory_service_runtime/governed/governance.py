"""M1B human-workbench projections; formal actions retain their original executor."""
from uuid import UUID
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response

from . import db, method_access, method_readers, workspace_readers, workspace_service, dashboard
from .errors import GovernedError
from .routes import bearer

router = APIRouter(prefix='/v1/governance', tags=['governance-workbench'])
# Object types whose 0.4 human actions may become pending work.
METHOD_TASK_TYPES = ('StrategicIssue', 'StrategicAgreement', 'StrategyUpdateProposal',
                     'LTCO', 'PCO', 'Mission', 'ReviewWindow', 'CandidateSet',
                     'OperatingState', 'OperatingProblem')

HUMAN_ACTIONS = {
    'm1b_comment': '发表意见', 'm1b_withdraw_comment': '撤回本人意见',
    'm1b_confirm_candidates': '确认整个候选集合',
    'm1b_reopen_window': '重开评论窗口', 'm1b_reopen_candidates': '退回并重开窗口',
}

# Human-offered workspace 0.2 events (scene Agent runs stay outside).
HUMAN_ACTION_LABELS = {
    **HUMAN_ACTIONS,
    'scene_create': '建立独立来源场景',
    'source_add': '登记来源',
    'source_version': '登记来源新版本',
    'source_correct': '更正来源',
    'source_withdraw': '撤回来源',
    'source_share': '分享来源片段',
    'source_unshare': '取消分享',
    'followup_draft': '提交跟进草稿',
    'draft_decision': '本人接受或处理草稿条目',
    'm1a_set_participants': '指定必要参与人',
    'm1a_confirm_agreement': '确认 Agreement（本人）',
    'm1a_confirm_update': '最终确认正式更新',
    'm1b_confirm_ltco': '确认 LTCO 正式版本',
    'm1b_replace_comment': '替代本人意见',
    'm1b_commit_candidate': '提交本人责任承诺',
    'm1b_activate_candidates': '整组激活候选集合',
    'method_confirm_state': '确认正式经营状态',
    'method_open_problem': '登记经营问题',
    'method_revise_problem': '修订经营问题',
    'method_close_problem': '关闭经营问题',
}

FORMAL_EFFECT = {
    'm1a_set_participants': 'participants_and_agreement_version',
    'm1a_confirm_agreement': 'agreement_confirmation_record',
    'm1a_confirm_update': 'atomic_formal_update',
    'm1b_confirm_ltco': 'exact_ltco_version',
    'm1b_comment': 'comment_history',
    'm1b_replace_comment': 'comment_history',
    'm1b_withdraw_comment': 'comment_history',
    'm1b_commit_candidate': 'own_responsibility_commitment',
    'm1b_activate_candidates': 'entire_candidate_set_effective_no_execution_authority',
    'm1b_reopen_candidates': 'new_review_window',
    'm1b_reopen_window': 'new_review_window',
    'method_confirm_state': 'canonical_operating_state',
    'method_open_problem': 'tracked_problem',
    'method_revise_problem': 'tracked_problem',
    'method_close_problem': 'problem_disposition',
}


CEO_ONLY_ACTIONS = frozenset({
    'm1a_set_participants', 'm1a_confirm_update', 'm1b_confirm_ltco',
    'm1b_activate_candidates', 'm1b_reopen_candidates', 'm1b_reopen_window',
})
SCOPED_HUMAN_ACTIONS = frozenset({
    'm1a_confirm_agreement', 'm1b_comment', 'm1b_replace_comment', 'm1b_withdraw_comment',
    'm1b_commit_candidate', 'method_confirm_state',
    'method_revise_problem', 'method_close_problem',
})
# Conservative phase gates per action, from the implemented 0.4 validators.  The
# core still re-checks; an unknown phase never yields "allowed".
PHASE_RULES = {
    'm1a_set_participants': {'issue_confirmed', 'agreement_formal', 'completed'},
    'm1a_confirm_agreement': {'draft', 'awaiting_confirmation'},
    'm1a_confirm_update': {'reviewed'},
    'm1b_confirm_ltco': {'draft'},
    'm1b_comment': {'open'}, 'm1b_replace_comment': {'open'}, 'm1b_withdraw_comment': {'open'},
    'm1b_commit_candidate': {'pending'},
    'm1b_activate_candidates': {'pending'},
    'm1b_reopen_candidates': {'pending'},
    'm1b_reopen_window': {'open', 'closed', 'resolved'},
    'method_confirm_state': {'proposed'},
    'method_revise_problem': {'open'}, 'method_close_problem': {'open'},
    'method_open_problem': {'confirmed'},
}
SCOPED_REASONS = {
    'm1a_confirm_agreement': 'not_required_participant',
    'm1b_comment': 'not_window_participant', 'm1b_replace_comment': 'not_window_participant',
    'm1b_withdraw_comment': 'not_window_participant',
    'm1b_commit_candidate': 'not_responsible_owner',
    'method_confirm_state': 'not_state_owner',
    'method_revise_problem': 'not_responsible_owner', 'method_close_problem': 'not_responsible_owner',
}


def _caller_is_ceo(ctx, domain_id):
    return any(a['role'] == 'CEO' and a['domain_id'] == domain_id for a in ctx.assignments)


def _ref_option(conn, ctx, ref, *, label=None):
    """Exact reference plus a human label; never asks the browser for a hash."""
    value = {'value': f"{ref['object_id']}:{ref['revision_id']}", 'ref': dict(ref)}
    if label:
        value['label'] = label
        return value
    try:
        revision = method_access.revision(conn, ctx, ref['object_id'], ref['revision_id'])
        title = (revision.get('payload') or {}).get('title')
        value['label'] = f"{title} · {ref['revision_id'][:8]}" if title else f"{ref['object_id'][:8]} · {ref['revision_id'][:8]}"
    except GovernedError:
        value['label'] = f"{ref['object_id'][:8]} · {ref['revision_id'][:8]}"
    return value


def _assignment_options(ctx):
    return [{'value': row['assignment_id'], 'assignment_id': row['assignment_id'],
             'role': row.get('role'), 'domain_id': str(row.get('domain_id')),
             'label': f"{row.get('role')} · {str(row.get('domain_id'))[:8]}"} for row in ctx.assignments]


def action_options(conn, ctx, obj, action):
    """Authorized, server-derived exact references for one human action form.

    The browser only ever selects from these options; it never receives a raw
    object/revision/hash text box.  Empty options mean the action is not
    currently preparable, not that free input becomes acceptable.
    """
    if conn is None:
        return {}
    payload = (obj.get('latest_revision') or {}).get('payload') or {}
    frozen = [*payload.get('pco_refs', []), *payload.get('mission_refs', [])]
    if action in {'m1b_comment', 'm1b_replace_comment'}:
        options = {'targets': [_ref_option(conn, ctx, ref) for ref in frozen]}
        if action == 'm1b_replace_comment':
            options['opinions'] = _my_opinions(conn, ctx, obj)
        return options
    if action == 'm1b_withdraw_comment':
        return {'opinions': _my_opinions(conn, ctx, obj)}
    if action == 'm1b_commit_candidate':
        from . import method_v04
        try:
            required = method_v04._required_committers(method_v04._LightExecution(conn, ctx), payload)
        except GovernedError:
            return {'responsibilities': []}
        mine = [spec for spec in required.values() if str(spec['owner']) == str(ctx.principal_id)]
        return {'responsibilities': [_ref_option(conn, ctx, spec['reference']) for spec in mine]}
    if action in {'method_open_problem', 'method_revise_problem', 'method_close_problem'}:
        assignments = _assignment_options(ctx)
        evidences = []
        seen = set()
        materials = []
        if action != 'method_open_problem' and payload.get('state_ref'):
            materials.append(payload['state_ref'])
        materials.extend(payload.get('evidence_refs') or [])
        if obj.get('latest_revision', {}).get('payload', {}).get('subject_ref'):
            state = obj['latest_revision']['payload']
            materials.extend(state.get('evidence_refs') or [])
            materials.extend(state.get('baseline_refs') or [])
        for ref in materials:
            key = (ref.get('object_id'), ref.get('revision_id'))
            if not ref.get('object_id') or key in seen:
                continue
            seen.add(key)
            evidences.append(_ref_option(conn, ctx, ref))
        options = {'assignments': assignments, 'evidences': evidences}
        if action == 'method_open_problem':
            options['state_ref'] = reference(obj)
            options['domain_id'] = obj['domain_id']
        elif payload.get('state_ref'):
            options['state_ref'] = payload['state_ref']
        return options
    if action == 'm1a_set_participants':
        rows = conn.execute(
            """SELECT a.principal_id, a.assignment_id, a.role, p.display_name,
                      (SELECT b.agent_principal_id FROM gov_method_agent_bindings b
                        WHERE b.scope_id=a.scope_id AND b.owner_principal_id=a.principal_id LIMIT 1) AS agent_id
                 FROM gov_role_assignments a JOIN gov_principals p
                   ON (p.scope_id,p.principal_id)=(a.scope_id,a.principal_id)
                WHERE a.scope_id=%s AND a.active AND p.active AND p.principal_type='human'
                  AND a.valid_from<=clock_timestamp() AND (a.valid_to IS NULL OR a.valid_to>clock_timestamp())
                ORDER BY p.display_name, a.assignment_id""", (ctx.scope_id,)).fetchall()
        return {'participants': [{'value': row['principal_id'], 'principal_id': str(row['principal_id']),
                                  'assignment_id': str(row['assignment_id']), 'role': row['role'],
                                  'personal_agent_id': str(row['agent_id']) if row['agent_id'] else None,
                                  'label': f"{row['display_name']} · {row['role']}"}
                                 for row in db.jsonable(rows)]}
    return {}


def _my_opinions(conn, ctx, obj):
    try:
        records = method_readers.review_records(conn, ctx, obj['object_id'])['items']
    except GovernedError:
        return []
    result = []
    for record in records:
        if record.get('kind') != 'window_comment' or str(record.get('principal_id')) != str(ctx.principal_id):
            continue
        if not record.get('effective_opinion'):
            continue
        target = {'object_id': str(record['target_object_id']), 'revision_id': str(record['target_revision_id']),
                  'payload_hash': None}
        try:
            target['payload_hash'] = method_access.revision(
                conn, ctx, target['object_id'], target['revision_id'])['payload_hash']
        except GovernedError:
            continue
        result.append({'value': str(record['record_id']), 'record_id': str(record['record_id']),
                       'ref': dict(target), 'target_ref': target,
                       'label': str((record.get('content') or {}).get('content') or '本人有效意见')[:60]})
    return result


def _candidate_members(conn, ctx, obj):
    members = []
    for ref in (obj['latest_revision']['payload'].get('target_refs') or []):
        try:
            head = method_access.head(conn, ctx, ref['object_id'])
            revision = method_access.revision(conn, ctx, ref['object_id'], ref['revision_id'])
        except GovernedError:
            continue
        members.append({'ref': dict(ref), 'object_type': head['object_type'],
                        'title': (revision.get('payload') or {}).get('title', head['object_type']),
                        'payload': revision.get('payload') or {}})
    return members


def _candidate_commitments(conn, ctx, obj):
    rows = conn.execute("""SELECT responsibility_object_id, principal_id, assignment_id, statement, recorded_at
        FROM gov_method_commitments WHERE scope_id=%s AND candidate_revision_id=%s
        ORDER BY recorded_at, commitment_id""",
        (ctx.scope_id, obj['latest_revision']['revision_id'])).fetchall()
    return db.jsonable(rows)


def _availability(conn, ctx, obj, action, *, ceo):
    """Conservative current role/responsibility/phase availability for one action.

    Never treats a generic human as allowed; the reason names the missing
    condition and the core re-validates at prepare and commit.
    """
    if ctx.principal_type != 'human':
        return False, 'human_identity_required'
    state = obj.get('method_state') or {}
    phase = state.get('phase')
    required = PHASE_RULES.get(action)
    if required is not None and phase not in required:
        return False, 'phase_not_permitted'
    if action in CEO_ONLY_ACTIONS and not ceo:
        return False, 'current_role_not_permitted'
    if action == 'm1a_confirm_agreement':
        confirmed = set(state.get('confirmed_principal_ids') or [])
        if ctx.principal_id in confirmed:
            return False, 'already_confirmed'
    if action in SCOPED_HUMAN_ACTIONS:
        from . import method_v04
        try:
            method_v04.scoped_assignment(conn, ctx, action, obj, obj['latest_revision'])
        except GovernedError:
            return False, SCOPED_REASONS.get(action, 'not_responsible_owner')
    if action == 'method_open_problem':
        from . import method_v04
        owner = method_v04._state_subject_owner_static(conn, ctx, obj['latest_revision']['payload']['subject_ref'])
        if owner != ctx.principal_id or not db._assignments(conn, ctx):
            return False, 'not_state_owner'
    if action == 'm1b_activate_candidates' and conn is not None:
        from . import method_v04
        blockers = method_v04.activation_blockers(conn, ctx, obj)
        if blockers:
            return False, blockers[0]
    return True, None


def reference(obj):
    rev = obj['latest_revision']
    return {'object_id': obj['object_id'], 'revision_id': rev['revision_id'], 'payload_hash': rev['payload_hash']}


def scenes(conn, ctx, window_id):
    result = []
    for row in conn.execute("SELECT scene_id FROM gov_workspace_events WHERE scope_id=%s AND kind='create' AND payload->>'scene_type'='monthly' AND payload->'anchor_ref'->>'object_id'=%s ORDER BY scene_id", (ctx.scope_id, window_id)).fetchall():
        try:
            result.append(workspace_readers.read(conn, ctx, str(row['scene_id'])))
        except GovernedError as exc:
            if exc.code not in {'FORBIDDEN', 'NOT_FOUND'}:
                raise
    return result


def method_object_actions(conn, ctx, obj):
    """Allowlisted human actions for one 0.4 object with current availability."""
    from .method_v04_models import ACTION_TARGETS, HUMAN_ACTIONS as V04_HUMAN_ACTIONS
    if obj['protocol']['contract_version'] != 'tkos.method/0.4':
        return {'items': []}
    ceo = ctx.principal_type == 'human' and _caller_is_ceo(ctx, obj['domain_id'])
    offered = [action for action in sorted(V04_HUMAN_ACTIONS)
               if obj['object_type'] in ACTION_TARGETS.get(action, frozenset())]
    if obj['object_type'] == 'OperatingState':
        # Problem opening has no target object; the state in the form is the
        # exact subject.  Availability is still checked now, not only at submit.
        offered.append('method_open_problem')
    items = []
    for action in offered:
        allowed, reason = _availability(conn, ctx, obj, action, ceo=ceo)
        items.append({'action_type': action,
            'label': HUMAN_ACTION_LABELS.get(action, action), 'allowed': allowed, 'reason': reason,
            'target': None if action == 'method_open_problem' else
                {**reference(obj), 'expected_version': obj['object_version']},
            'object_domain_id': obj['domain_id'],
            'contract_version': obj['protocol']['contract_version'],
            'options': action_options(conn, ctx, obj, action),
            'formal_effect': FORMAL_EFFECT.get(action, 'versioned_business_write')})
    return {'items': items}


def actions(conn, ctx, object_id):
    obj = method_readers.object_state(conn, ctx, object_id)
    if obj['protocol']['contract_version'] == 'tkos.method/0.4':
        return method_object_actions(conn, ctx, obj)
    kind = obj['object_type']
    if kind == 'CandidateSet':
        window_id = obj['latest_revision']['payload']['window_ref']['object_id']
    elif kind == 'ReviewWindow':
        window_id = object_id
    else:
        return {'items': []}
    monthly = workspace_readers.monthly(conn, ctx, window_id)
    result = []
    for operation in monthly['operations']:
        action = operation['action_type']
        if action not in HUMAN_ACTIONS:
            continue
        target = obj
        if action in {'m1b_confirm_candidates', 'm1b_reopen_candidates'}:
            if monthly['candidate']['status'] != 'available':
                continue
            target = method_readers.object_state(conn, ctx, monthly['candidate']['ref']['object_id'])
        elif kind == 'CandidateSet':
            target = method_readers.object_state(conn, ctx, window_id)
        reason = operation['reason']
        if target['protocol']['contract_version'] != 'tkos.method/0.3':
            reason = 'read_only_contract_version'
        if ctx.principal_type != 'human':
            reason = 'human_identity_required'
        result.append({**operation, 'allowed': reason is None, 'reason': reason,
            'label': HUMAN_ACTIONS[action], 'target': {**reference(target), 'expected_version': target['object_version']},
            'contract_version': target['protocol']['contract_version'],
            'formal_effect': 'entire_candidate_set_effective_no_execution_authority' if action == 'm1b_confirm_candidates' else 'comment_history' if action in {'m1b_comment', 'm1b_withdraw_comment'} else 'new_review_window'})
    return {'items': result}


def method_tasks(conn, ctx, after=None, limit=25):
    """0.4 objects where this human identity is offered an allowlisted action."""
    rows = conn.execute(
        """SELECT object_id FROM gov_objects
           WHERE scope_id=%s AND object_type=ANY(%s) AND (%s::uuid IS NULL OR object_id>%s::uuid)
           ORDER BY object_id LIMIT %s""",
        (ctx.scope_id, list(METHOD_TASK_TYPES), after, after, limit * 3)).fetchall()
    items = []
    for row in rows:
        try:
            obj = method_readers.object_state(conn, ctx, str(row['object_id']))
        except GovernedError as exc:
            if exc.code in {'NOT_FOUND', 'FORBIDDEN'}:
                continue
            raise
        if obj['protocol']['contract_version'] != 'tkos.method/0.4':
            continue
        offered_all = method_object_actions(conn, ctx, obj)['items']
        offered = [item for item in offered_all if item['allowed']]
        blocked_activation = [item for item in offered_all
                              if not item['allowed'] and item['action_type'] == 'm1b_activate_candidates']
        visible = offered + blocked_activation
        if not visible:
            continue
        item = {'object_id': obj['object_id'], 'object_type': obj['object_type'],
                'title': obj['latest_revision']['payload'].get('title', obj['object_type']),
                'phase': obj['method_state'].get('phase', 'unknown'),
                'contract_version': obj['protocol']['contract_version'],
                'payload': obj['latest_revision']['payload'],
                'method_state': obj['method_state'],
                'actions': visible}
        if obj['object_type'] == 'CandidateSet':
            item['members'] = _candidate_members(conn, ctx, obj)
            item['commitments'] = _candidate_commitments(conn, ctx, obj)
        items.append(item)
        if len(items) > limit:
            break
    return {'items': items[:limit],
            'next_after': items[limit - 1]['object_id'] if len(items) > limit else None}


def window(conn, ctx, window_id):
    obj = method_readers.object_state(conn, ctx, window_id)
    if obj['protocol']['contract_version'] == 'tkos.method/0.4':
        # 0.4 windows are Method objects; no 0.1 monthly scene is involved.
        return {'identity': workspace_readers.identity(conn, ctx), 'object': obj,
                'monthly': None, 'scenes': [],
                'actions': method_object_actions(conn, ctx, obj)['items'],
                'can_create_scene': False,
                'recovery': method_readers.recovery(conn, ctx, window_id)}
    monthly = workspace_readers.monthly(conn, ctx, window_id)
    scene_views = scenes(conn, ctx, window_id)
    candidate_targets = []
    if monthly['candidate']['status'] == 'available':
        for ref in monthly['candidate']['revision']['payload']['target_refs']:
            revision = method_access.revision(conn, ctx, ref['object_id'], ref['revision_id'])
            candidate_targets.append({'ref': ref, 'revision': revision})
    monthly['candidate_targets'] = candidate_targets
    for item in [*monthly['targets'], *candidate_targets]:
        head = method_access.head(conn, ctx, item['ref']['object_id'])
        item['responsibilities'] = dashboard._responsibility_entries(conn, ctx, head, item['revision']['payload'])
    is_ceo = ctx.principal_type == 'human' and any(a['role'] == 'CEO' and a['domain_id'] == obj['domain_id'] for a in ctx.assignments)
    return {'identity': workspace_readers.identity(conn, ctx), 'object': obj, 'monthly': monthly,
            'scenes': scene_views, 'actions': actions(conn, ctx, window_id)['items'],
            'can_create_scene': is_ceo and obj['protocol']['contract_version'] == 'tkos.method/0.3',
            'recovery': method_readers.recovery(conn, ctx, window_id)}


def tasks(conn, ctx, after=None, limit=25):
    items = []
    rows = conn.execute("SELECT object_id FROM gov_objects WHERE scope_id=%s AND object_type='ReviewWindow' AND (%s::uuid IS NULL OR object_id>%s::uuid) ORDER BY object_id", (ctx.scope_id, after, after)).fetchall()
    for row in rows:
        try:
            value = window(conn, ctx, str(row['object_id']))
        except GovernedError as exc:
            if exc.code in {'NOT_FOUND', 'FORBIDDEN'}:
                continue
            raise
        obj = value['object']
        monthly = value['monthly']
        if obj['protocol']['contract_version'] == 'tkos.method/0.4':
            items.append({'object_id': obj['object_id'],
                          'title': obj['latest_revision']['payload'].get('title', obj['object_type']),
                          'phase': obj['method_state'].get('phase', 'unknown'),
                          'label': '0.4 人工确认事项', 'contract_version': obj['protocol']['contract_version'],
                          'actions': [item for item in value['actions'] if item['allowed']]})
            if len(items) > limit:
                break
            continue
        phase = obj['method_state'].get('phase')
        if phase not in {'open', 'closed', 'resolved'}:
            continue
        enabled = [a['action_type'] for a in value['actions'] if a['allowed']]
        label = '等待 Co-agent 收拢'
        if 'm1b_confirm_candidates' in enabled:
            label = '审阅并决定候选集合'
        elif 'm1b_comment' in enabled:
            label = '补充共同核对意见' if monthly['my_reviews'] else '参与共同核对'
        elif phase == 'resolved':
            if any(s['monthly']['reviewed_current_candidate'] for s in value['scenes']):
                continue
            label = '核对候选差异'
        items.append({'object_id': obj['object_id'], 'title': obj['latest_revision']['payload']['title'],
                      'phase': phase, 'label': label, 'contract_version': obj['protocol']['contract_version'],
                      'actions': value['actions']})
        if len(items) > limit:
            break
    return {'items': items[:limit], 'next_after': items[limit - 1]['object_id'] if len(items) > limit else None}


def bases(conn, ctx):
    result = []
    for row in conn.execute("SELECT object_id FROM gov_objects WHERE scope_id=%s AND object_type IN ('Strategy','LTCO') AND effective_revision_id IS NOT NULL ORDER BY object_id", (ctx.scope_id,)).fetchall():
        try:
            obj = method_readers.object_state(conn, ctx, str(row['object_id']))
            rev = obj['effective_revision']
            if rev and obj['protocol']['contract_version'] == 'tkos.method/0.3':
                result.append({'object_type': obj['object_type'], 'title': rev['payload']['title'],
                    'ref': {'object_id': obj['object_id'], 'revision_id': rev['revision_id'], 'payload_hash': rev['payload_hash']},
                    'strategy_ref': rev['payload'].get('strategy_ref')})
        except GovernedError as exc:
            if exc.code not in {'NOT_FOUND', 'FORBIDDEN'}:
                raise
    return {'items': result}


def source_scenes(conn, ctx, scene_type=None, after=None, limit=25):
    """0.2 standalone source scenes visible to the current member (no count)."""
    from . import workspace_v02_readers as v02
    return v02.list_scenes(conn, ctx, scene_type, after, limit)


def source_scene(conn, ctx, scene_id):
    """Scene read plus names for the scene's own principals only.

    The name map is built exclusively from principals already named by the
    authorized scene definition; no scope-wide directory is exposed for
    convenience, and an unresolved person stays an id with no guessed name.
    """
    from . import workspace_v02_readers as v02
    result = v02.read(conn, ctx, scene_id)
    scene = result.get('scene', {})
    ids = {scene.get('owner_principal_id'), *scene.get('participant_principal_ids', [])}
    for binding in scene.get('agent_bindings', []):
        ids.add(binding.get('agent_principal_id'))
        ids.add(binding.get('owner_principal_id'))
    ids = {value for value in ids if value}
    names = {}
    if ids:
        rows = conn.execute("""SELECT principal_id, display_name FROM gov_principals
            WHERE scope_id=%s AND principal_id=ANY(%s::uuid[])""",
            (ctx.scope_id, sorted(ids))).fetchall()
        names = {str(row['principal_id']): row['display_name'] for row in rows}
    result['principal_names'] = names
    return result


def source_context(conn, ctx, context_id):
    from . import workspace_v02_readers as v02
    return v02.read_context(conn, ctx, context_id)


def read(token, function, *args, **kwargs):
    with db.transaction(token) as (conn, ctx):
        return db.jsonable(function(conn, ctx, *args, **kwargs))


@router.get('/method/tasks')
def read_method_tasks(response: Response, token: Annotated[str, Depends(bearer)],
                      after: UUID | None = None,
                      limit: Annotated[int, Query(ge=1, le=100)] = 25):
    response.headers['Cache-Control'] = 'no-store'
    return read(token, method_tasks, str(after) if after else None, limit)


@router.get('/sources')
def read_sources(response: Response, token: Annotated[str, Depends(bearer)],
                 scene_type: str | None = None, after: UUID | None = None,
                 limit: Annotated[int, Query(ge=1, le=100)] = 25):
    response.headers['Cache-Control'] = 'no-store'
    return read(token, source_scenes, scene_type, str(after) if after else None, limit)


@router.get('/sources/contexts/{context_id}')
def read_source_context(context_id: UUID, response: Response, token: Annotated[str, Depends(bearer)]):
    response.headers['Cache-Control'] = 'no-store'
    return read(token, source_context, str(context_id))


@router.get('/sources/{scene_id}')
def read_source_scene(scene_id: UUID, response: Response, token: Annotated[str, Depends(bearer)]):
    response.headers['Cache-Control'] = 'no-store'
    return read(token, source_scene, str(scene_id))


@router.get('/tasks')
def read_tasks(response: Response, token: Annotated[str, Depends(bearer)], after: UUID | None = None,
               limit: Annotated[int, Query(ge=1, le=100)] = 25):
    response.headers['Cache-Control'] = 'no-store'
    return read(token, tasks, str(after) if after else None, limit)


@router.get('/review-windows/{object_id}')
def read_window(object_id: UUID, response: Response, token: Annotated[str, Depends(bearer)]):
    response.headers['Cache-Control'] = 'no-store'
    return read(token, window, str(object_id))


@router.get('/objects/{object_id}/actions')
def read_actions(object_id: UUID, response: Response, token: Annotated[str, Depends(bearer)]):
    response.headers['Cache-Control'] = 'no-store'
    return read(token, actions, str(object_id))
