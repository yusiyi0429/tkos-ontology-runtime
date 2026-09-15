"""Controlled protocol registration CLI (A1 control plane).

Runs only against an explicit owner DSN from the MIGRATION_DATABASE_URL
environment variable.  Every command executes in one transaction that first
asserts gov_control_plane_on() — the transaction GUC plus the session role
really being the database owner (migration 0018) — so the ordinary
application role cannot use this surface even if it learns the GUC names.
Any failure rolls back with no database modification.

Output is structured JSON on stdout; connection strings and credentials are
never printed.  All changes are recorded in gov_protocol_control_events.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
from typing import Any
from uuid import uuid4

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from . import artifacts, canon, profile, protocol
from .db import jsonable, set_write_capability


class ControlError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _fail(code: str, message: str) -> None:
    raise ControlError(code, message)


def _connect() -> psycopg.Connection:
    url = os.environ.get("MIGRATION_DATABASE_URL", "").strip()
    if not url:
        _fail("CONTROL_PLANE_NOT_CONFIGURED",
              "MIGRATION_DATABASE_URL (database-owner DSN) is required.")
    conn = psycopg.connect(url, connect_timeout=10)
    conn.row_factory = dict_row
    return conn


def _begin(conn: psycopg.Connection, scope_id: str | None = None) -> None:
    """Open the control-plane session guards; must precede any protected SQL."""
    conn.execute("SELECT set_config('app.gov_control_plane', 'on', true)")
    set_write_capability(conn)
    if scope_id is not None:
        conn.execute("SELECT set_config('app.governed_scope_id', %s, true)", (scope_id,))
    row = conn.execute("SELECT gov_control_plane_on() AS ok").fetchone()
    if not row["ok"]:
        _fail("CONTROL_PLANE_NOT_AUTHORIZED",
              "The control plane requires the database owner role; the GUC alone is not sufficient.")


def _require_scope(conn: psycopg.Connection, scope_id: str) -> None:
    row = conn.execute("SELECT scope_id FROM gov_scopes WHERE scope_id=%s", (scope_id,)).fetchone()
    if row is None:
        _fail("SCOPE_NOT_FOUND", "The named scope does not exist.")


def _audit(conn: psycopg.Connection, scope_id: str, event_type: str, detail: dict[str, Any],
           actor: str) -> None:
    conn.execute(
        "INSERT INTO gov_protocol_control_events (scope_id, event_type, detail, actor)"
        " VALUES (%s,%s,%s,%s)",
        (scope_id, event_type, Jsonb(detail), actor),
    )


def _load_strict_json(path: str) -> Any:
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        _fail("INVALID_REQUEST", f"cannot read {path}: {exc.__class__.__name__}")
    try:
        return canon.load_json_strict(text)
    except ValueError as exc:
        _fail("INVALID_REQUEST", f"{path}: {exc}")


# ------------------------------------------------------------------ commands


def install_profile(conn: psycopg.Connection, args: argparse.Namespace) -> dict[str, Any]:
    if args.profile_json:
        data = _load_strict_json(args.profile_json)
    else:
        # Default: the frozen profile-core fixture bundled with the package
        # (hash-pinned, offline; no external paths are consulted).
        data = canon.load_json_strict(artifacts.profile_core_bytes().decode("utf-8"))
    try:
        core = profile.validate_profile_core(data)
    except Exception as exc:
        _fail("PROFILE_CONTENT_CONFLICT", f"profile rejected by strict validation: {exc}")
    if args.contract_file:
        contract_bytes = Path(args.contract_file).read_bytes()
    else:
        contract_bytes = artifacts.contract_a_bytes()
    contract_sha = hashlib.sha256(contract_bytes).hexdigest()
    if contract_sha != core.action_contract_ref.content_sha256:
        _fail("PROFILE_CONTENT_CONFLICT",
              "action_contract_ref.content_sha256 does not match the supplied contract file bytes.")
    from . import method_profile, method_v02_profile, method_v03_profile
    pinned_sha = (method_v03_profile.CONTRACT_SHA256 if core.profile_core_schema_version == method_v03_profile.SCHEMA_VERSION
                  else method_v02_profile.CONTRACT_SHA256 if core.profile_core_schema_version == method_v02_profile.SCHEMA_VERSION
                  else method_profile.CONTRACT_SHA256 if core.profile_core_schema_version == method_profile.SCHEMA_VERSION
                  else profile.CONTRACT_A_MAIN_CONTRACT_SHA256)
    if core.action_contract_ref.content_sha256 != pinned_sha:
        _fail("PROFILE_CONTENT_CONFLICT",
              "The compiled tkos.contract-a/0.1 support is bound to the pinned main-contract "
              "SHA256; changed contract bytes require a new contract revision.")
    scope_id = args.scope_id
    _begin(conn)  # cross-scope conflict check needs control-plane visibility
    # Serialize concurrent installs of the same profile identity across scopes:
    # two owner transactions must not both read "absent" and commit different
    # content for one (profile_id, revision).
    conn.execute("SELECT pg_advisory_xact_lock(hashtext(%s)::bigint)",
                 (f"{core.profile_id}@{core.revision}",))
    existing = conn.execute(
        "SELECT scope_id, canonical_hash FROM gov_method_profile_revisions"
        " WHERE profile_id=%s AND revision=%s",
        (core.profile_id, core.revision),
    ).fetchall()
    for row in existing:
        if row["canonical_hash"] != core.canonical_hash:
            _fail("PROFILE_CONTENT_CONFLICT",
                  f"(profile_id, revision) already installed in scope {row['scope_id']} "
                  "with different content; a revision is immutable once installed.")
    conn.execute("SELECT set_config('app.governed_scope_id', %s, true)", (scope_id,))
    _require_scope(conn, scope_id)
    if any(str(row["scope_id"]) == scope_id for row in existing):
        _audit(conn, scope_id, "install_profile_noop", {
            "profile_id": core.profile_id, "revision": core.revision,
            "canonical_hash": core.canonical_hash, "reason": "identical content already installed",
        }, args.actor)
        return {"installed": False, "already_installed": True, "profile_id": core.profile_id,
                "revision": core.revision, "canonical_hash": core.canonical_hash}
    conn.execute(
        """INSERT INTO gov_method_profile_revisions
           (scope_id, profile_id, revision, schema_version, canonical_hash,
            action_contract_ref, record_origin, experimental, content,
            installed_by, install_reason)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
        (scope_id, core.profile_id, core.revision, core.profile_core_schema_version,
         core.canonical_hash, Jsonb(core.action_contract_ref.model_dump(mode="json")),
         core.record_origin, core.experimental,
         Jsonb(core.model_dump(mode="json")), args.actor, args.reason),
    )
    _audit(conn, scope_id, "install_profile", {
        "profile_id": core.profile_id, "revision": core.revision,
        "canonical_hash": core.canonical_hash,
        "action_contract_sha256": contract_sha, "reason": args.reason,
    }, args.actor)
    return {"installed": True, "profile_id": core.profile_id, "revision": core.revision,
            "canonical_hash": core.canonical_hash}


