"""Bounded Co-agent consolidation for a browser-opened 0.4 review window.

Given an existing ``window_open`` browser fixture report, its private identities
file and the isolated DB env, this helper uses *only* legal ``/v1/actions``
prepare/commit calls to:

1. close the open window (Co-agent), reading the frozen effective opinions once;
2. resolve it into a CandidateSet with the **unchanged** reviewed PCO/Mission
   bodies, every frozen opinion dispositioned exactly once as ``unresolved``
   (deferred) and non-critical unresolved differences only;
3. persist a public report with candidate refs and independent read-only SQL
   checks (receipts, object versions, opinion history, no executions) and
   ``real_model: not_run``.

It never creates human commitments, never seeds business SQL, and never claims
adoption: without a body change, ``已采纳`` would be a fabricated business
statement.  The root runs it explicitly; ``--dry-run`` performs no writes.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import json
from pathlib import Path
from uuid import uuid4

import psycopg
from psycopg.rows import dict_row

from acceptance.protocol_a1_independent.support import Environment, public_json
from acceptance.runtime.client import Client, EvidenceLog

CONTRACT = "tkos.method/0.4"
DEFERRED_RATIONALE = (
    "受控 Co-agent 收拢：未修改被审正文，本人意见按未决（deferred）保留；"
    "不代表采纳，也不代表真实模型结论。"
)


def _reason_for_opinions(opinions: list[dict]) -> list[dict]:
    """Non-critical unresolved differences derived from the frozen opinions.

    The reviewed bodies are unchanged, so no opinion can honestly be marked
    adopted; they stay visible as deferred entries instead of disappearing.
    """
    differences = []
    for index, opinion in enumerate(opinions):
        excerpt = str(opinion.get("content") or "本人意见").strip()
        differences.append({
            "topic": f"意见 {index + 1}（{opinion['record_id'][:8]}…）",
            "statement": f"{excerpt[:160]} —— 未修改正文，延后到后续窗口处理。",
            "critical": False,
        })
    return differences


def _prepare(client: Client, body: dict) -> dict:
    result = client.json("POST", "/v1/actions/prepare", body)
    body = deepcopy(body)
    body["expected_versions"] = result["expected_versions"]
    if result["target"]:
        assert result["target"]["revision_id"] == body["target"]["revision_id"]
        body["target"]["expected_version"] = result["target"]["expected_version"]
    return body


def _commit(client: Client, body: dict) -> dict:
    receipt = client.json("POST", "/v1/actions", body)
    assert receipt["status"] == "committed" and receipt["effect_task_ids"] == [], receipt
    return receipt


def _act(client: Client, kind: str, params: dict, obj: dict, reason: str) -> dict:
    body = {
        "action_type": kind, "contract_version": CONTRACT, "expected_versions": [],
        "idempotency_key": f"browser-consolidate-{uuid4().hex}",
        "reason": reason, "params": params,
        "target": {"object_id": obj["object_id"], "revision_id": obj["latest_revision_id"],
                   "expected_version": obj["object_version"]},
    }
    return _commit(client, _prepare(client, body))


class Sql:
    """Read-only, scope-fenced independent assertions (app role)."""

    def __init__(self, env: Environment, scope_id: str):
        self.env, self.scope_id = env, scope_id

    def __enter__(self):
        self.conn = psycopg.connect(self.env.values["APP_DATABASE_URL"], row_factory=dict_row)
        self.conn.execute("SET TRANSACTION READ ONLY")
        self.conn.execute("SELECT set_config('app.governed_scope_id',%s,true)", (self.scope_id,))
        return self

    def __exit__(self, *_exc):
        self.conn.close()

    def rows(self, statement: str, params: tuple = ()) -> list[dict]:
        return [dict(row) for row in self.conn.execute(statement, params).fetchall()]


def _plain(value):
    """UUID/timestamps from SQL evidence become plain JSON strings."""
    from datetime import date, datetime
    from uuid import UUID
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_plain(item) for item in value]
    return value


def _http_opinions(client: Client, window_id: str) -> list[dict]:
    """Authorized Co-agent read of the window's effective opinions.

    SQL never supplies business input to the Co-agent; the review endpoint is
    the same authorized read the workbench uses.  Only ``window_comment``
    records are opinions; closed/resolution records are independent evidence.
    """
    response = client.json("GET", f"/v1/method/objects/{window_id}/reviews?effective_only=true")
    opinions = []
    for item in response.get("items", []):
        if item.get("kind") != "window_comment":
            continue
        content = item.get("content") or {}
        opinions.append({
            "record_id": str(item["record_id"]),
            "principal_id": str(item.get("principal_id")),
            "content": content.get("content", ""),
            "target_object_id": str(item.get("target_object_id")),
            "target_revision_id": str(item.get("target_revision_id")),
        })
    return opinions


def consolidate(args) -> dict:
    fixture = json.loads(Path(args.fixture).read_text())
    identities = json.loads(Path(args.identities).read_text())
    stage = args.stage
    scope = next((item for item in fixture["scopes"] if item["stage"] == stage), None)
    if scope is None:
        raise SystemExit(f"fixture has no stage {stage!r}")
    if str(identities.get("scope_id")) != str(scope["scope_id"]):
        raise SystemExit("identities file belongs to a different scope")
    co_agent = identities["actors"].get("co_agent")
    if co_agent is None or co_agent.get("principal_type") != "agent" or co_agent.get("role") != "CO_AGENT":
        raise SystemExit("identities do not contain the Co-agent")
    url = args.url or fixture["url"]
    window_ref = scope["objects"]["window"]
    output_path = Path(args.output)
    output_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    log = EvidenceLog(output_path.parent)
    client = Client(url, co_agent["token"], "co_agent", log)
    env = Environment(Path(args.env_file))
    try:
        window = client.object(window_ref["object_id"])
        if window["protocol"]["contract_version"] != CONTRACT:
            raise SystemExit("window is not a 0.4 object")
        if window["method_state"].get("phase") != "open":
            raise SystemExit(f"window is not open (phase={window['method_state'].get('phase')})")
        payload = window["latest_revision"]["payload"]
        # Business input comes from the authorized HTTP review endpoint; SQL is
        # reserved for independent assertions after the writes.
        opinions = _http_opinions(client, window_ref["object_id"])
        active = sorted({rid for ids in (window["method_state"].get("active_opinions") or {}).values() for rid in ids})
        if sorted(opinion["record_id"] for opinion in opinions) != active:
            raise SystemExit("authorized review read does not match the window's active opinions")
        if len(active) != len(set(active)):
            raise SystemExit("duplicate active opinion ids")
        plan = {
            "mode": "controlled_coagent_consolidation",
            "real_model": "not_run",
            "stage": stage, "url": url, "scope_id": scope["scope_id"],
            "window_ref": window_ref,
            "active_opinion_ids": active,
            "planned_dispositions": [{"review_record_id": rid, "decision": "unresolved",
                                      "rationale": DEFERRED_RATIONALE} for rid in active],
            "planned_unresolved_differences": _reason_for_opinions(opinions),
            "unchanged_bodies": {"pco_refs": payload.get("pco_refs", []),
                                 "mission_refs": payload.get("mission_refs", [])},
        }
        if args.dry_run:
            plan["dry_run"] = True
            public_json(output_path, plan)
            return plan

        close_receipt = _act(client, "m1b_close_window", {"reason": args.reason},
                             window, "Co-agent controlled close for consolidation")
        frozen = sorted(close_receipt["result"]["frozen_opinion_ids"])
        if frozen != active:
            raise SystemExit(f"frozen opinions {frozen} do not match the read active set {active}")
        if len(frozen) != len(set(frozen)):
            raise SystemExit("frozen opinion ids are not unique")

        pcos, missions = [], []
        for ref in payload.get("pco_refs", []):
            obj = client.object(ref["object_id"])
            pcos.append({"object_id": ref["object_id"], "payload": obj["latest_revision"]["payload"]})
        for ref in payload.get("mission_refs", []):
            obj = client.object(ref["object_id"])
            mission_payload = deepcopy(obj["latest_revision"]["payload"])
            mission_payload.pop("parent_pco_ref")
            missions.append({"object_id": ref["object_id"], **mission_payload})
        dispositions = [{"review_record_id": rid, "decision": "unresolved",
                         "rationale": DEFERRED_RATIONALE} for rid in frozen]
        resolve_params = {
            "title": f"受控 Co-agent 收拢候选（{stage}）",
            "pcos": pcos, "missions": missions,
            "dispositions": dispositions,
            "unresolved_differences": _reason_for_opinions(opinions),
            "summary": "受控收拢保留原审正文；全部意见延后（未决），未产生任何执行或验收效力。",
        }
        resolve_receipt = _act(client, "m1b_resolve_window", resolve_params,
                               client.object(window_ref["object_id"]),
                               "Co-agent controlled resolution with deferred dispositions")
        candidate_ref = {key: resolve_receipt["result"][key]
                         for key in ("object_id", "revision_id", "payload_hash")}

        with Sql(env, scope["scope_id"]) as sql:
            receipts = sql.rows(
                """SELECT receipt_id, action_type, status, result, object_versions
                     FROM gov_action_receipts
                    WHERE scope_id=%s AND action_type IN ('m1b_close_window','m1b_resolve_window')
                    ORDER BY recorded_at, receipt_id""", (scope["scope_id"],))
            window_row = sql.rows("SELECT object_id, object_version, latest_revision_id, effective_revision_id, lifecycle_status"
                                  " FROM gov_objects WHERE scope_id=%s AND object_id=%s",
                                  (scope["scope_id"], window["object_id"]))[0]
            candidate_row = sql.rows("SELECT object_id, object_version, latest_revision_id, effective_revision_id, lifecycle_status"
                                     " FROM gov_objects WHERE scope_id=%s AND object_id=%s",
                                     (scope["scope_id"], candidate_ref["object_id"]))[0]
            window_state = sql.rows("SELECT state FROM gov_method_state WHERE scope_id=%s AND object_id=%s",
                                    (scope["scope_id"], window["object_id"]))[0]["state"]
            candidate_state = sql.rows("SELECT state FROM gov_method_state WHERE scope_id=%s AND object_id=%s",
                                       (scope["scope_id"], candidate_ref["object_id"]))[0]["state"]
            reviews = sql.rows("SELECT record_id, kind FROM gov_method_reviews WHERE scope_id=%s AND window_id=%s",
                               (scope["scope_id"], window["object_id"]))
            commitments = sql.rows("SELECT count(*) AS n FROM gov_method_commitments WHERE scope_id=%s",
                                   (scope["scope_id"],))[0]["n"]
            executions = sql.rows("SELECT count(*) AS n FROM gov_execution_authorities WHERE scope_id=%s",
                                  (scope["scope_id"],))[0]["n"]
            work_receipts = sql.rows("SELECT count(*) AS n FROM gov_work_receipts WHERE scope_id=%s",
                                     (scope["scope_id"],))[0]["n"]
        opinion_history = [row for row in reviews if row["kind"] == "window_comment"]
        history_ids = [str(row["record_id"]) for row in opinion_history]
        review_kinds = {str(row["kind"]) for row in reviews}
        checks = {
            "close_and_resolve_receipts_present": {str(row["action_type"]) for row in receipts} >= {
                "m1b_close_window", "m1b_resolve_window"},
            "window_closed_resolved": (window_state or {}).get("phase") == "resolved" and "window_resolution" in review_kinds,
            "candidate_is_pending_and_not_effective": (candidate_state or {}).get("phase") == "pending"
                and candidate_row["effective_revision_id"] is None,
            "frozen_opinions_exactly_once": (len(frozen) == len(set(frozen))
                                             and all(history_ids.count(rid) == 1 for rid in frozen)),
            "dispositions_cover_frozen_opinions_once": [d["review_record_id"] for d in dispositions] == frozen,
            "no_human_commitments_created": commitments == 0,
            "no_execution_authority": executions == 0,
            "no_work_receipts": work_receipts == 0,
        }
        report_plan = {key: value for key, value in plan.items() if not key.startswith("planned_")}
        report = {
            **report_plan, "dry_run": False,
            "candidate_ref": candidate_ref,
            "close_receipt_id": close_receipt["receipt_id"],
            "resolve_receipt_id": resolve_receipt["receipt_id"],
            "frozen_opinion_ids": frozen,
            "dispositions": dispositions,
            "unresolved_differences": resolve_params["unresolved_differences"],
            "sql_checks": checks,
            "sql_evidence": _plain({
                "receipts": [{"receipt_id": row["receipt_id"], "action_type": row["action_type"],
                              "status": row["status"]} for row in receipts],
                "window": window_row, "window_state": window_state,
                "candidate": candidate_row, "candidate_state": candidate_state,
                "opinion_history_count": len(opinion_history),
            }),
            "controlled_consolidation_passed": all(checks.values()),
            "note": ("受控 Co-agent 收口：未使用真实模型，未修改正文，未创建人类承诺；"
                     "候选仍需具名 DRI/Owner 承诺与 CEO 激活。"),
        }
        public_json(output_path, report)
        return report
    finally:
        client.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument("--fixture", type=Path, required=True,
                        help="browser-fixture.json produced by browser_fixture.py")
    parser.add_argument("--identities", type=Path, required=True,
                        help="private identities-*.json for the same scope")
    parser.add_argument("--output", type=Path, required=True,
                        help="public report JSON path (parent dir is created)")
    parser.add_argument("--stage", default="window_open")
    parser.add_argument("--url", default=None, help="override the fixture's server URL")
    parser.add_argument("--reason", default="受控 Co-agent 关闭窗口以收拢；未使用真实模型。")
    parser.add_argument("--dry-run", action="store_true",
                        help="read the open window and print the plan without any write")
    args = parser.parse_args()
    result = consolidate(args)
    compact = {"mode": result["mode"], "real_model": result["real_model"], "stage": result["stage"],
               "dry_run": result["dry_run"], "active_opinions": len(result["active_opinion_ids"]),
               "candidate_ref": result.get("candidate_ref"),
               "controlled_consolidation_passed": result.get("controlled_consolidation_passed")}
    print(json.dumps(compact), flush=True)


if __name__ == "__main__":
    main()
