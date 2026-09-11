"""Round definitions and formal submissions under the caller's scope fence."""
from copy import deepcopy
from uuid import uuid4
from . import a2_models, db, protocol
from .errors import GovernedError


def fail(code):
    raise GovernedError(code)


def actor(e, domain, role, assignment_id=None):
    allowed = db.authorize_domain(e.conn, e.ctx, domain, e.kind)
    rows = [db.jsonable(r) for r in allowed if r['role'] == role and str(r['principal_id']) == e.ctx.principal_id
            and (assignment_id is None or str(r['assignment_id']) == assignment_id)]
    if not rows or e.ctx.principal_type != 'human': fail('FORBIDDEN')
    row = sorted(rows, key=lambda r: r['assignment_id'])[0]
    e.required_assignments.add(row['assignment_id']); e.action_assignments.append(row)
    return row


def state(e, oid):
    row = e.conn.execute('SELECT * FROM gov_formation_round_state WHERE scope_id=%s AND object_id=%s',
                         (e.ctx.scope_id, oid)).fetchone()
    if row is None: fail('NOT_FOUND')
    return db.jsonable(row)


def members(e, specs, ceo):
    result, people = [], {ceo['principal_id']}
    for m in specs:
        a = e.assignment(m['dri_assignment_id'])
        if a['role'] != 'DOMAIN_DRI' or a['domain_id'] != m['domain_id'] or a['principal_id'] in people: fail('FORBIDDEN')
        people.add(a['principal_id'])
        result.append({'domain_id': m['domain_id'], 'dri_assignment_id': a['assignment_id'], 'dri_principal_id': a['principal_id']})
    return sorted(result, key=lambda m: m['domain_id'])


def authorize(e):
    p = e.params
    if e.ctx.principal_type != 'human': fail('FORBIDDEN')
    if e.kind == 'open_formation_round':
        if p['company_id'] != e.ctx.company_id: fail('FORBIDDEN')
        e.domain_id = p['company_domain_id']; ceo = actor(e, e.domain_id, 'CEO', p['ceo_assignment_id'])
        for m in p['members']: actor(e, m['domain_id'], 'CEO')
        e.a2_members, e.a2_ceo = members(e, p['members'], ceo), ceo
        e.creation_fields = protocol.resolve_creation(e.conn, e.ctx.scope_id, e.domain_id, 'FormationRound',
                                                      e.request.contract_version, action_type=e.kind)
        e.protocol_context = e.creation_fields['contract_version']
        if (p['method_profile_ref']['profile_id'], p['method_profile_ref']['revision']) != (
                e.creation_fields['profile_id'], e.creation_fields['profile_revision']): fail('METHOD_PROFILE_UNSUPPORTED')
        return
    e.target = e.head(e.request.target.object_id)
    if e.target['object_type'] != 'FormationRound': fail('NOT_FOUND')
    e.target_revision = e.revision(e.target['object_id'], e.request.target.revision_id)
    definition = e.revision(e.target['object_id'], e.target['latest_revision_id'])['payload']
    if e.kind == 'publish_domain_submission':
        e.domain_id = p['domain_id']
        slot = next((m for m in definition['members'] if m['domain_id'] == e.domain_id), None)
        if slot is None or slot['dri_assignment_id'] != p['dri_assignment_id'] or slot['dri_principal_id'] != e.ctx.principal_id: fail('FORBIDDEN')
        actor(e, e.domain_id, 'DOMAIN_DRI', p['dri_assignment_id'])
    else:
        e.domain_id = definition['company_domain_id']; ceo = actor(e, e.domain_id, 'CEO')
        specs = p.get('members', definition['members'])
        for domain in {m['domain_id'] for m in [*definition['members'], *specs]}: actor(e, domain, 'CEO')
        e.a2_members, e.a2_ceo = members(e, specs, ceo), ceo
    e.protocol_context = protocol.gate_target_action(e.conn, e.ctx.scope_id, e.target['object_id'], e.kind, e.request.contract_version)
    if e.target['lifecycle_status'] != 'open': fail('INVALID_STATE')


