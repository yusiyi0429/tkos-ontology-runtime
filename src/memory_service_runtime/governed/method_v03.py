"""Anchor actions use the existing authorization, CAS and receipt transaction."""
from copy import deepcopy
from datetime import datetime
from hashlib import sha256
from psycopg.types.json import Jsonb
from . import db, method_m1a as a, method_v02 as v2
from .errors import GovernedError


def fail(message, code='INVALID_REQUEST'):
    raise GovernedError(code, message)


def ceo_agent_owner(e):
    e.require_role('CEO_AGENT', principal_type='agent')
    rows = e.conn.execute('SELECT owner_principal_id FROM gov_method_agent_bindings WHERE scope_id=%s AND agent_principal_id=%s',
                          (e.ctx.scope_id, e.ctx.principal_id)).fetchall()
    owners = set()
    for row in db.jsonable(rows):
        try:
            e.validate_principal(row['owner_principal_id'], 'human', 'CEO', e.domain_id)
            e.check_personal_agent(e.ctx.principal_id, row['owner_principal_id'])
            owners.add(row['owner_principal_id'])
        except GovernedError:
            continue
    if len(owners) != 1:
        fail('A unique current same-domain CEO binding is required.', 'FORBIDDEN')
    return next(iter(owners))


def architecture_check(e, payload, old=None):
    for unit in payload['units']:
        # A referenced domain must exist in this scope; definitions do not grant access.
        row = e.conn.execute('SELECT 1 FROM gov_domains WHERE scope_id=%s AND domain_id=%s',
                             (e.ctx.scope_id, unit['domain_id'])).fetchone()
        if not row:
            fail('Unknown responsibility domain.')
    if old:
        history = e.conn.execute('SELECT payload FROM gov_object_revisions WHERE scope_id=%s AND object_id=%s',
                                 (e.ctx.scope_id, old['object_id'])).fetchall()
        prior = {u['unit_id']: u['unit_type'] for r in history for u in r['payload']['units']}
        if any(u['unit_id'] in prior and prior[u['unit_id']] != u['unit_type'] for u in payload['units']):
            fail('Stable definition IDs cannot change their nature.')


def current_architecture(e, strategy_ref):
    strategy, _ = e.current_strategy(strategy_ref)
    reference = e.state(strategy).get('architecture_ref')
    if not reference:
        fail('The Strategy has no confirmed Architecture.', 'INVALID_STATE')
    head, revision = e.ref(reference, types={'StrategicArchitecture'}, effective=True, current=False)
    return head, revision


def target_architecture(e, kind, payload, *, pco_payload=None, historical=False):
    # Called by every existing M1B validator, including candidate confirmation.
    ref = payload['architecture_ref']
    head, revision = e.ref(ref, types={'StrategicArchitecture'}, effective=not historical, current=False)
    if not historical:
        strategy_ref = (pco_payload or {}).get('strategy_ref') if kind == 'Mission' else payload['strategy_ref']
        if strategy_ref is None:
            _, pco = e.ref(payload['pco_ref'], types={'PCO'}, current=False)
            strategy_ref = pco['payload']['strategy_ref']
            pco_payload = pco['payload']
        current = current_architecture(e, strategy_ref)
        if not a._same(ref, e.exact_ref(*current)):
            fail('Explicitly adopt the current Architecture before publishing targets.', 'STALE_DEPENDENCY')
    units = {u['unit_id'] for u in revision['payload']['units']}
    if kind == 'Mission':
        if payload['primary_scope_id'] not in units:
            fail('Mission Primary Scope is absent from its Architecture.')
        if pco_payload and not a._same(ref, pco_payload['architecture_ref']):
            fail('Mission and PCO must use the same Architecture.')
    else:
        values = payload['outcomes' if kind == 'LTCO' else 'unit_outcomes']
        if kind == 'PCO':
            domains={u['unit_id']:u['domain_id'] for u in revision['payload']['units']}
            for outcome in values:
                if outcome['unit_id'] in domains:
                    e.validate_principal(outcome['dri_principal_id'],'human','DOMAIN_DRI',domains[outcome['unit_id']])
        if not {x['unit_id'] for x in values}.issubset(units):
            fail('Outcome responsibility is absent from Architecture.')


