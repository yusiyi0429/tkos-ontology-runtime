"""Shared constants and application grants for local Method acceptance databases.

Application-role privileges come from the release rules via
``acceptance.execution_a3_independent.database.grants``.

Creating and upgrading an isolated Method database is done by
``acceptance/method_v05/database.py``: it takes the endpoint from the base env and
checks that it is the isolated acceptance container's published port. The
original create/upgrade helpers here pinned 54350 and exact migration lists, so
they were removed. Imports do no work.
"""
from __future__ import annotations

from acceptance.execution_a3_independent.database import grants

BASE_COMMIT = '1b8cec9cc30e561e3570bc5dd010a09126f283c2'
CONTAINER = 'tkos-ontology-runtime-acceptance-postgres-1'
BASE_MUTABLE = {'gov_execution_state', 'gov_a3_work_item_state'}
METHOD_MUTABLE = {'gov_method_state', 'gov_method_strategy_heads', 'gov_method_runs'}
METHOD_TABLES = METHOD_MUTABLE | {
    'gov_method_reviews', 'gov_method_impacts', 'gov_method_run_attempts',
    'gov_method_run_members', 'gov_method_agent_bindings',
}


# Method 表的应用角色权限同样由发布规则给出（gov_method_agent_bindings 只读），不再单独收窄。
method_grants = grants