def source_refs(payload):
    refs = list(payload.get('upstream_refs', [])) + list(payload.get('dependency_refs', []))
    refs.extend(b['source_ref'] for b in payload.get('bindings', []))
    for m in payload.get('missions', []): refs.extend(source_refs(m))
    return refs


def sources(e, refs, shared_domains):
    seen, active = set(), set()
    heights = {}
    def visit(ref, depth):
        oid, rid = ref['object_id'], ref['revision_id']
        obj, rev = e.add_dependency(oid), e.revision(oid, rid)
        if oid in active or depth > a2_models.CLOSURE_MAX_DEPTH: fail('COMPOSITION_NOT_READY')
        if obj['object_type'] not in a2_models.A2_SOURCE_TYPES: fail('INVALID_STATE')
        if obj['effective_revision_id'] != rid or ref.get('payload_hash', rev['payload_hash']) != rev['payload_hash']: fail('COMPOSITION_INPUT_CHANGED')
        if not set(shared_domains).issubset(set(rev['payload'].get('shared_with_domain_ids', []))): fail('FORBIDDEN')
        live = e.conn.execute('''SELECT valid_from<=clock_timestamp() AND (valid_to IS NULL OR clock_timestamp()<valid_to) AND
            (NOT payload ? 'observed_at' OR ((payload->>'observed_at')::timestamptz<=clock_timestamp()
             AND clock_timestamp()-(payload->>'observed_at')::timestamptz<=interval '86400 seconds')) AS valid
            FROM gov_object_revisions WHERE scope_id=%s AND object_id=%s AND revision_id=%s''', (e.ctx.scope_id, oid, rid)).fetchone()
        if live is None or not live['valid']: fail('COMPOSITION_INPUT_CHANGED')
        if (oid, rid) in seen:
            # A previously validated tail can be reached through a longer
            # prefix. Its full height still counts toward the path bound.
            if depth + heights[(oid, rid)] - 1 > a2_models.CLOSURE_MAX_DEPTH: fail('COMPOSITION_NOT_READY')
            return heights[(oid, rid)]
        seen.add((oid, rid))
        if len(seen) > a2_models.CLOSURE_MAX_NODES: fail('COMPOSITION_NOT_READY')
        active.add(oid)
        height = 1
        for child in source_refs(rev['payload']): height = max(height, 1 + visit(child, depth+1))
        active.remove(oid)
        heights[(oid, rid)] = height
        return height
    for ref in refs: visit(ref, 1)


def collect(e):
    p = e.params
    definition = p if e.kind == 'open_formation_round' else e.target_revision['payload']
    specs = p.get('members', definition['members']) if e.kind == 'amend_formation_round' else definition['members']
    shared = [definition['company_domain_id'], *[m['domain_id'] for m in specs]]
    ref = p.get('company_reference_ref', definition['company_reference_ref']); refs = [ref]
    if e.kind == 'publish_domain_submission':
        refs.extend(source_refs(p['submission']))
        for m in p['submission']['missions']:
            row = e.conn.execute('''SELECT mission_object_id FROM gov_mission_index
                WHERE scope_id=%s AND round_object_id=%s AND domain_id=%s AND mission_key=%s''',
                (e.ctx.scope_id, e.target['object_id'], p['domain_id'], m['mission_key'])).fetchone()
            if row: e.add_dependency(str(row['mission_object_id']))
    sources(e, refs, shared)
    reference = e.revision(ref['object_id'], ref['revision_id'])
    if e.head(ref['object_id'])['object_type'] != 'CompanyReference' or reference['payload']['period_id'] != definition['period_id']: fail('COMPOSITION_INPUT_CHANGED')
    if e.kind == 'publish_domain_submission':
        for b in p['submission']['bindings']:
            if b['period_id'] != definition['period_id']: fail('COMPOSITION_INPUT_CHANGED')
            source = b['source_ref']; obj, rev = e.head(source['object_id']), e.revision(source['object_id'], source['revision_id'])
            if obj['object_type'] != 'CapacityObservation' or any(rev['payload'][key] != b[key] for key in ('resource_id', 'period_id', 'unit')): fail('COMPOSITION_INPUT_CHANGED')
        if any(r['period_id'] != definition['period_id'] for r in p['submission']['resources']): fail('COMPOSITION_INPUT_CHANGED')