def install_policy(conn: psycopg.Connection, args: argparse.Namespace) -> dict[str, Any]:
    try:
        content = protocol.PolicyContent.model_validate(_load_strict_json(args.content_json))
    except Exception as exc:
        _fail("INVALID_REQUEST", f"policy content rejected: {exc}")
    scope_id = args.scope_id
    _begin(conn, scope_id)
    _require_scope(conn, scope_id)
    if args.domain_id is not None:
        row = conn.execute(
            "SELECT domain_id FROM gov_domains WHERE scope_id=%s AND domain_id=%s",
            (scope_id, args.domain_id),
        ).fetchone()
        if row is None:
            _fail("DOMAIN_NOT_FOUND", "The named domain does not exist in this scope.")
    installed = protocol.installed_profile(conn, scope_id, content.default_profile_ref.profile_id,
                                           content.default_profile_ref.revision)
    if installed is None:
        _fail("METHOD_PROFILE_UNSUPPORTED",
              "default_profile_ref must name a profile revision already installed in this scope.")
    implied = profile.implied_protocol(installed["schema_version"], installed["content"])
    if implied != (content.default_protocol, content.default_contract_version):
        _fail("PROFILE_CONTENT_CONFLICT",
              "default_profile_ref does not semantically belong to the policy's protocol/version.")
    if (content.default_protocol, content.default_contract_version) not in protocol.SUPPORTED_PROTOCOL_CONTRACTS:
        _fail("PROTOCOL_NOT_SUPPORTED",
              "The policy names a protocol/contract version this runtime does not implement.")
    if args.domain_id is None:
        row = conn.execute(
            "SELECT COALESCE(MAX(policy_seq), 0) + 1 AS seq FROM gov_protocol_policies"
            " WHERE scope_id=%s AND domain_id IS NULL", (scope_id,),
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT COALESCE(MAX(policy_seq), 0) + 1 AS seq FROM gov_protocol_policies"
            " WHERE scope_id=%s AND domain_id=%s", (scope_id, args.domain_id),
        ).fetchone()
    seq = row["seq"]
    conn.execute(
        "INSERT INTO gov_protocol_policies (scope_id, domain_id, policy_seq, content, recorded_by, reason)"
        " VALUES (%s,%s,%s,%s,%s,%s)",
        (scope_id, args.domain_id, seq, Jsonb(content.model_dump(mode="json")), args.actor, args.reason),
    )
    _audit(conn, scope_id, "install_policy", {
        "domain_id": args.domain_id, "policy_seq": seq,
        "default_protocol": content.default_protocol,
        "default_contract_version": content.default_contract_version,
        "reason": args.reason,
    }, args.actor)
    return {"installed": True, "scope_id": scope_id, "domain_id": args.domain_id, "policy_seq": seq}