def subject_owner(e, subject, *, effective=True):
    head, revision = e.ref(subject, types={'Mission', 'LTCO', 'PCO'}, effective=effective, current=False)
    payload = revision['payload']
    item = None
    if subject.get('outcome_id'):
        item = next((x for x in payload.get('outcomes', payload.get('unit_outcomes', []))
                     if x['outcome_id'] == subject['outcome_id']), None)
        if item is None:
            fail('Unknown outcome in the exact subject revision.')
    if head['object_type'] == 'Mission':
        owner, role = payload['owner_principal_id'], None
    elif head['object_type'] == 'LTCO':
        owner, role = payload['owner_principal_id'], 'CEO'
    elif item:
        owner, role = item['dri_principal_id'], 'DOMAIN_DRI'
    else:
        rows = e.conn.execute("SELECT principal_id FROM gov_role_assignments WHERE scope_id=%s AND domain_id=%s AND role='CEO'",
                              (e.ctx.scope_id, head['domain_id'])).fetchall()
        owners = set()
        for row in db.jsonable(rows):
            try:
                e.validate_principal(row['principal_id'], 'human', 'CEO', head['domain_id'])
                owners.add(row['principal_id'])
            except GovernedError:
                continue
        if len(owners) != 1:
            fail('A unique current company CEO is required.', 'FORBIDDEN')
        owner, role = next(iter(owners)), 'CEO'
    e.validate_principal(owner, 'human', role)
    return head, revision, owner


def state_check(e, payload):
    head, revision, owner = subject_owner(e, payload['subject_ref'])
    if head['domain_id'] != e.domain_id:
        fail('State must be stored in its subject domain.', 'FORBIDDEN')
    if not any(a._same(r, payload['subject_ref']) for r in payload['baseline_refs']):
        fail('State baseline must include the exact subject commitment.')
    permitted = [payload['subject_ref'], *[revision['payload'][key] for key in ('architecture_ref', 'pco_ref', 'ltco_ref') if key in revision['payload']]]
    if any(not any(a._same(ref, basis) for basis in permitted) for ref in payload['baseline_refs']):
        fail('State baselines must belong to this exact commitment and its direct basis.')
    for ref in payload['baseline_refs']:
        e.ref(ref, types={'Mission', 'LTCO', 'PCO', 'StrategicArchitecture'}, current=False)
    for ref in payload['evidence_refs']:
        e.ref(ref, types={'BusinessFact', 'EvidenceAsset'}, current=False)
    return owner


def canonical(e, ref):
    head, revision = e.ref(ref, types={'OperatingState'}, effective=True, current=False)
    if not a._same(e.state(head).get('canonical_ref'), ref):
        fail('The cited State is not canonical.', 'STALE_DEPENDENCY')
    subject_owner(e, revision['payload']['subject_ref'])
    return head, revision


def state_key(payload):
    s = payload['subject_ref']
    return s['object_id'], s.get('outcome_id', ''), datetime.fromisoformat(payload['as_of'])


def problem_key(payload, state_payload):
    s = state_payload['subject_ref']
    return s['object_id'], s.get('outcome_id', ''), sha256(' '.join(payload['core_question'].split()).encode()).hexdigest()