def new_object(e, kind, domain, payload, status, *, round_id=None, effective=False):
    oid = str(uuid4())
    obj = db.jsonable(e.conn.execute('''INSERT INTO gov_objects(object_id,scope_id,domain_id,object_type,lifecycle_status)
        VALUES (%s,%s,%s,%s,%s) RETURNING *''', (oid, e.ctx.scope_id, domain, kind, status)).fetchone())
    if round_id:
        protocol.inherit_binding(e.conn, e.ctx.scope_id, oid, round_id, registered_by=e.ctx.principal_id, receipt_id=e.action_id)
    else:
        protocol.insert_binding(e.conn, e.ctx.scope_id, oid, e.creation_fields, registered_by=e.ctx.principal_id, receipt_id=e.action_id)
    revision = e.insert_revision(obj, payload, version=1)
    obj = db.jsonable(e.conn.execute('''UPDATE gov_objects SET latest_revision_id=%s,effective_revision_id=%s
        WHERE scope_id=%s AND object_id=%s RETURNING *''',
        (revision['revision_id'], revision['revision_id'] if effective else None, e.ctx.scope_id, oid)).fetchone())
    e.heads[oid] = e.changed[oid] = obj; e.event(obj, None, e.kind)
    return obj, revision


def open_round(e):
    p = e.params
    if e.conn.execute('SELECT 1 FROM gov_formation_round_state WHERE scope_id=%s AND company_id=%s AND period_id=%s',
                      (e.ctx.scope_id, p['company_id'], p['period_id'])).fetchone(): fail('INVALID_STATE')
    definition = deepcopy(p)
    definition.update(ceo_principal_id=e.a2_ceo['principal_id'], members=e.a2_members, member_set_version=1,
        method_profile_ref={'profile_id': e.creation_fields['profile_id'], 'revision': e.creation_fields['profile_revision'],
                            'canonical_hash': e.creation_fields['profile_canonical_hash']})
    definition = a2_models.RoundDefinitionPayload.model_validate(definition).model_dump(mode='json', exclude_none=True)
    obj, rev = new_object(e, 'FormationRound', e.domain_id, definition, 'open', effective=True)
    e.conn.execute('''INSERT INTO gov_formation_round_state(object_id,scope_id,company_id,company_domain_id,
        period_id,member_set_version,input_set_version) VALUES (%s,%s,%s,%s,%s,1,1)''',
        (obj['object_id'], e.ctx.scope_id, p['company_id'], e.domain_id, p['period_id']))
    return {'round_object_id': obj['object_id'], 'round_revision_id': rev['revision_id'],
            'member_set_version': 1, 'input_set_version': 1, 'members': e.a2_members}


def amend_round(e):
    p, before = e.params, e.target; current = state(e, before['object_id'])
    if p['expected_member_set_version'] != current['member_set_version']: fail('COMPOSITION_INPUT_CHANGED')
    previous = e.target_revision['payload']; updated = deepcopy(previous)
    updated.update(members=e.a2_members, ceo_assignment_id=e.a2_ceo['assignment_id'], ceo_principal_id=e.a2_ceo['principal_id'],
                   company_reference_ref=p.get('company_reference_ref', previous['company_reference_ref']))
    if all(updated[key] == previous[key] for key in ('members', 'ceo_assignment_id', 'ceo_principal_id', 'company_reference_ref')): fail('INVALID_STATE')
    updated.update(member_set_version=current['member_set_version']+1, change_reason=p['change_reason'])
    updated = a2_models.RoundDefinitionPayload.model_validate(updated).model_dump(mode='json', exclude_none=True)
    revision = e.insert_revision(before, updated, version=before['object_version']+1)
    e.bump(before, latest=revision['revision_id'], effective=revision['revision_id'])
    e.conn.execute('''UPDATE gov_formation_round_state SET member_set_version=member_set_version+1,
        input_set_version=input_set_version+1,updated_at=clock_timestamp() WHERE scope_id=%s AND object_id=%s''', (e.ctx.scope_id, before['object_id']))
    return {'round_object_id': before['object_id'], 'round_revision_id': revision['revision_id'], 'members': e.a2_members,
            'member_set_version': updated['member_set_version'], 'input_set_version': current['input_set_version']+1}