def set_registry(conn: psycopg.Connection, args: argparse.Namespace) -> dict[str, Any]:
    try:
        content = protocol.RegistryContent.model_validate(_load_strict_json(args.content_json))
    except Exception as exc:
        _fail("INVALID_REQUEST", f"registry content rejected: {exc}")
    scope_id = args.scope_id
    _begin(conn, scope_id)
    _require_scope(conn, scope_id)
    row = conn.execute(
        "SELECT COALESCE(MAX(registry_seq), 0) + 1 AS seq FROM gov_protocol_support_registry"
        " WHERE scope_id=%s AND protocol_id=%s", (scope_id, args.protocol_id),
    ).fetchone()
    seq = row["seq"]
    conn.execute(
        """INSERT INTO gov_protocol_support_registry
           (scope_id, protocol_id, contract_version, registry_seq, content, recorded_by)
           VALUES (%s,%s,%s,%s,%s,%s)""",
        (scope_id, args.protocol_id, args.contract_version, seq,
         Jsonb(content.model_dump(mode="json")), args.actor),
    )
    compiled = (args.protocol_id, args.contract_version) in protocol.SUPPORTED_PROTOCOL_CONTRACTS
    _audit(conn, scope_id, "set_registry", {
        "protocol_id": args.protocol_id, "contract_version": args.contract_version,
        "registry_seq": seq, "compiled_support": compiled, "reason": args.reason,
    }, args.actor)
    return {"installed": True, "scope_id": scope_id, "protocol_id": args.protocol_id,
            "contract_version": args.contract_version, "registry_seq": seq,
            "compiled_support": compiled,
            "note": None if compiled else
                    "Registered, but this runtime build does not implement the pair; all gates reject it."}


def freeze_writes(conn: psycopg.Connection, args: argparse.Namespace) -> dict[str, Any]:
    """Kill switch: append a registry entry disabling create/write/evidence upload.

    Reads and historical receipt replay are unaffected.
    """
    scope_id = args.scope_id
    _begin(conn, scope_id)
    _require_scope(conn, scope_id)
    current = protocol.current_registry(conn, scope_id, args.protocol_id)
    if current is None:
        _fail("PROTOCOL_NOT_SUPPORTED", "No registry entry exists for this protocol in the scope.")
    # A protocol-wide kill switch must freeze every installed Method version.
    # Append revocations; never let the per-version reader revive an older row.
    if args.protocol_id == "tkos.method":
        versions = conn.execute("""SELECT DISTINCT ON (contract_version) * FROM gov_protocol_support_registry
            WHERE scope_id=%s AND protocol_id=%s ORDER BY contract_version,registry_seq DESC""",
            (scope_id, args.protocol_id)).fetchall()
        seq = int(current["registry_seq"])
        for row in versions:
            seq += 1
            content = {**row["content"], "can_write": False, "can_create": False, "evidence_upload": False,
                       "notes": f"Writes frozen by control plane: {args.reason}"}
            validated = protocol.RegistryContent.model_validate(content)
            conn.execute("""INSERT INTO gov_protocol_support_registry
                (scope_id,protocol_id,contract_version,registry_seq,content,recorded_by) VALUES(%s,%s,%s,%s,%s,%s)""",
                (scope_id,args.protocol_id,row["contract_version"],seq,Jsonb(validated.model_dump(mode="json")),args.actor))
            _audit(conn, scope_id, "freeze_writes", {"protocol_id": args.protocol_id,
                "contract_version": row["contract_version"], "registry_seq": seq, "reason": args.reason}, args.actor)
        return {"frozen": True, "scope_id": scope_id, "protocol_id": args.protocol_id,
                "contract_version": current["contract_version"], "registry_seq": seq,
                "versions": [r["contract_version"] for r in versions]}
    content = dict(current["content"])
    content.update({"can_write": False, "can_create": False, "evidence_upload": False,
                    "notes": f"Writes frozen by control plane: {args.reason}"})
    try:
        validated = protocol.RegistryContent.model_validate(content)
    except Exception as exc:
        _fail("INVALID_REQUEST", f"frozen registry content rejected: {exc}")
    seq = int(current["registry_seq"]) + 1
    conn.execute(
        """INSERT INTO gov_protocol_support_registry
           (scope_id, protocol_id, contract_version, registry_seq, content, recorded_by)
           VALUES (%s,%s,%s,%s,%s,%s)""",
        (scope_id, args.protocol_id, current["contract_version"], seq,
         Jsonb(validated.model_dump(mode="json")), args.actor),
    )
    _audit(conn, scope_id, "freeze_writes", {
        "protocol_id": args.protocol_id, "contract_version": current["contract_version"],
        "registry_seq": seq, "reason": args.reason,
    }, args.actor)
    return {"frozen": True, "scope_id": scope_id, "protocol_id": args.protocol_id,
            "contract_version": current["contract_version"], "registry_seq": seq,
            "note": "Reads and receipt replay remain available; new writes and uploads are rejected."}


