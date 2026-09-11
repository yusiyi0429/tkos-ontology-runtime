"""Exact-manifest confirmation and atomic effectivation under the scope row fence."""
from uuid import uuid4

from psycopg.types.json import Jsonb

from . import a2_composition, a2_models, a2_rounds, checkpoints
from .errors import GovernedError


def confirmations(e, manifest):
    return e.conn.execute(
        """SELECT principal_id,assignment_id,responsibility_role,manifest_hash
           FROM gov_composition_confirmations WHERE scope_id=%s
             AND composition_object_id=%s AND composition_revision_id=%s""",
        (e.ctx.scope_id, e.target['object_id'], e.target_revision['revision_id']),
    ).fetchall()


def confirm(e):
    manifest = a2_composition.validate_current(e)['manifest']
    slot = next(s for s in manifest['required_signers']
                if s['principal_id'] == e.ctx.principal_id
                and s['assignment_id'] == e.params['assignment_id'])
    previous = confirmations(e, manifest)
    if any(str(r['principal_id']) == slot['principal_id']
           or str(r['assignment_id']) == slot['assignment_id'] for r in previous):
        raise GovernedError('INVALID_STATE')
    confirmation_id = str(uuid4())
    e.conn.execute(
        """INSERT INTO gov_composition_confirmations
           (confirmation_id,scope_id,composition_object_id,composition_revision_id,
            manifest_hash,principal_id,assignment_id,responsibility_role,
            confirmation_statement,action_id) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
        (confirmation_id, e.ctx.scope_id, e.target['object_id'], e.target_revision['revision_id'],
         manifest['manifest_hash'], slot['principal_id'], slot['assignment_id'],
         slot['responsibility_role'], e.params['confirmation_statement'], e.action_id),
    )
    e.bump(e.target, detail={'confirmation_id': confirmation_id, 'manifest_hash': manifest['manifest_hash']})
    return {'confirmation_id': confirmation_id, 'composition_ref': e.params['composition_ref'],
            'signed_count': len(previous) + 1, 'required_count': len(manifest['required_signers']),
            'complete': len(previous) + 1 == len(manifest['required_signers'])}


def activate(e):
    context = a2_composition.validate_current(e)
    manifest = context['manifest']
    required = {(s['principal_id'], s['assignment_id'], s['responsibility_role'])
                for s in manifest['required_signers']}
    signed = {(str(r['principal_id']), str(r['assignment_id']), r['responsibility_role'])
              for r in confirmations(e, manifest) if r['manifest_hash'] == manifest['manifest_hash']}
    if signed != required:
        raise GovernedError('CONFIRMATION_INCOMPLETE')
    round_id = manifest['round_id']
    if context['state']['activated_composition_object_id'] is not None:
        raise GovernedError('INVALID_STATE')
    commitments, activated_missions = [], []
    for index, member in enumerate(manifest['members']):
        domain = member['domain_id']
        submission = context['formal_submissions'][domain]['payload']['submission']
        mission_refs = []
        for definition in submission['missions']:
            row = e.conn.execute(
                """SELECT mission_object_id FROM gov_mission_index WHERE scope_id=%s
                   AND round_object_id=%s AND domain_id=%s AND mission_key=%s""",
                (e.ctx.scope_id, round_id, domain, definition['mission_key']),
            ).fetchone()
            if row is None:
                raise GovernedError('COMPOSITION_INPUT_CHANGED')
            head = e.head(str(row['mission_object_id']))
            revision = e.revision(head['object_id'], head['latest_revision_id'])
            expected_payload = {'round_object_id': round_id, 'domain_id': domain,
                                'origin_submission_ref': member['submission_ref'], **definition}
            if revision['payload'] != expected_payload or head['object_type'] != 'Mission':
                raise GovernedError('COMPOSITION_INPUT_CHANGED')
            e.bump(head, status='active', effective=revision['revision_id'],
                   detail={'composition_ref': e.params['composition_ref'],
                           'origin_submission_ref': member['submission_ref']})
            ref = {'mission_key': definition['mission_key'], 'object_id': head['object_id'],
                   'revision_id': revision['revision_id']}
            mission_refs.append(ref)
            activated_missions.append({'domain_id': domain, **ref})
        payload = a2_models.DomainCommitmentPayload.model_validate({
            'round_object_id': round_id, 'domain_id': domain,
            'composition_ref': e.params['composition_ref'], 'submission_ref': member['submission_ref'],
            'mission_refs': mission_refs,
        }).model_dump(mode='json')
        obj, rev = a2_rounds.new_object(e, 'DomainCommitment', domain, payload, 'active',
                                      round_id=round_id, effective=True)
        commitments.append({'domain_id': domain, 'object_id': obj['object_id'], 'revision_id': rev['revision_id']})
        if index == 0:
            checkpoints.checkpoint('after_first_member_activation', {'action_type': e.kind, 'receipt_id': e.action_id})
    detail = {'domain_commitments': commitments, 'missions': activated_missions,
              'composition_ref': e.params['composition_ref']}
    e.bump(e.target, status='active', effective=e.target_revision['revision_id'], detail=detail)
    e.bump(context['round_object'], status='active', detail={'composition_ref': e.params['composition_ref']})
    e.conn.execute(
        """UPDATE gov_formation_round_state SET activated_composition_object_id=%s,
           activated_composition_revision_id=%s,activated_at=clock_timestamp(),updated_at=clock_timestamp()
           WHERE scope_id=%s AND object_id=%s""",
        (e.target['object_id'], e.target_revision['revision_id'], e.ctx.scope_id, round_id),
    )
    activation_id = str(uuid4())
    e.conn.execute(
        """INSERT INTO gov_activation_records
           (activation_id,scope_id,round_object_id,composition_object_id,composition_revision_id,
            manifest_hash,member_set_version,input_set_version,detail,action_id)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
        (activation_id, e.ctx.scope_id, round_id, e.target['object_id'], e.target_revision['revision_id'],
         manifest['manifest_hash'], manifest['member_set_version'], manifest['input_set_version'], Jsonb(detail), e.action_id),
    )
    return {'activation_id': activation_id, **detail, 'member_set_version': manifest['member_set_version'],
            'input_set_version': manifest['input_set_version']}
