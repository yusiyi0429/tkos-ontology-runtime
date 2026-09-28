"""Shared constants and application grants for local Method acceptance databases.

Creating and upgrading an isolated Method database is done by
``acceptance/method_v05/database.py``: it takes the endpoint from the base env and
checks that it is the isolated acceptance container's published port. The
original create/upgrade helpers here pinned 54350 and exact migration lists, so
they were removed. Imports do no work.
"""
from __future__ import annotations

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict

from acceptance.execution_a3_independent.database import grants

BASE_COMMIT = '1b8cec9cc30e561e3570bc5dd010a09126f283c2'
CONTAINER = 'tkos-ontology-runtime-acceptance-postgres-1'
BASE_MUTABLE = {'gov_execution_state', 'gov_a3_work_item_state'}
METHOD_MUTABLE = {'gov_method_state', 'gov_method_strategy_heads', 'gov_method_runs'}
METHOD_TABLES = METHOD_MUTABLE | {
    'gov_method_reviews', 'gov_method_impacts', 'gov_method_run_attempts',
    'gov_method_run_members', 'gov_method_agent_bindings',
}


def method_grants(env):
    grants(env, BASE_MUTABLE | METHOD_MUTABLE)
    app = conninfo_to_dict(env.values['APP_DATABASE_URL'])['user']
    with psycopg.connect(env.values['MIGRATION_DATABASE_URL']) as conn:
        conn.execute(sql.SQL('REVOKE INSERT ON gov_method_agent_bindings FROM {}').format(sql.Identifier(app)))
