"""Real HTTP regression for an old, objectless receipt after domain revocation.

Only identities, assignments, policy and the starting Outcome are provisioned
directly. All tested changes use the running API. Tokens stay in memory and are
never included in the returned report or HTTP transcript.
"""
from __future__ import annotations

import hashlib
import secrets
from urllib.parse import urlsplit
import uuid

import psycopg
from psycopg.rows import dict_row

from acceptance.runtime.client import Client, assert_error
from acceptance.runtime.sql_oracle import snapshot_scope


def run_receipt_domain_check(harness, base_url: str) -> dict:
    """Prove that retaining domain B cannot authorize a domain A receipt.

    ``harness`` supplies the isolated database connections and sanitized HTTP
    logger. ``base_url`` is the already-running local API (Scenario.api_url).
    This helper does not start/stop processes or change the main fixture.
    """
    parsed = urlsplit(base_url)
    if (parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
            or parsed.username or parsed.password or parsed.query or parsed.fragment):
        raise AssertionError("Receipt-domain check requires the isolated local HTTP API")

    from memory_service_runtime.governed.bootstrap import seed_scope

    namespace = "runtime-acceptance-receipt-domain-" + uuid.uuid4().hex[:12]
    second_ceo_token = secrets.token_urlsafe(48)
    second_ceo_id, second_ceo_assignment_id, domain_b_assignment_id = (
        str(uuid.uuid4()) for _ in range(3)
    )
    try:
        with psycopg.connect(harness.env["MIGRATION_DATABASE_URL"], row_factory=dict_row) as conn:
            fixture = seed_scope(conn, namespace, namespace + "-company")
            scope_id = fixture["scope_id"]
            domain_a = fixture["domain_id"]
            first_ceo = fixture["actors"]["ceo"]
            with conn.transaction():
                conn.execute("SELECT set_config('app.governed_scope_id',%s,true)", (scope_id,))
                conn.execute("SELECT set_config('app.runtime_write_capability','tkos-runtime-a1',true)")
                row = conn.execute(
                    "SELECT domain_id FROM gov_role_assignments WHERE scope_id=%s AND assignment_id=%s",
                    (scope_id, fixture["actors"]["outsider"]["assignment_id"]),
                ).fetchone()
                assert row is not None, "The fixture must contain a second domain"
                domain_b = str(row["domain_id"])
                assert domain_b != domain_a
                # One extra B-domain assignment for the original CEO.
                conn.execute(
                    """INSERT INTO gov_role_assignments
                       (assignment_id,scope_id,principal_id,domain_id,role)
                       VALUES(%s,%s,%s,%s,'IC')""",
                    (domain_b_assignment_id, scope_id, first_ceo["principal_id"], domain_b),
                )
                # A second independent human CEO may revoke the first CEO in A.
                conn.execute(
                    """INSERT INTO gov_principals
                       (principal_id,scope_id,principal_type,display_name)
                       VALUES(%s,%s,'human','Synthetic second CEO for domain-receipt acceptance')""",
                    (second_ceo_id, scope_id),
                )
                conn.execute(
                    """INSERT INTO gov_role_assignments
                       (assignment_id,scope_id,principal_id,domain_id,role)
                       VALUES(%s,%s,%s,%s,'CEO')""",
                    (second_ceo_assignment_id, scope_id, second_ceo_id, domain_a),
                )
                digest = hashlib.sha256(second_ceo_token.encode("utf-8")).hexdigest()
                conn.execute("SELECT set_config('app.governed_credential_digest',%s,true)", (digest,))
                conn.execute(
                    """INSERT INTO gov_credentials(scope_id,principal_id,credential_digest,label)
                       VALUES(%s,%s,%s,'Synthetic second CEO credential')""",
                    (scope_id, second_ceo_id, digest),
                )
                conn.execute("SELECT set_config('app.governed_credential_digest','',true)")
    except psycopg.Error as exc:
        # PostgreSQL error DETAIL can contain a failed credential row. Expose
        # only SQLSTATE, never the original exception text in a public report.
        raise AssertionError(f"Receipt-domain identity provisioning failed (SQLSTATE {exc.sqlstate})") from None

    clients = []
    try:
        def client(name: str, token: str) -> Client:
            result = Client(base_url, token, "receipt-domain-" + name, harness.log)
            clients.append(result)
            return result

        ceo = client("first-ceo", first_ceo["token"])
        second_ceo = client("second-ceo", second_ceo_token)
        domain_b_dri = client("domain-b-dri", fixture["actors"]["outsider"]["token"])

        # A real B-domain object supplies a positive authentication/read control.
        b_receipt = domain_b_dri.action("create_object", {
            "object_type": "FeedbackThread", "domain_id": domain_b,
            "payload": {"title": "Synthetic domain B access control",
                        "description": "Still readable after the same human loses all domain A authority."},
        })
        b_object_id = b_receipt["result"]["object_id"]
        assert ceo.object(b_object_id)["object_id"] == b_object_id

        old_command = ceo.command("revoke_assignment", {
            "assignment_id": fixture["actors"]["ic"]["assignment_id"],
        })
        old_receipt = ceo.json("POST", "/v1/actions", old_command)
        old_receipt_id = old_receipt["receipt_id"]
        assert old_receipt["status"] == "committed"
        assert old_receipt["object_versions"] == [], "The regression requires an objectless receipt"
        assert old_receipt["result"].get("referenced_object_ids", []) == []
        assert old_receipt["result"]["domain_id"] == domain_a
        receipt_path = f"/v1/action-receipts/{old_receipt_id}"
        assert ceo.json("GET", receipt_path)["receipt"] == old_receipt

        revocation = second_ceo.action("revoke_assignment", {
            "assignment_id": first_ceo["assignment_id"],
        })
        assert revocation["status"] == "committed"
        assert revocation["auth_epoch"] > old_receipt["auth_epoch"]

        # Verify the exact persisted authority state through the application DB
        # role. No credential or principal display data is selected.
        with psycopg.connect(harness.env["APP_DATABASE_URL"], row_factory=dict_row) as conn:
            conn.execute("SET TRANSACTION READ ONLY")
            conn.execute("SELECT set_config('app.governed_scope_id',%s,true)", (scope_id,))
            conn.execute("SELECT set_config('app.runtime_write_capability','tkos-runtime-a1',true)")
            rows = conn.execute(
                """SELECT assignment_id::text, domain_id::text, role, active
                   FROM gov_role_assignments WHERE scope_id=%s AND principal_id=%s
                   ORDER BY assignment_id""",
                (scope_id, first_ceo["principal_id"]),
            ).fetchall()
            assert len(rows) == 2, "The first CEO must have exactly the two intended assignments"
            by_id = {row["assignment_id"]: row for row in rows}
            assert by_id[first_ceo["assignment_id"]]["active"] is False
            retained = by_id[domain_b_assignment_id]
            assert retained["active"] is True and retained["domain_id"] == domain_b and retained["role"] == "IC"

        # A 200 in B proves the following denials are about A's authorization,
        # not an expired/invalid credential or a globally disabled principal.
        assert ceo.object(b_object_id)["object_id"] == b_object_id
        assert_error(ceo.object(fixture["outcome"]["object_id"], expected=404), "NOT_FOUND")

        def snapshot():
            with psycopg.connect(harness.env["APP_DATABASE_URL"]) as conn:
                return snapshot_scope(conn, scope_id, fixture["tenant_id"], fixture["company_id"])

        before = snapshot()
        denied_get = ceo.json("GET", receipt_path, expected=403)
        assert_error(denied_get, "FORBIDDEN")
        denied_replay = ceo.json("POST", "/v1/actions", old_command, expected=403)
        assert_error(denied_replay, "FORBIDDEN")
        after = snapshot()
        assert after == before, "Denied receipt read/replay changed persisted governed state"
        assert after["tables"]["gov_action_receipts"]["row_count"] == 3
        return {
            "status": "passed", "scope_id": scope_id, "domain_a": domain_a, "domain_b": domain_b,
            "old_receipt_id": old_receipt_id, "revocation_receipt_id": revocation["receipt_id"],
            "retained_domain_object_id": b_object_id,
            "domain_b_read_status": 200, "domain_a_object_status": 404,
            "old_receipt_get_status": 403, "old_command_replay_status": 403,
            "original_domain_assignment_active": False, "retained_domain_assignment_active": True,
            "persisted_authority_rows": rows, "unchanged_rejection_snapshot": before,
        }
    finally:
        for item in clients:
            item.close()


__all__ = ["run_receipt_domain_check"]
