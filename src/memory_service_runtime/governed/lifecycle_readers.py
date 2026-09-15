"""Read-only lifecycle presentation; action commits remain authoritative."""
from . import db, method_access as access, method_readers
from .errors import GovernedError


def operations(conn, ctx, obj):
    state, kind = obj['method_state'], obj['object_type']
    candidates = {
        'Signal': [('m1a_activate_signal', {'captured', 'archived'}, 'CEO', None),
                   ('m1a_archive_signal', {'captured', 'active', 'converted'}, 'CEO', None)],
        'PotentialIssue': [('m1a_confirm_strategic_issue', {'potential'}, 'CEO', None)],
        'StrategicIssue': [
            ('m1a_assign_research', {'issue_confirmed'}, 'CEO', 'ceo_principal_id'),
            ('m1a_publish_brief', {'research_assigned', 'brief_drafting', 'meeting_ready', 'report_returned'}, 'CEO_AGENT', 'ceo_agent_id'),
            ('m1a_confirm_brief', {'brief_drafting'}, 'CEO', 'ceo_principal_id'),
            ('m1a_open_meeting', {'meeting_ready'}, 'DOMAIN_DRI', 'dri_principal_id'),
            ('m1a_confirm_minutes', {'minutes_reconciled'}, 'DOMAIN_DRI', 'dri_principal_id'),
            ('m1a_confirm_agreement', {'minutes_confirmed'}, 'CEO', 'ceo_principal_id')],
    }.get(kind, [])
    result = []
    for action, phases, role, identity_key in candidates:
        reason = None
        expected_type = 'agent' if role.endswith('AGENT') else 'human'
        if ctx.principal_type != expected_type or (identity_key and state.get(identity_key) != ctx.principal_id):
            reason = 'current_actor_not_permitted'
        else:
            assignments = db._assignments(conn, ctx)
            # Appointed DRI and personal agents act in their current role domain.
            domains = [a['domain_id'] for a in assignments if a['role'] == role
                       and (a['domain_id'] == obj['domain_id'] or role == 'DOMAIN_DRI')]
            if not domains:
                reason = 'current_role_not_permitted'
            else:
                try:
                    db.authorize_domain(conn, ctx, domains[0], action)
                except GovernedError:
                    reason = 'current_policy_not_permitted'
        if not reason and state.get('phase') not in phases:
            reason = 'lifecycle_state_not_permitted'
        result.append({'action_type': action, 'allowed': reason is None, 'reason': reason,
                       'authority': 'advisory_full_dependencies_rechecked_by_method_commit'})
    return result


def review_materials(conn, ctx, object_id):
    target = method_readers.object_state(conn, ctx, object_id)
    if target['object_type'] != 'PCO':
        raise GovernedError('INVALID_REQUEST', 'Review materials require a PCO.')
    # Selection is by the exact authorized target revisions, never by domain counts.
    visible = {v['revision_id'] for key in ('latest_revision', 'effective_revision') if (v := target[key])}
    output = {'pco_object_id': object_id, 'business_facts': [], 'period_reviews': [], 'formal_effect': 'none'}
    rows = conn.execute("SELECT object_id FROM gov_objects WHERE scope_id=%s AND object_type IN ('BusinessFact','PeriodReview') ORDER BY object_id", (ctx.scope_id,)).fetchall()
    for row in rows:
        try:
            obj = method_readers.object_state(conn, ctx, str(row['object_id']))
            revision = obj['latest_revision']
            if not revision:
                continue
            payload = revision['payload']
            refs = [payload['subject_ref']] if obj['object_type'] == 'BusinessFact' else payload['target_refs']
            if not any(ref['object_id'] == object_id and ref['revision_id'] in visible for ref in refs):
                continue
            if obj['protocol']['contract_version'] != target['protocol']['contract_version']:
                continue
            key = 'business_facts' if obj['object_type'] == 'BusinessFact' else 'period_reviews'
            output[key].append(obj)
        except GovernedError as exc:
            if exc.code not in {'NOT_FOUND', 'FORBIDDEN'}:
                raise
    return output