def publish_submission(e):
    p, round_id = e.params, e.target['object_id']
    payload = a2_models.DomainSubmissionPayload.model_validate(dict(p, round_object_id=round_id, dri_principal_id=e.ctx.principal_id)).model_dump(mode='json', exclude_none=True)
    sub, rev = new_object(e, 'DomainSubmission', e.domain_id, payload, 'submitted', round_id=round_id)
    ref = {'object_id': sub['object_id'], 'revision_id': rev['revision_id'], 'payload_hash': rev['payload_hash']}; mission_refs = []
    for m in p['submission']['missions']:
        body = a2_models.MissionPayload.model_validate(dict(m, round_object_id=round_id, domain_id=e.domain_id, origin_submission_ref=ref)).model_dump(mode='json', exclude_none=True)
        index = e.conn.execute('''SELECT mission_object_id FROM gov_mission_index
            WHERE scope_id=%s AND round_object_id=%s AND domain_id=%s AND mission_key=%s''',
            (e.ctx.scope_id, round_id, e.domain_id, m['mission_key'])).fetchone()
        if index:
            obj = e.head(str(index['mission_object_id'])); revision = e.insert_revision(obj, body, version=obj['object_version']+1)
            obj = e.bump(obj, status='draft', latest=revision['revision_id'])
        else:
            obj, revision = new_object(e, 'Mission', e.domain_id, body, 'draft', round_id=round_id)
            e.conn.execute('''INSERT INTO gov_mission_index(scope_id,round_object_id,domain_id,mission_key,mission_object_id)
                VALUES (%s,%s,%s,%s,%s)''', (e.ctx.scope_id, round_id, e.domain_id, m['mission_key'], obj['object_id']))
        mission_refs.append({'mission_key': m['mission_key'], 'mission_object_id': obj['object_id'], 'mission_revision_id': revision['revision_id']})
    e.conn.execute('''INSERT INTO gov_round_formal_submissions(scope_id,round_object_id,domain_id,submission_object_id,
        submission_revision_id,published_by_principal_id,published_by_assignment_id,action_id) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
        ON CONFLICT(scope_id,round_object_id,domain_id) DO UPDATE SET submission_object_id=EXCLUDED.submission_object_id,
        submission_revision_id=EXCLUDED.submission_revision_id,published_by_principal_id=EXCLUDED.published_by_principal_id,
        published_by_assignment_id=EXCLUDED.published_by_assignment_id,action_id=EXCLUDED.action_id,published_at=clock_timestamp()''',
        (e.ctx.scope_id, round_id, e.domain_id, sub['object_id'], rev['revision_id'], e.ctx.principal_id, p['dri_assignment_id'], e.action_id))
    row = e.conn.execute('''UPDATE gov_formation_round_state SET input_set_version=input_set_version+1,
        updated_at=clock_timestamp() WHERE scope_id=%s AND object_id=%s RETURNING input_set_version''', (e.ctx.scope_id, round_id)).fetchone()
    e.bump(e.target, detail={'submission_ref': ref, 'input_set_version': row['input_set_version']})
    return {'submission_object_id': sub['object_id'], 'submission_revision_id': rev['revision_id'], 'payload_hash': rev['payload_hash'],
            'input_set_version': row['input_set_version'], 'missions': mission_refs}