def register_sentinel(conn: psycopg.Connection, args: argparse.Namespace) -> dict[str, Any]:
    """Create a ProtocolSentinel object bound to an installed profile.

    A sentinel carries no business semantics; it exists so Contract-A bindings
    and read support can be exercised without fabricating business success.
    """
    scope_id = args.scope_id
    _begin(conn, scope_id)
    _require_scope(conn, scope_id)
    row = conn.execute(
        "SELECT domain_id FROM gov_domains WHERE scope_id=%s AND domain_id=%s",
        (scope_id, args.domain_id),
    ).fetchone()
    if row is None:
        _fail("DOMAIN_NOT_FOUND", "The named domain does not exist in this scope.")
    installed = protocol.installed_profile(conn, scope_id, args.profile_id, args.profile_revision)
    if installed is None:
        _fail("METHOD_PROFILE_UNSUPPORTED", "The named profile revision is not installed in this scope.")
    if (args.protocol_id, args.contract_version) not in protocol.SUPPORTED_PROTOCOL_CONTRACTS:
        _fail("PROTOCOL_NOT_SUPPORTED", "The protocol/contract version pair is not implemented.")
    implied = profile.implied_protocol(installed["schema_version"], installed["content"])
    if implied != (args.protocol_id, args.contract_version):
        _fail("PROFILE_CONTENT_CONFLICT",
              "The named profile does not semantically belong to the requested protocol/version.")
    object_id = str(uuid4())
    conn.execute(
        "INSERT INTO gov_objects(object_id,scope_id,domain_id,object_type,lifecycle_status)"
        " VALUES (%s,%s,%s,'ProtocolSentinel','recorded')",
        (object_id, scope_id, args.domain_id),
    )
    binding_id = protocol.insert_binding(
        conn, scope_id, object_id,
        {"protocol_id": args.protocol_id, "contract_version": args.contract_version,
         "profile_id": installed["profile_id"], "profile_revision": installed["revision"],
         "profile_canonical_hash": installed["canonical_hash"], "record_origin": "synthetic"},
        registered_by=args.actor,
        detail={"sentinel": True, "reason": args.reason},
    )
    _audit(conn, scope_id, "register_sentinel", {
        "object_id": object_id, "protocol_id": args.protocol_id,
        "contract_version": args.contract_version, "profile_id": args.profile_id,
        "profile_revision": args.profile_revision, "reason": args.reason,
    }, args.actor)
    return {"registered": True, "object_id": object_id, "binding_id": binding_id,
            "protocol_id": args.protocol_id, "contract_version": args.contract_version}


