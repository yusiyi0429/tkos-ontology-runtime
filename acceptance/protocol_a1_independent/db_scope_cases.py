"""Actual app-role scope reads with every public capability setting asserted."""
from __future__ import annotations

import re

from .control_adapter import CONTROL_TABLES, BINDING_TABLE, NotReady
from .support import public_json


def run_scope_and_function_catalog(h, f, *, foreign_fixture, source, catalog):
    if foreign_fixture is None:
        raise NotReady("cross-scope SQL reads need the independently preserved foreign scope")
    assert foreign_fixture["scope_id"] != f["scope_id"]
    tables = [*CONTROL_TABLES, BINDING_TABLE]
    counts = {}
    for table in tables:
        count = h.sql(foreign_fixture, f"SELECT count(*) AS n FROM {table} WHERE scope_id=%s", (foreign_fixture["scope_id"],))[0]["n"]
        assert count > 0, "scope-negative control must contain actual foreign rows"
        counts[table] = count
    with h.app_connection() as conn, conn.transaction():
        conn.execute("SET TRANSACTION READ ONLY")
        for name, value in (("app.governed_scope_id", f["scope_id"]), ("app.gov_control_plane", "on"),
                            ("app.runtime_write_capability", "tkos-runtime-a1")):
            conn.execute("SELECT set_config(%s,%s,true)", (name, value))
        assert conn.execute("SELECT gov_control_plane_on() AS allowed").fetchone()["allowed"] is False
        for table in tables:
            own = conn.execute(f"SELECT count(*) AS n FROM {table} WHERE scope_id=%s", (f["scope_id"],)).fetchone()["n"]
            hidden = conn.execute(f"SELECT count(*) AS n FROM {table} WHERE scope_id=%s", (foreign_fixture["scope_id"],)).fetchone()["n"]
            assert own > 0 and hidden == 0, "app self-asserted control settings widened row visibility"
    sql_source = (source / "memory_service_app/migrations/0018_method_protocol.sql").read_text()
    names = set(re.findall(r"CREATE\s+(?:OR\s+REPLACE\s+)?FUNCTION\s+(\w+)\s*\(", sql_source, flags=re.I))
    functions = [row for row in catalog["functions"] if row["signature"].split("(", 1)[0].removeprefix("public.") in names]
    assert len(functions) == len(names) and names, "new function catalog is incomplete"
    if any(row["security_definer"] for row in functions):
        raise NotReady("actual SECURITY DEFINER control functions require reviewed exact app-call probes")
    result = {"passed": True, "cross_scope_foreign_rows": counts,
              "app_control_guc_is_not_owner_authority": True, "new_functions": functions,
              "new_security_definer_entrypoints": 0, "function_calls_needed": False,
              "contract_a1_accepted": False}
    public_json(h.output / "scope-and-function-catalog.json", result)
    return result
