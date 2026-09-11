"""Synthetic identities/policies only; business successes always use HTTP."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
from pathlib import Path
import secrets
import uuid

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from acceptance.composition_a2_independent.fixture import (
    seed_authority as seed_a2_authority, register_contract,
)
from acceptance.protocol_a1_independent.support import private_json


def uid():
    return str(uuid.uuid4())


def seed_authority(env, path: Path, label: str, *, reviewer_seconds=None):
    f = seed_a2_authority(env, path, label)
    with psycopg.connect(env.values['MIGRATION_DATABASE_URL'], row_factory=dict_row) as conn:
        conn.execute("SELECT set_config('app.runtime_write_capability','tkos-runtime-a1',true)")
        conn.execute("SELECT set_config('app.gov_control_plane','on',true)")
        conn.execute("SELECT set_config('app.governed_scope_id',%s,true)", (f['scope_id'],))
        conn.execute('SELECT scope_id FROM gov_scopes WHERE scope_id=%s FOR UPDATE', (f['scope_id'],))
        for name, role, domain, kind in [
            ('ic', 'IC', 'b', 'human'), ('reviewer', 'VERIFIER', 'b', 'human'),
            ('wrong_ic', 'IC', 'b', 'human'), ('agent_ic', 'IC', 'b', 'agent'),
            ('ic_a', 'IC', 'a', 'human'), ('reviewer_a', 'VERIFIER', 'a', 'human'),
        ]:
            principal, assignment, token = uid(), uid(), secrets.token_urlsafe(48)
            until = (datetime.now(timezone.utc) + timedelta(seconds=reviewer_seconds)
                     if name == 'reviewer' and reviewer_seconds else None)
            conn.execute('''INSERT INTO gov_principals
                (principal_id,scope_id,principal_type,display_name) VALUES (%s,%s,%s,%s)''',
                (principal, f['scope_id'], kind, 'Synthetic A3 ' + name))
            conn.execute('''INSERT INTO gov_role_assignments
                (assignment_id,scope_id,principal_id,domain_id,role,valid_to)
                VALUES (%s,%s,%s,%s,%s,%s)''',
                (assignment, f['scope_id'], principal, f['domains'][domain], role, until))
            digest = hashlib.sha256(token.encode()).hexdigest()
            conn.execute("SELECT set_config('app.governed_credential_digest',%s,true)", (digest,))
            conn.execute('''INSERT INTO gov_credentials
                (scope_id,principal_id,credential_digest,label) VALUES (%s,%s,%s,%s)''',
                (f['scope_id'], principal, digest, 'Synthetic A3 ' + name))
            f['actors'][name] = {'principal_id': principal, 'assignment_id': assignment,
                                 'token': token, 'domain_id': f['domains'][domain],
                                 'role': role, 'valid_to': until.isoformat() if until else None}
        for key, actor, role in [('ic_verifier_assignment', 'ic', 'VERIFIER'),
                                  ('b_as_ic_assignment', 'b', 'IC')]:
            f[key] = uid()
            conn.execute('''INSERT INTO gov_role_assignments
                (assignment_id,scope_id,principal_id,domain_id,role) VALUES (%s,%s,%s,%s,%s)''',
                (f[key], f['scope_id'], f['actors'][actor]['principal_id'], f['domains']['b'], role))
        conn.execute("SELECT set_config('app.governed_credential_digest','',true)")
        f['policy_revision_ids'] = {}
        for domain_name, domain in f['domains'].items():
            old = conn.execute('''SELECT * FROM gov_activation_policies
                WHERE scope_id=%s AND domain_id=%s ORDER BY policy_seq DESC LIMIT 1''',
                (f['scope_id'], domain)).fetchone()
            content = dict(old['content'])
            content['action_roles'] = dict(content['action_roles'])
            content['action_roles'].update({
                'create_object': ['CEO', 'DOMAIN_DRI', 'IC'],
                'propose_revision': ['CEO', 'DOMAIN_DRI', 'IC'],
                'accept_commitment': ['DOMAIN_DRI', 'IC'],
                'activate_commitment': ['DOMAIN_DRI'],
                'accept_work_item': ['IC'], 'submit_deliverable': ['IC'],
                'review_deliverable': ['CEO', 'DOMAIN_DRI', 'VERIFIER', 'IC'],
                'record_outcome_assessment': ['CEO'],
            })
            revision_id = uid()
            f['policy_revision_ids'][domain_name] = revision_id
            conn.execute('''INSERT INTO gov_activation_policies
                (policy_revision_id,scope_id,domain_id,policy_id,policy_seq,content,recorded_by)
                VALUES (%s,%s,%s,%s,%s,%s,%s)''',
                (revision_id, f['scope_id'], domain, old['policy_id'], old['policy_seq'] + 1,
                 Jsonb(content), f['actors']['ceo']['principal_id']))
    private_json(path, f)
    return f


def invalidate_execution_epoch(h, f, object_id: str):
    """Explicit negative-only control fixture; never pretends reassignment exists.

    Advances only the current pointer generation, leaving the real historical
    authority immutable. This invalidates old rights; it creates no authority,
    confirmation, work receipt, success receipt, or new assignee.
    """
    with psycopg.connect(h.env.values['MIGRATION_DATABASE_URL']) as conn:
        conn.execute("SELECT set_config('app.runtime_write_capability','tkos-runtime-a1',true)")
        conn.execute("SELECT set_config('app.governed_scope_id',%s,true)", (f['scope_id'],))
        conn.execute('SELECT scope_id FROM gov_scopes WHERE scope_id=%s FOR UPDATE', (f['scope_id'],))
        result = conn.execute('''UPDATE gov_execution_state
            SET current_execution_epoch=current_execution_epoch+1
            WHERE scope_id=%s AND commitment_object_id=%s''', (f['scope_id'], object_id))
        assert result.rowcount == 1


__all__ = ['seed_authority', 'register_contract', 'invalidate_execution_epoch']