def backfill_legacy(conn: psycopg.Connection, args: argparse.Namespace) -> dict[str, Any]:
    """Explicit legacy backfill for one scope (e.g. after a restore).

    Only objects without any binding are bound; already-bound objects are
    never rebound (the 0018 insert gate also rejects it).  Idempotent.
    """
    scope_id = args.scope_id
    _begin(conn, scope_id)
    _require_scope(conn, scope_id)
    conn.execute(
        """INSERT INTO gov_method_profile_revisions
           (scope_id, profile_id, revision, schema_version, canonical_hash,
            action_contract_ref, record_origin, experimental, content,
            installed_by, install_reason)
           VALUES (%s,%s,%s,%s,%s,NULL,'legacy',false,%s,%s,%s)
           ON CONFLICT (scope_id, profile_id, revision) DO NOTHING""",
        (scope_id, profile.LEGACY_PROFILE_ID, profile.LEGACY_PROFILE_REVISION,
         "tkos.legacy-interpretation-record/0.2", profile.LEGACY_PROFILE_CANONICAL_HASH,
         Jsonb(profile.LEGACY_PROFILE_CONTENT), args.actor, args.reason),
    )
    conn.execute(
        """INSERT INTO gov_protocol_policies
           (scope_id, domain_id, policy_seq, content, recorded_by, reason)
           SELECT %s, NULL, 1, %s, %s, %s
           WHERE NOT EXISTS (SELECT 1 FROM gov_protocol_policies
                             WHERE scope_id=%s AND domain_id IS NULL)""",
        (scope_id, Jsonb(profile.LEGACY_SCOPE_POLICY_CONTENT), args.actor, args.reason, scope_id),
    )
    for protocol_id, contract_version, content in (
            (profile.LEGACY_PROTOCOL_ID, profile.LEGACY_CONTRACT_VERSION,
             profile.LEGACY_REGISTRY_CONTENT),
            (profile.CONTRACT_A_PROTOCOL_ID, profile.CONTRACT_A_CONTRACT_VERSION,
             profile.CONTRACT_A_REGISTRY_CONTENT)):
        conn.execute(
            """INSERT INTO gov_protocol_support_registry
               (scope_id, protocol_id, contract_version, registry_seq, content, recorded_by)
               VALUES (%s,%s,%s,1,%s,%s)
               ON CONFLICT (scope_id, protocol_id, registry_seq) DO NOTHING""",
            (scope_id, protocol_id, contract_version, Jsonb(content), args.actor),
        )
    unbound = conn.execute(
        """SELECT o.object_id FROM gov_objects o
           WHERE o.scope_id=%s AND NOT EXISTS (
               SELECT 1 FROM gov_object_protocol_bindings b
               WHERE b.scope_id=o.scope_id AND b.object_id=o.object_id)
           ORDER BY o.object_id""",
        (scope_id,),
    ).fetchall()
    for row in unbound:
        protocol.insert_binding(
            conn, scope_id, str(row["object_id"]),
            {"protocol_id": profile.LEGACY_PROTOCOL_ID,
             "contract_version": profile.LEGACY_CONTRACT_VERSION,
             "profile_id": profile.LEGACY_PROFILE_ID,
             "profile_revision": profile.LEGACY_PROFILE_REVISION,
             "profile_canonical_hash": profile.LEGACY_PROFILE_CANONICAL_HASH,
             "record_origin": "legacy"},
            registered_by=args.actor,
            detail={"registration": "legacy_backfill", "reason": args.reason},
        )
    _audit(conn, scope_id, "backfill_legacy", {
        "kind": "legacy_registration", "source": "control-cli",
        "object_bindings_inserted": len(unbound),
        "unbound_object_ids": [str(row["object_id"]) for row in unbound],
        "reason": args.reason,
    }, args.actor)
    return {"backfilled": len(unbound), "scope_id": scope_id}