def problem_check(e, payload):
    state_head, state_rev = canonical(e, payload['state_ref'])
    subject, revision, owner = subject_owner(e, state_rev['payload']['subject_ref'])
    if state_head['domain_id'] != e.domain_id:
        fail('Problem must remain in its State domain.', 'FORBIDDEN')
    assignment = e.validate_assignment(payload['responsible_assignment_id'], principal_type='human')
    level = payload['level']
    if level == 'mission':
        if subject['object_type'] != 'Mission' or assignment['principal_id'] != owner:
            fail('Mission problems belong to the exact Mission Owner.', 'FORBIDDEN')
    elif level == 'domain':
        if assignment['role'] != 'DOMAIN_DRI':
            fail('Domain problems require a DRI.', 'FORBIDDEN')
        content = revision['payload']
        arch_ref = content['architecture_ref']
        _, arch = e.ref(arch_ref, types={'StrategicArchitecture'}, current=False)
        unit_id = content.get('primary_scope_id')
        if not unit_id:
            outcome = state_rev['payload']['subject_ref'].get('outcome_id')
            unit_id = next((x['unit_id'] for x in content.get('unit_outcomes', content.get('outcomes', [])) if x['outcome_id'] == outcome), None)
        domain = next((x['domain_id'] for x in arch['payload']['units'] if x['unit_id'] == unit_id), None)
        if not domain or assignment['domain_id'] != domain:
            fail('Problem DRI must own its Architecture responsibility domain.', 'FORBIDDEN')
    elif assignment['role'] != 'CEO' or assignment['domain_id'] != e.domain_id:
        fail('Company and strategic problems require the domain CEO.', 'FORBIDDEN')
    for ref in payload['evidence_refs']:
        e.ref(ref, types={'BusinessFact', 'EvidenceAsset'}, current=False)
    return state_rev['payload'], assignment


def potential_sources(e, payload):
    for ref in payload['source_refs']:
        head, _ = e.ref(ref, types={'PeriodReview', 'OperatingProblem', 'EvidenceAsset', 'Signal'}, current=False)
        if head['object_type'] == 'OperatingProblem':
            a._phase(e.state(head), 'open')
            _, revision = e.ref(ref, current=True)
            if revision['payload']['level'] != 'strategic':
                fail('Only strategic-level Problems can transfer into M1A.')


