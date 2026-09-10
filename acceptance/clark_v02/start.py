"""Start one isolated Clark -> Runtime acceptance scope without seeding deliveries."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import signal
import subprocess
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from acceptance.runtime.harness import Harness, port, private_json, wait_until
from acceptance.runtime.run import Scenario
from acceptance.runtime.seed import create_fixture


def setup_one(s: Scenario, key: str, label: str) -> dict:
    """Provision only the offered starting point through authenticated HTTP."""
    outcome = s.create("CompanyOutcome", {
        "title": "Clark 联调：达到 80 项合格交付",
        "terms": {"metric_id": "qualified-deliveries", "target": 80, "unit": "deliveries"},
        "outcome_statement": "达到 80 项合格交付，并保留可复核来源证据", "upstream_refs": [],
    })
    s.act(s.ceo, "confirm_outcome", {}, oid=outcome)
    bc, signatures = s.signed("BusinessCommitment", outcome)
    bc_activation = s.activate(bc, signatures, outcome)
    ec, signatures = s.signed("ExecutionCommitment", bc)
    ec_activation = s.activate(ec, signatures, bc)
    feedback = s.feedback_investigation("Clark 联调：交付资料完整性反馈")
    title = "Clark 联调：" + label
    oid = s.create("WorkItem", {
            "title": title, "execution_commitment_ref": s.upstream(ec),
            "dri_assignment_id": s.f["actors"]["mission_dri"]["assignment_id"],
            "acceptor_assignment_id": s.f["actors"]["verifier"]["assignment_id"],
            "acceptance_criteria": [
                {"criterion_id": "complete", "description": "交付包包含完整原始来源"},
                {"criterion_id": "consistent", "description": "摘要数值与来源一致"},
            ],
            "feedback_ref": {"object_id": feedback, "revision_id": s.object(feedback)["latest_revision_id"]},
    })
    assert s.object(oid)["lifecycle_status"] == "offered"
    work_item = {"objectId": oid, "title": title, "outcomeObjectId": outcome, "feedbackObjectId": feedback}
    tasks = bc_activation["effect_task_ids"] + ec_activation["effect_task_ids"]
    task_results = s.wait_tasks(tasks)
    assert s.object(outcome)["outcome_achievement"] == "not_assessed"
    assert s.object(feedback)["lifecycle_status"] == "investigating"
    return {"outcome_id": outcome, "business_commitment_id": bc, "execution_commitment_id": ec,
            "feedback_id": feedback, "work_item": work_item, "activation_tasks": task_results}


def setup(s: Scenario) -> dict:
    s.start_worker()
    scenarios = {key: setup_one(s, key, label)
                 for key, label in (("browser", "浏览器交付闭环"), ("http", "HTTP 权限与版本边界"))}
    return {"scenarios": scenarios, "work_items": {key: value["work_item"] for key, value in scenarios.items()}}


def identity_files(s: Scenario, business: dict) -> tuple[Path, Path]:
    config_file = s.h.private / "clark-identities.json"
    codes_file = s.h.private / "clark-access-codes.json"
    names = {
        "ceo": ("CEO", "陈总（测试 CEO）"),
        "mission_dri": ("MISSION_DRI", "李交付（测试 DRI）"),
        "verifier": ("VERIFIER", "王验收（测试验收人）"),
        "domain_dri": ("DOMAIN_DRI", "赵领域（测试领域 DRI）"),
        "outsider": ("OUTSIDER", "周外域（测试越权身份）"),
        "agent": ("AGENT", "自动助手（测试 Agent）"),
    }
    codes = {name: secrets.token_urlsafe(24) for name in names}
    identities = [{"id": name, "name": label, "role": role,
                   "assignmentIds": [s.f["actors"][name]["assignment_id"]],
                   "accessCodeHash": hashlib.sha256(codes[name].encode()).hexdigest(),
                   "runtimeToken": s.f["actors"][name]["token"]}
                  for name, (role, label) in names.items()]
    private_json(config_file, {"testOnly": True, "sessionSecret": secrets.token_urlsafe(48),
                              "identities": identities, "workItems": list(business["work_items"].values())})
    private_json(codes_file, codes)
    return config_file, codes_file


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--clark-root", type=Path, default=Path("/Users/yusiyi/ysy/clark"))
    parser.add_argument("--run-id")
    parser.add_argument("--next-dist-dir", help="Separate Next build directory when another local validation server is running")
    parser.add_argument("--keep", action="store_true", help="Keep only owned processes alive for browser acceptance")
    parser.add_argument("--stop", type=Path, help="Request graceful stop using this run's private state.json")
    parser.add_argument("--resume-state", type=Path, help="Resume a stopped run with the same persisted business objects, identities and Clark port")
    args = parser.parse_args()
    if args.stop:
        state = json.loads(args.stop.read_text())
        stop_file = Path(state["stop_file"])
        if stop_file.parent != args.stop.resolve().parent:
            raise ValueError("Stop file must belong to the selected run")
        stop_file.touch()
        print(json.dumps({"stop_requested": True, "run_id": state["run_id"]}))
        return

    resumed = json.loads(args.resume_state.read_text()) if args.resume_state else None
    next_dist_dir = args.next_dist_dir or (resumed or {}).get("next_dist_dir", ".next-clark-v02")
    if (not next_dist_dir.startswith(".next-") or len(next_dist_dir) > 100
            or not all(character.isascii() and (character.isalnum() or character in ".-_")
                       for character in next_dist_dir)):
        raise ValueError("Next build directory must be a local .next-* directory name")
    if resumed:
        try:
            os.kill(resumed["owner_pid"], 0)
        except ProcessLookupError:
            pass
        else:
            raise RuntimeError("The previous owner is still running; stop it before resuming")
        run_id = resumed["run_id"]
        fixture_file = Path(resumed["fixture_file"])
    else:
        run_id = args.run_id or "clark-v02-" + uuid.uuid4().hex[:12]
        _, fixture_file = create_fixture(run_id)
    h = Harness(run_id=run_id)
    next_process = None
    stopping = False
    state_file = h.private / "state.json"
    stop_file = h.private / "stop-requested"
    if resumed:
        stop_file.unlink(missing_ok=True)
        (h.private / "receiver-ready.json").unlink(missing_ok=True)

    def requested_stop(*_):
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, requested_stop)
    signal.signal(signal.SIGINT, requested_stop)
    try:
        s = Scenario(h, json.loads(fixture_file.read_text()))
        if resumed:
            business = resumed["business"]
            identities_file, codes_file = Path(resumed["identities_file"]), Path(resumed["codes_file"])
            s.start_worker()
            print(json.dumps({"resumed": True, "run_id": run_id, "business_recreated": False}), flush=True)
        else:
            with h.group("clark_fixture_http_business_setup") as report:
                business = setup(s)
                report.update(business=business, delivery_preseeded=False, outcome_assessment_preseeded=False,
                              mf_closed=False, activation_effects_terminal=True)
            identities_file, codes_file = identity_files(s, business)
        from urllib.parse import urlsplit
        clark_port = urlsplit(resumed["clark_url"]).port if resumed else port()
        clark_url = f"http://127.0.0.1:{clark_port}"
        shared_password = json.loads(Path(resumed["shared_access_file"]).read_text())["password"] if resumed else secrets.token_urlsafe(24)
        private_json(h.private / "clark-shared-access.json", {"password": shared_password})
        clark_env = {key: os.environ[key] for key in ("PATH", "HOME", "LANG", "LC_ALL") if key in os.environ}
        # Empty explicit values override Next's .env.local; mocks prevent model/search calls.
        clark_env.update({key: "" for key in (
            "ARK_API_KEY", "DEEPSEEK_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "DASHSCOPE_API_KEY",
            "MINIMAX_API_KEY", "ASR_API_KEY", "TTS_API_KEY", "TKOS_MEMORY_API_KEY", "TKOS_MEMORY_API_BASE_URL",
            "TKOS_GRAPHKNOWLEDGE_AUTH", "TKOS_GRAPHKNOWLEDGE_BASE_URL", "OPERATOR_TOKEN")})
        clark_env.update({
            "NODE_ENV": "development", "NEXT_TELEMETRY_DISABLED": "1", "TKOS_DEV_PIN": "0",
            "NEXT_DIST_DIR": next_dist_dir,
            "TKOS_MODE": "demo", "TKOS_MODEL_MOCK": "1", "TKOS_SEARCH_PROVIDER": "none",
            "TKOS_GRAPHKNOWLEDGE_MOCK": "1", "TKOS_PERSIST": "0", "TKOS_DATA_DIR": str(h.private / "clark-data"),
            "TKOS_RUNTIME_ENABLED": "1", "TKOS_RUNTIME_BASE_URL": s.api_url,
            "TKOS_RUNTIME_CLARK_ORIGIN": clark_url,
            "TKOS_RUNTIME_IDENTITIES_FILE": str(identities_file),
            "TKOS_ACCESS_PASSWORD": shared_password, "TKOS_ACCESS_SECRET": secrets.token_urlsafe(48),
        })
        node = shutil.which("node")
        if not node:
            raise RuntimeError("Node.js is required")
        next_bin = args.clark_root / "node_modules/next/dist/bin/next"
        next_log = h.private / "clark-next.log"
        with next_log.open("ab") as log:
            next_log.chmod(0o600)
            next_process = subprocess.Popen(
                [node, str(next_bin), "dev", "--hostname", "127.0.0.1", "--port", str(clark_port)],
                cwd=args.clark_root, env=clark_env, stdin=subprocess.DEVNULL,
                stdout=log, stderr=subprocess.STDOUT, start_new_session=True,
            )
        h.wait_http(clark_url + "/api/health")
        state = {"run_id": run_id, "api_url": s.api_url, "clark_url": clark_url,
                 "fixture_file": str(fixture_file), "identities_file": str(identities_file),
                 "codes_file": str(codes_file), "shared_access_file": str(h.private / "clark-shared-access.json"),
                 "report_directory": str(h.output), "private_directory": str(h.private),
                 "stop_file": str(stop_file), "business": business, "owner_pid": os.getpid(),
                 "processes": {name: process.pid for name, process in h.processes}, "clark_pid": next_process.pid,
                 "next_dist_dir": clark_env["NEXT_DIST_DIR"],
                 "resumed": bool(resumed),
                 "state": "ready", "clark_integration_accepted": False, "production_deployed": False}
        private_json(state_file, state)
        print(json.dumps({"state_file": str(state_file), "clark_url": clark_url, "api_url": s.api_url,
                          "browser_work_item": business["work_items"]["browser"]["objectId"]}), flush=True)
        if args.keep:
            while not stopping and not stop_file.exists():
                if next_process.poll() is not None or any(proc.poll() is not None for _, proc in h.processes):
                    raise RuntimeError("An owned acceptance process exited; inspect private logs")
                time.sleep(0.25)
        state["state"] = "stopped"
        private_json(state_file, state)
    finally:
        if next_process and next_process.poll() is None:
            os.killpg(next_process.pid, signal.SIGTERM)
            try:
                next_process.wait(timeout=8)
            except subprocess.TimeoutExpired:
                os.killpg(next_process.pid, signal.SIGKILL)
                next_process.wait(timeout=8)
        h.stop_all()


if __name__ == "__main__":
    main()