def status(conn: psycopg.Connection, args: argparse.Namespace) -> dict[str, Any]:
    scope_id = args.scope_id
    _begin(conn, scope_id)
    _require_scope(conn, scope_id)
    profiles = conn.execute(
        """SELECT profile_id, revision, canonical_hash, record_origin, experimental, recorded_at
           FROM gov_method_profile_revisions WHERE scope_id=%s
           ORDER BY profile_id, revision""", (scope_id,),
    ).fetchall()
    policies = conn.execute(
        """SELECT domain_id, policy_seq, content->>'default_protocol' AS default_protocol,
                  content->>'default_contract_version' AS default_contract_version, recorded_at
           FROM gov_protocol_policies WHERE scope_id=%s
           ORDER BY domain_id NULLS FIRST, policy_seq""", (scope_id,),
    ).fetchall()
    registries = conn.execute(
        """SELECT protocol_id, contract_version, registry_seq, content, recorded_at
           FROM gov_protocol_support_registry WHERE scope_id=%s
           ORDER BY protocol_id, registry_seq""", (scope_id,),
    ).fetchall()
    bindings = conn.execute(
        """SELECT protocol_id, contract_version, count(*) AS objects
           FROM gov_object_protocol_bindings WHERE scope_id=%s
           GROUP BY protocol_id, contract_version ORDER BY protocol_id""", (scope_id,),
    ).fetchall()
    unbound = conn.execute(
        """SELECT count(*) AS n FROM gov_objects o WHERE o.scope_id=%s AND NOT EXISTS (
               SELECT 1 FROM gov_object_protocol_bindings b
               WHERE b.scope_id=o.scope_id AND b.object_id=o.object_id)""", (scope_id,),
    ).fetchone()
    events = conn.execute(
        """SELECT event_type, actor, recorded_at FROM gov_protocol_control_events
           WHERE scope_id=%s ORDER BY recorded_at DESC, event_id DESC LIMIT 20""", (scope_id,),
    ).fetchall()
    return jsonable({
        "scope_id": scope_id, "profiles": profiles, "policies": policies,
        "registries": registries, "bindings": bindings,
        "unbound_object_count": unbound["n"], "recent_control_events": events,
    })


# ---------------------------------------------------------------------- main


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    def base(name: str, **kwargs) -> argparse.ArgumentParser:
        p = sub.add_parser(name, **kwargs)
        p.add_argument("--actor", default="control-cli")
        return p

    p = base("install-profile")
    p.add_argument("--scope-id", required=True)
    p.add_argument("--profile-json", default=None,
                   help="Profile JSON path; defaults to the bundled frozen profile-core fixture.")
    p.add_argument("--contract-file", default=None,
                   help="Path to the exact main-contract bytes named by action_contract_ref; "
                        "defaults to the bundled pinned contract artifact.")
    p.add_argument("--reason", required=True)

    p = base("install-policy")
    p.add_argument("--scope-id", required=True)
    p.add_argument("--domain-id", default=None)
    p.add_argument("--content-json", required=True)
    p.add_argument("--reason", required=True)

    p = base("set-registry")
    p.add_argument("--scope-id", required=True)
    p.add_argument("--protocol-id", required=True)
    p.add_argument("--contract-version", required=True)
    p.add_argument("--content-json", required=True)
    p.add_argument("--reason", required=True)

    p = base("freeze-writes")
    p.add_argument("--scope-id", required=True)
    p.add_argument("--protocol-id", required=True)
    p.add_argument("--reason", required=True)

    p = base("register-sentinel")
    p.add_argument("--scope-id", required=True)
    p.add_argument("--domain-id", required=True)
    p.add_argument("--protocol-id", required=True)
    p.add_argument("--contract-version", required=True)
    p.add_argument("--profile-id", required=True)
    p.add_argument("--profile-revision", required=True)
    p.add_argument("--reason", required=True)

    p = base("backfill-legacy")
    p.add_argument("--scope-id", required=True)
    p.add_argument("--reason", required=True)

    p = base("status")
    p.add_argument("--scope-id", required=True)

    args = parser.parse_args()
    handler = {
        "install-profile": install_profile,
        "install-policy": install_policy,
        "set-registry": set_registry,
        "freeze-writes": freeze_writes,
        "register-sentinel": register_sentinel,
        "backfill-legacy": backfill_legacy,
        "status": status,
    }[args.command]
    try:
        with _connect() as conn:
            report = handler(conn, args)
        report = {"ok": True, "command": args.command, **jsonable(report)}
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    except ControlError as exc:
        print(json.dumps({"ok": False, "command": args.command,
                          "error": {"code": exc.code, "message": exc.message}},
                         ensure_ascii=False), file=sys.stderr)
        raise SystemExit(2) from exc
    except (psycopg.Error, OSError) as exc:
        # Never echo connection details; only the exception class and SQLSTATE.
        sqlstate = getattr(exc, "sqlstate", None)
        print(json.dumps({"ok": False, "command": args.command,
                          "error": {"code": "CONTROL_PLANE_UNAVAILABLE",
                                    "message": f"{exc.__class__.__name__} sqlstate={sqlstate}"}},
                         ensure_ascii=False), file=sys.stderr)
        raise SystemExit(2) from exc


if __name__ == "__main__":
    main()