def collect(e):
    k, p = e.kind, e.params
    e._v03 = {}
    if k == 'method_open_run' and e.ctx.principal_type == 'agent':
        ceo_agent_owner(e)
        if p['payload']['method'] != 'M1A':
            fail('CEO Agent may open only M1A intake runs.')
        return
    if k == 'method_attach_run' and e.ctx.principal_type == 'agent':
        ceo_agent_owner(e)
        row = e.conn.execute('SELECT owner_principal_id,phase FROM gov_method_runs WHERE scope_id=%s AND run_id=%s', (e.ctx.scope_id, e.target['object_id'])).fetchone()
        if not row or str(row['owner_principal_id']) != e.ctx.principal_id or row['phase'] != 'running':
            fail('Only the intake run owner can attach roots.', 'FORBIDDEN')
        e.ref(p['object_ref'], current=False)
        existing = e.conn.execute('SELECT run_id FROM gov_method_run_members WHERE scope_id=%s AND object_id=%s', (e.ctx.scope_id,p['object_ref']['object_id'])).fetchone()
        if existing and str(existing['run_id']) != e.target['object_id']:
            fail('Object already belongs to another run.', 'INVALID_STATE')
        return
    if k in {'m1a_open_potential_issue', 'm1a_revise_potential_issue'}:
        a._source_actor(e)
        if e.target:
            a._phase(e.state(e.target), 'potential')
            if e.state(e.target)['created_by'] != e.ctx.principal_id:
                ceo_agent_owner(e)
        potential_sources(e, p['payload'])
        return
    if k == 'm1a_confirm_strategic_issue':
        e._v03['ceo'] = ceo_agent_owner(e)
        if len(e.run_ids) != 1:
            fail('Intake requires one explicit MethodRun.', 'INVALID_STATE')
        run = e.head(next(iter(e.run_ids)))
        run_payload = e.revision(run['object_id'],run['latest_revision_id'])['payload']
        if run_payload['method'] not in {'M1A','M1A+M1B'}:
            fail('Strategic intake requires an M1A run.', 'INVALID_STATE')
        a._phase(e.state(e.target), 'potential')
        potential_sources(e, e.target_revision['payload'])
        if p.get('existing_issue_ref'):
            head, _ = e.ref(p['existing_issue_ref'], types={'StrategicIssue'}, current=True)
            if head['domain_id'] != e.domain_id or e.state(head).get('phase') == 'completed':
                fail('Associate a live issue in the same domain.')
        return
    if k in {'method_propose_architecture', 'method_revise_architecture', 'method_confirm_architecture'}:
        payload = p.get('payload', e.target_revision['payload'] if e.target else None)
        e.current_strategy(payload['strategy_ref'])
        architecture_check(e, payload, e.target)
        if k == 'method_confirm_architecture':
            e.require_role('CEO')
            a._phase(e.state(e.target), 'proposed')
        else:
            a._source_actor(e)
            if payload.get('source_proposal_ref'):
                fail('Architecture provenance is server assigned.')
        strategy, _ = e.current_strategy(payload['strategy_ref'])
        old = e.state(strategy).get('architecture_ref')
        if old:
            e.ref(old, current=False)
            if not e.target or old['object_id'] != e.target['object_id']:
                fail('Revise the existing Architecture instead of creating a parallel definition.', 'INVALID_STATE')
        return
    if k in {'method_propose_state', 'method_confirm_state'}:
        payload = p.get('payload', e.target_revision['payload'] if e.target else None)
        owner = state_check(e, payload)
        if k == 'method_confirm_state':
            e.require_actor(owner)
            a._phase(e.state(e.target), 'proposed')
            if p.get('rag') and p['rag'] != 'unknown' and not payload['evidence_refs']:
                fail('Missing evidence cannot be overridden into a known rating.')
        else:
            if e.ctx.principal_type == 'human':
                e.require_actor(owner)
            else:
                a._source_actor(e)
            row = e.conn.execute('SELECT state_id FROM gov_method_state_keys WHERE scope_id=%s AND subject_id=%s AND outcome_id=%s AND as_of=%s', (e.ctx.scope_id, *state_key(payload))).fetchone()
            previous = p.get('previous_state_ref')
            if row and (not previous or str(row['state_id']) != previous['object_id']):
                fail('Reuse the existing State identity with its exact previous revision.', 'VERSION_CONFLICT')
            if previous:
                head, old = e.ref(previous, types={'OperatingState'})
                if not row or state_key(old['payload']) != state_key(payload):
                    fail('A new recommendation must preserve the State identity.')
        return
    if k in {'method_open_problem', 'method_revise_problem', 'method_close_problem'}:
        if k == 'method_close_problem':
            a._phase(e.state(e.target), 'open')
            old = e.target_revision['payload']
            assignment = e.validate_assignment(old['responsible_assignment_id'], principal_type='human')
            e.require_actor(assignment['principal_id'])
            for ref in p['evidence_refs']:
                e.ref(ref, current=False)
            return
        state_payload, assignment = problem_check(e, p['payload'])
        if e.target:
            a._phase(e.state(e.target), 'open')
            old = e.target_revision['payload']
            prior = e.validate_assignment(old['responsible_assignment_id'], principal_type='human')
            e.require_actor(prior['principal_id'])
            _, sr = e.ref(old['state_ref'], current=False)
            if problem_key(old, sr['payload']) != problem_key(p['payload'], state_payload):
                fail('Revising a Problem preserves its core question and subject.')
        elif e.ctx.principal_type == 'human':
            e.require_actor(assignment['principal_id'])
        else:
            a._source_actor(e)
        if not e.target:
            row = e.conn.execute('SELECT problem_id FROM gov_method_problem_keys WHERE scope_id=%s AND subject_id=%s AND outcome_id=%s AND question_hash=%s', (e.ctx.scope_id, *problem_key(p['payload'], state_payload))).fetchone()
            if row:
                fail('This question already has a Problem; explicitly revise or associate it.', 'VERSION_CONFLICT')
        return
    if k.startswith('method_'):
        return e._collect_run()
    v2.collect(e)
    if k in {'m1b_generate_review', 'm1b_regenerate_review'}:
        covered = set()
        subjects = []
        for ref in p['payload']['state_refs']:
            _, sr = canonical(e, ref)
            covered.add(sr['payload']['subject_ref']['object_id'])
            subjects.append(sr['payload']['subject_ref'])
        if covered != {r['object_id'] for r in p['payload']['target_refs']} or any(not any(a._same(r,s) and not s.get('outcome_id') for s in subjects) for r in p['payload']['target_refs']):
            fail('Review must cite canonical States for exactly its target objects.')
    if k in {'m1a_propose_update', 'm1a_review_update', 'm1a_confirm_update'}:
        proposal = p['payload'] if k == 'm1a_propose_update' else e._m1a['proposal']
        for change in proposal['changes']:
            if change['scope'] == 'company':
                definition = change['architecture']
                architecture_check(e, definition)
                units = {u['unit_id']:u['unit_type'] for u in definition['units']}
                if units != {u['unit_id']:u['unit_type'] for u in change['payload']['map']['units']}:
                    fail('Strategy map and Architecture must name the same definition IDs and types.')
                if change.get('target_ref'):
                    old, _ = current_architecture(e, change['target_ref'])
                    architecture_check(e, definition, old)


