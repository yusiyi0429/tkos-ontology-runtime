"""Deterministic overlap of two actual Profile CLI transactions.

Only pause instrumentation runs after the real global identity SELECT. SQL,
query results and installation decisions are unchanged. A fixed implementation
may serialize the second connection before that SELECT; actual PostgreSQL lock
and backend observations distinguish it from an accidentally sequential run.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
import time
import uuid

import psycopg
from psycopg.rows import dict_row

from .control_adapter import NotReady
from .support import private_json, public_json


def run_pair(h, adapter, jobs, *, label):
    assert len(jobs) == 2
    token = uuid.uuid4().hex
    checkpoints = [{"paused": str(h.private / (label + f"-{n}-paused.json")),
        "release": str(h.private / (label + f"-{n}-release.json")),
        "token": token + str(n), "application_name": "a1-profile-" + token[:16] + str(n)} for n in (0, 1)]
    from pathlib import Path
    for check in checkpoints:
        assert not Path(check["paused"]).exists() and not Path(check["release"]).exists()
    evidence = {"instrumentation": "pause_after_real_identity_select_only", "transactions_overlapped": False,
        "sql_or_results_modified": False, "mode": None, "backends": [], "locks": []}
    futures = []

    def invoke(n):
        return adapter.cli(jobs[n]["case_id"], jobs[n]["arguments"],
            expected_exit=jobs[n]["expected_exit"], identity_checkpoint=checkpoints[n])

    def release(n):
        path = Path(checkpoints[n]["release"])
        if not path.exists():
            private_json(path, {"token": checkpoints[n]["token"]})

    def observe():
        with psycopg.connect(h.env.values["MIGRATION_DATABASE_URL"], row_factory=dict_row) as conn:
            activity = conn.execute("""SELECT pid,application_name,state,wait_event_type,wait_event,
                pg_blocking_pids(pid) AS blocking_pids FROM pg_stat_activity
                WHERE datname=current_database() AND application_name=ANY(%s) ORDER BY application_name""",
                ([item["application_name"] for item in checkpoints],)).fetchall()
            pids = [item["pid"] for item in activity]
            locks = conn.execute("""SELECT pid,locktype,mode,granted,relation::regclass::text AS relation,
                classid,objid,objsubid FROM pg_locks WHERE pid=ANY(%s)
                ORDER BY pid,locktype,mode,granted""", (pids,)).fetchall() if pids else []
        return activity, locks

    with ThreadPoolExecutor(max_workers=2) as executor:
        try:
            futures.append(executor.submit(invoke, 0))
            deadline = time.monotonic() + 12
            first_file = Path(checkpoints[0]["paused"])
            while not first_file.exists():
                if futures[0].done():
                    futures[0].result()  # Surface actual CLI rejection first.
                    raise AssertionError("first CLI returned without reaching real identity SELECT")
                if time.monotonic() >= deadline:
                    raise NotReady("first real CLI identity SELECT checkpoint was not reached")
                time.sleep(0.025)
            first = json.loads(first_file.read_text())
            assert first["phase"] == "after_real_global_profile_identity_select" and first["sql_result_modified"] is False
            assert first["application_name"] == checkpoints[0]["application_name"]
            futures.append(executor.submit(invoke, 1))
            deadline = time.monotonic() + 12
            while True:
                activity, locks = observe()
                by_name = {row["application_name"]: row for row in activity}
                second = by_name.get(checkpoints[1]["application_name"])
                if second and len(activity) == 2:
                    assert by_name[checkpoints[0]["application_name"]]["pid"] == first["backend_pid"]
                    second_file = Path(checkpoints[1]["paused"])
                    if second_file.exists():
                        pause = json.loads(second_file.read_text())
                        assert pause["backend_pid"] == second["pid"] and pause["sql_result_modified"] is False
                        evidence["mode"] = "both_real_identity_selects_completed_before_either_install"
                        break
                    if second["wait_event_type"] == "Lock" and first["backend_pid"] in second["blocking_pids"]:
                        assert any(row["pid"] == second["pid"] and row["granted"] is False for row in locks)
                        evidence["mode"] = "second_real_transaction_blocked_by_first_before_identity_select"
                        break
                if futures[1].done():
                    futures[1].result()
                    raise AssertionError("second CLI completed without a proven concurrency window")
                if time.monotonic() >= deadline:
                    raise NotReady("no true PostgreSQL Profile concurrency window was observed")
                time.sleep(0.025)
            evidence.update(transactions_overlapped=True, backends=activity, locks=locks)
            # Release tokens can be created before a blocked second SELECT
            # returns; its wrapper still captures the unmodified result first.
            release(1)
            release(0)
            outcomes = [future.result(timeout=20) for future in futures]
            evidence["exit_codes"] = [item["exit_code"] for item in outcomes]
            return outcomes, evidence
        finally:
            release(0)
            # Only release a submitted second process; a stale token must not
            # trick a later child into bypassing fresh checkpoint validation.
            if len(futures) == 2:
                release(1)
            public_json(h.output / (label + "-concurrency-window.json"), evidence)
