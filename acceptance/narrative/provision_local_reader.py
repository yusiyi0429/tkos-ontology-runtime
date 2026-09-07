"""Provision one expiring, read-only AGENT in an empty local convergence restore.

This is an acceptance utility, not a production identity or migration tool.
The only credential output is the original private state file. Re-running after
any identity already exists is refused; no authority is repaired or expanded.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import stat
import tempfile
import uuid

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict
from psycopg.types.json import Jsonb

ROOT = Path(__file__).resolve().parents[2]
IDENTITY_TABLES = frozenset({"gov_scopes", "gov_domains", "gov_principals",
    "gov_role_assignments", "gov_credentials", "gov_activation_policies"})
POLICY = {"version": "local-narrative-reader/1", "action_roles": {
    "read": ["AGENT"], "read_legacy_context": ["AGENT"]}}


class Refused(RuntimeError):
    """Fixed reason codes only: exceptions never expose connection details."""


def require(condition, code):
    if not condition:
        raise Refused(code)


def private_json(path):
    info = path.lstat()
    require(stat.S_ISREG(info.st_mode) and stat.S_IMODE(info.st_mode) == 0o600
            and info.st_uid == os.getuid() and info.st_nlink == 1, "PRIVATE_FILE_REQUIRED")
    value = json.loads(path.read_text(encoding="utf-8"))
    require(isinstance(value, dict), "STATE_OR_REPORT_INVALID")
    return value


def local_dsn(value):
    try:
        parts = conninfo_to_dict(value)
    except Exception:
        raise Refused("DSN_INVALID") from None
    require(set(parts) <= {"host", "hostaddr", "port", "dbname", "user", "password",
            "sslmode", "connect_timeout", "application_name"}, "DSN_OPTIONS_NOT_ALLOWED")
    require(parts.get("host") in {"localhost", "127.0.0.1", "::1"}, "LOOPBACK_REQUIRED")
    require(not parts.get("hostaddr") or parts["hostaddr"] in {"127.0.0.1", "::1"}, "LOOPBACK_REQUIRED")
    require(re.fullmatch(r"tkos_convergence_[a-z0-9_]+", parts.get("dbname", "")), "CONVERGENCE_DATABASE_REQUIRED")
    require(parts.get("user") and parts.get("password"), "EXPLICIT_DATABASE_IDENTITY_REQUIRED")
    require(parts.get("port", "").isdigit() and 1 <= int(parts["port"]) <= 65535, "EXPLICIT_PORT_REQUIRED")
    # Pin localhost without DNS; never follow libpq service, socket or host lists.
    parts["hostaddr"] = parts.get("hostaddr") or ("::1" if parts["host"] == "::1" else "127.0.0.1")
    return parts


def load_state(path, *, root=ROOT):
    path = Path(os.path.abspath(path))
    expected_parent = root.resolve() / ".runtime-acceptance"
    require(path.name == "state.json" and path.parent.parent == expected_parent
            and re.fullmatch(r"convergence-[a-z0-9-]+", path.parent.name)
            and path.resolve() == path, "LOCAL_STATE_PATH_REQUIRED")
    state = private_json(path)
    require(state.get("run_id") == path.parent.name, "RUN_ID_MISMATCH")
    require(state.get("output_directory") == str(path.parent)
            and state.get("private_directory") == str(path.parent), "REPORT_DIRECTORY_MISMATCH")
    require(not any(key in state for key in ("reader_token", "reader_principal_id", "reader_authority",
            "scope_id", "domain_id")), "READER_ALREADY_PRESENT")
    require(state.get("local_only", True) is True, "LOCAL_RESTORE_REQUIRED")
    report = private_json(path.parent / "report.json")
    require(report.get("run_id") == state["run_id"] and report.get("status") == "passed", "MIGRATION_NOT_PASSED")
    require(report.get("local_isolated_restore") is True
            and all(report.get(key) is False for key in ("production_mutations", "production_services_restarted",
                "traffic_switched", "gov_identity_or_authority_imported"))
            and report.get("governed_rows") == 0, "RESTORE_AUTHORITY_BOUNDARY_FAILED")
    require(report.get("preservation_merge_enabled") is True
            and state.get("selected_source") == "memory_plus_aw_history", "PRESERVATION_MERGE_REQUIRED")
    merge = report.get("preservation_merge", {})
    require(all(gate.get("all_53_union_preserved") is True for gate in (
        merge.get("union_after_insert", {}), merge.get("union_after_replay", {}),
        report.get("union_after_migrations", {}))), "PRESERVATION_MERGE_NOT_PASSED")
    primary = state.get("primary_scope")
    require(isinstance(primary, dict) and set(primary) == {"tenant_id", "organization_id"}
            and all(isinstance(value, str) and value == value.strip() and 1 <= len(value) <= 200
                    for value in primary.values())
            and state.get("scopes") == [primary] and state.get("legacy_scopes") == [primary]
            and state.get("scope_requires_selection") is False, "SINGLE_PRIMARY_SCOPE_REQUIRED")
    dsns = {key: local_dsn(state.get(key)) for key in (
        "selected_admin_url", "MIGRATION_DATABASE_URL", "APP_DATABASE_URL")}
    require(len({(dsn["hostaddr"], int(dsn["port"]), dsn["dbname"]) for dsn in dsns.values()}) == 1,
            "DATABASE_TARGET_MISMATCH")
    return path, state, dsns


def validate_counts(counts, *, provisioned=False):
    require(IDENTITY_TABLES <= counts.keys() and "gov_objects" in counts, "GOVERNED_SCHEMA_MISSING")
    require(all(count == (1 if provisioned and table in IDENTITY_TABLES else 0)
                for table, count in counts.items()), "GOVERNED_ROWS_ALREADY_PRESENT" if not provisioned
                else "UNEXPECTED_PROVISIONING_ROWS")


def atomic_save(path, value):
    # Only the original state is a credential destination. Atomic staging is
    # private, in the same local directory, and removed on failure.
    fd, staging = tempfile.mkstemp(prefix=".reader-state-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            os.fchmod(stream.fileno(), 0o600)
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write("\n"); stream.flush(); os.fsync(stream.fileno())
        os.replace(staging, path)
    finally:
        Path(staging).unlink(missing_ok=True)


@contextmanager
def state_lock(path):
    # Lock the directory, whose inode survives atomic replacement of state.json.
    descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally:
        os.close(descriptor)


def provision(path, *, clark_root=None):
    path, _, _ = load_state(path)
    with state_lock(path):
        path, state, dsns = load_state(path)
        clark_root = Path(clark_root or ROOT.parent / "clark").resolve()
        require((clark_root / "src/lib/ontology/narrative.ts").is_file(), "CLARK_CLIENT_NOT_FOUND")
        scope, domain, principal = (str(uuid.uuid4()) for _ in range(3))
        token = secrets.token_urlsafe(48)
        credential_digest = hashlib.sha256(token.encode()).hexdigest()
        primary = state["primary_scope"]
        with psycopg.connect(**{**dsns["selected_admin_url"], "connect_timeout": 5}) as conn:
            conn.execute("SET LOCAL lock_timeout='5s'")
            conn.execute("SET LOCAL statement_timeout='15s'")
            conn.execute("SET LOCAL row_security=off")
            require(conn.execute("SELECT current_database()").fetchone()[0] == dsns["APP_DATABASE_URL"]["dbname"],
                    "DATABASE_TARGET_MISMATCH")
            # RLS must never hide existing authority during the empty-store gate.
            require(conn.execute("SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname=current_user").fetchone()[0],
                    "UNFILTERED_LOCAL_ADMIN_REQUIRED")
            tables = [row[0] for row in conn.execute("SELECT tablename FROM pg_tables WHERE schemaname='public' AND starts_with(tablename,'gov_') ORDER BY tablename")]
            require(tables, "GOVERNED_SCHEMA_MISSING")
            conn.execute(sql.SQL("LOCK TABLE {} IN SHARE ROW EXCLUSIVE MODE").format(
                sql.SQL(",").join(sql.Identifier("public", table) for table in tables)))
            def counts():
                return {table: conn.execute(sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier("public", table))).fetchone()[0] for table in tables}
            validate_counts(counts())
            actual_scopes = conn.execute("SELECT DISTINCT tenant_id,organization_id FROM public.semantic_entities ORDER BY 1,2").fetchall()
            require(actual_scopes == [(primary["tenant_id"], primary["organization_id"])], "LIVE_PRIMARY_SCOPE_MISMATCH")
            conn.execute("INSERT INTO public.gov_scopes(scope_id,tenant_id,company_id) VALUES(%s,%s,%s)",
                (scope, primary["tenant_id"], primary["organization_id"]))
            conn.execute("INSERT INTO public.gov_domains(domain_id,scope_id,name) VALUES(%s,%s,%s)",
                (domain, scope, "Local restored legacy narrative acceptance"))
            conn.execute("INSERT INTO public.gov_principals(principal_id,scope_id,principal_type,display_name) VALUES(%s,%s,'agent',%s)",
                (principal, scope, "Local read-only acceptance agent"))
            expires = conn.execute("INSERT INTO public.gov_role_assignments(scope_id,principal_id,domain_id,role,valid_from,valid_to) VALUES(%s,%s,%s,'AGENT',now(),now()+interval '24 hours') RETURNING valid_to",
                (scope, principal, domain)).fetchone()[0]
            conn.execute("INSERT INTO public.gov_credentials(scope_id,principal_id,credential_digest,label) VALUES(%s,%s,%s,%s)",
                (scope, principal, credential_digest, "Local narrative acceptance only; assignment expires in 24 hours"))
            conn.execute("INSERT INTO public.gov_activation_policies(scope_id,domain_id,policy_id,policy_revision_id,policy_seq,content,recorded_by) VALUES(%s,%s,%s,%s,1,%s,%s)",
                (scope, domain, str(uuid.uuid4()), str(uuid.uuid4()), Jsonb(POLICY), principal))
            validate_counts(counts(), provisioned=True)
        state.update(local_only=True, tenant_id=primary["tenant_id"], company_id=primary["organization_id"],
            scope_id=scope, domain_id=domain, reader_token=token, reader_principal_id=principal,
            clark_root=str(clark_root), reader_expires_at=expires.isoformat(),
            reader_authority="Local restored database only: AGENT read/read_legacy_context; 24-hour assignment; no business authority")
        try:
            atomic_save(path, state)
        except Exception:
            # The database can now contain an unreachable reader, but a retry
            # refuses all existing rows. Never overwrite or grant a replacement.
            raise Refused("PRIVATE_STATE_WRITE_FAILED_NEW_RESTORE_REQUIRED") from None
    return {"status": "passed", "local_only": True, "identity_rows_created": 6,
        "business_rows_created": 0, "assignment_hours": 24, "actions": sorted(POLICY["action_roles"])}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("state", type=Path)
    parser.add_argument("--clark-root", type=Path)
    args = parser.parse_args()
    try:
        result = provision(args.state, clark_root=args.clark_root)
    except Refused as exc:
        result = {"status": "refused", "reason": str(exc)}
    except Exception:
        result = {"status": "refused", "reason": "LOCAL_READER_PROVISION_FAILED"}
    print(json.dumps(result, sort_keys=True))
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