def activate_architecture(e, strategy_ref, definition, proposal_ref, previous=None):
    payload = {**definition, 'strategy_ref': strategy_ref, 'source_proposal_ref': proposal_ref}
    if previous:
        old, _ = e.ref(previous, current=False)
        head, revision = e.revise(old, payload, status='active', effective=True)
    else:
        head, revision = e.create('StrategicArchitecture', payload, status='active')
    ref = e.exact_ref(head, revision)
    e.set_state(head, {'phase':'confirmed'})
    strategy, _ = e.ref(strategy_ref, current=False)
    state = e.state(strategy)
    state['architecture_ref'] = ref
    e.set_state(strategy, state)
    e.transition(strategy)
    e.review('architecture_confirmation', ref, {'strategy_ref': strategy_ref, 'source_proposal_ref': proposal_ref})
    return ref


def run(e):
    collect(e)
    k, p = e.kind, e.params
    if k == 'method_open_run' or k == 'method_attach_run':
        return e._run_action()
    if k in {'m1a_open_potential_issue', 'm1a_revise_potential_issue'}:
        head, revision = e.revise(e.target, p['payload']) if e.target else e.create('PotentialIssue', p['payload'])
        e.set_state(head, {'phase':'potential', 'created_by':e.state(head).get('created_by', e.ctx.principal_id)})
        return e.exact_ref(head, revision)
    if k == 'm1a_confirm_strategic_issue':
        source = e.target_revision['payload']
        potential_ref = e.exact_ref(e.target, e.target_revision)
        ref = p.get('existing_issue_ref')
        if ref:
            head, revision = e.ref(ref, types={'StrategicIssue'})
        else:
            head, revision = e.create('StrategicIssue', {**source, 'potential_issue_ref':potential_ref, 'confirmation_reason':p['reason']}, status='active')
            ref = e.exact_ref(head, revision)
            e.set_state(head, {'phase':'issue_confirmed', 'ceo_principal_id':e._v03['ceo'], 'initiating_agent_id':e.ctx.principal_id})
        issue_state = e.state(head)
        issue_state.setdefault('source_refs', [])
        for src in source['source_refs']:
            if src not in issue_state['source_refs']:
                issue_state['source_refs'].append(src)
            origin, _ = e.ref(src, current=False)
            if origin['object_type'] == 'OperatingProblem':
                st = e.state(origin)
                st.update(phase='transferred', tracking=False, issue_ref=ref, transfer_reason=p['reason'])
                e.set_state(origin, st)
                e.transition(origin)
        e.checkpoint('after_method_problem_transfer')
        e.set_state(head, issue_state)
        e.transition(head)
        e.set_state(e.target, {'phase':'promoted', 'strategic_issue_ref':ref})
        e.transition(e.target)
        record = e.review('agent_issue_initiation', ref, {'potential_ref':potential_ref, 'sources':source['source_refs'], 'reason':p['reason']})
        return {**ref, 'issue_ref':ref, 'phase':issue_state['phase'], 'review_record_id':record}
    if k in {'method_propose_architecture', 'method_revise_architecture'}:
        head, rev = e.revise(e.target, p['payload']) if e.target else e.create('StrategicArchitecture', p['payload'])
        e.set_state(head, {'phase':'proposed'})
        return e.exact_ref(head, rev)
    if k == 'method_confirm_architecture':
        e.transition(e.target, status='active', effective=True)
        ref = e.exact_ref(e.target, e.target_revision)
        strategy, _ = e.current_strategy(e.target_revision['payload']['strategy_ref'])
        state = e.state(strategy)
        state['architecture_ref'] = ref
        e.set_state(strategy, state)
        e.transition(strategy)
        e.set_state(e.target, {'phase':'confirmed'})
        return {**ref, 'review_record_id':e.review('architecture_confirmation', ref, p)}
    if k == 'method_propose_state':
        previous = p.get('previous_state_ref')
        if previous:
            head, _ = e.ref(previous)
            head, rev = e.revise(head, p['payload'])
        else:
            head, rev = e.create('OperatingState', p['payload'])
            e.conn.execute('INSERT INTO gov_method_state_keys(scope_id,subject_id,outcome_id,as_of,state_id) VALUES(%s,%s,%s,%s,%s)', (e.ctx.scope_id,*state_key(p['payload']),head['object_id']))
        st = e.state(head)
        st.update(phase='proposed', recommendation_ref=e.exact_ref(head, rev))
        e.set_state(head, st)
        return e.exact_ref(head, rev)
    if k == 'method_confirm_state':
        original = e.exact_ref(e.target, e.target_revision)
        head, rev = e.target, e.target_revision
        if p.get('rag'):
            head, rev = e.revise(head, {**rev['payload'], 'rag':p['rag'], 'summary':p['summary']})
        e.transition(head, status='active', effective=True)
        ref = e.exact_ref(head, rev)
        st = e.state(head)
        st.update(phase='confirmed', canonical_ref=ref, confirmed_by=e.ctx.principal_id)
        e.set_state(head, st)
        return {**ref, 'review_record_id':e.review('state_confirmation', ref, {**p, 'recommendation_ref':original})}
    if k in {'method_open_problem', 'method_revise_problem'}:
        head, rev = e.revise(e.target,p['payload']) if e.target else e.create('OperatingProblem',p['payload'],status='active')
        if not e.target:
            _, sr = e.ref(p['payload']['state_ref'], current=False)
            e.conn.execute('INSERT INTO gov_method_problem_keys(scope_id,subject_id,outcome_id,question_hash,problem_id) VALUES(%s,%s,%s,%s,%s)', (e.ctx.scope_id,*problem_key(p['payload'],sr['payload']),head['object_id']))
        else:
            e.transition(head,status='active',effective=True)
        e.set_state(head, {'phase':'open','tracking':True})
        return e.exact_ref(head,rev)
    if k == 'method_close_problem':
        e.set_state(e.target, {'phase':p['disposition'], 'tracking':False, 'reason':p['reason']})
        e.transition(e.target)
        ref=e.exact_ref(e.target,e.target_revision)
        return {**ref,'review_record_id':e.review('problem_closure',ref,p)}
    if k.startswith('method_'):
        return e._run_action()
    result = v2.run(e)
    if k == 'm1a_confirm_update':
        result['architecture_refs'] = [e.state(e.head(ref['object_id']))['architecture_ref']
            for ref in result['changed_refs'] if e.head(ref['object_id'])['object_type']=='Strategy']
    return result
