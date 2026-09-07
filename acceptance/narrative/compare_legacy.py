"""Compare restored legacy query/pack output with the real Narrative HTTP API.

All data stays on the local, explicitly restored convergence database. Existing
leaf vectors are replayed through a loopback embedding fixture; this does not
accept an embedding model or compression quality. Reports never contain queries,
enterprise prose, credentials, vectors, entity IDs or source document IDs.
"""
from __future__ import annotations

import argparse
from collections import Counter
from contextlib import contextmanager
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import math
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import threading
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

import httpx
from pgvector.psycopg import register_vector
import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict
from psycopg.rows import dict_row

from memory_service.context_graph import context_pack, query
from memory_service.context_graph.query_contracts import RetrievalBudgets

LEGACY_HEADER = "【历史语义背景：不替代 Runtime 交付、Outcome 或 MF 验收结论】\n"


def digest(value):
    raw = value if isinstance(value, str) else json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(raw.encode()).hexdigest()


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    path.chmod(0o600)


def trusted_state(path):
    path = path.resolve()
    assert path.parent.parent == ROOT / ".runtime-acceptance" and path.parent.name.startswith("convergence-"), "State must be inside a local convergence directory"
    assert path.stat().st_mode & 0o077 == 0, "Private state permissions must exclude group/other"
    state = json.loads(path.read_text())
    assert state["local_only"] is True
    dsn = conninfo_to_dict(state["APP_DATABASE_URL"])
    assert dsn.get("host") in {"127.0.0.1", "localhost", "::1"}
    assert not dsn.get("hostaddr") or dsn["hostaddr"] in {"127.0.0.1", "::1"}
    assert dsn.get("dbname", "").startswith("tkos_convergence_")
    for key in ("tenant_id", "company_id", "scope_id", "domain_id", "reader_token"):
        assert isinstance(state[key], str) and state[key]
    return state


@contextmanager
def connection(state, *, vectors=False):
    with psycopg.connect(state["APP_DATABASE_URL"], row_factory=dict_row, connect_timeout=5) as conn:
        if vectors:
            register_vector(conn)
            conn.commit()
        yield conn


def fingerprint(state):
    """Hash scoped legacy/governed rows under a real read-only transaction."""
    report = {}
    with connection(state) as conn, conn.transaction():
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        conn.execute("SELECT set_config('app.governed_scope_id',%s,true)", (state["scope_id"],))
        catalog = conn.execute("""SELECT c.relname AS name,array_agg(a.attname ORDER BY a.attnum) AS columns
          FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
          JOIN pg_attribute a ON a.attrelid=c.oid AND a.attnum>0 AND NOT a.attisdropped
          WHERE n.nspname='public' AND c.relkind='r'
          GROUP BY c.relname ORDER BY c.relname""").fetchall()
        for table in catalog:
            name, columns = table["name"], table["columns"]
            if name == "gov_credentials":
                continue
            if {"tenant_id", "organization_id"}.issubset(columns):
                predicate = sql.SQL("tenant_id=%s AND organization_id=%s")
                values = (state["tenant_id"], state["company_id"])
            elif name.startswith("gov_") and "scope_id" in columns:
                predicate = sql.SQL("scope_id=%s")
                values = (state["scope_id"],)
            else:
                continue
            cursor = conn.execute(sql.SQL("SELECT canonical FROM (SELECT to_jsonb(row_data)::text AS canonical FROM {} row_data WHERE {}) scoped_rows ORDER BY canonical COLLATE \"C\"").format(sql.Identifier(name), predicate), values)
            count, h = 0, hashlib.sha256()
            while rows := cursor.fetchmany(128):
                for row in rows:
                    raw = row["canonical"].encode()
                    h.update(len(raw).to_bytes(8, "big")); h.update(raw); count += 1
            report[name] = {"rows": count, "sha256": h.hexdigest()}
    assert {"semantic_entities", "semantic_relations", "context_graph_versions", "gov_scopes"}.issubset(report)
    return report


def leaves(state, limit):
    with connection(state) as conn, conn.transaction():
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        rows = conn.execute("""SELECT e.entity_id::text,e.name,e.embedding::text AS vector
          FROM semantic_entities e JOIN context_graph_versions v
            ON v.generation_id=e.graph_generation_id AND v.tenant_id=e.tenant_id AND v.organization_id=e.organization_id
          WHERE e.tenant_id=%s AND e.organization_id=%s AND v.status='current'
            AND e.status='confirmed' AND e.type_key IS NOT NULL AND e.type_key<>'CompanyVision'
            AND e.embedding IS NOT NULL AND length(btrim(e.name)) BETWEEN 1 AND 1800
            AND NOT EXISTS (SELECT 1 FROM semantic_relations r
              WHERE r.tenant_id=e.tenant_id AND r.organization_id=e.organization_id
                AND r.graph_generation_id=e.graph_generation_id AND r.target_id=e.entity_id
                AND r.relation_type='primary_alignment' AND r.status='confirmed')
          ORDER BY e.entity_id""", (state["tenant_id"], state["company_id"])).fetchall()
    cases = []
    for row in rows[:limit]:
        vector = json.loads(row["vector"])
        assert vector and all(isinstance(v, (int, float)) and math.isfinite(v) for v in vector) and any(vector)
        # A private stable suffix distinguishes duplicate real leaf names without
        # exposing names or IDs in reports. Both sides receive this exact query.
        private_query = row["name"].strip() + " [" + row["entity_id"] + "]"
        cases.append({"query": private_query, "vector": vector})
    return cases, len(rows)


class FixedReplay:
    def __init__(self, case): self.case = case
    def embed(self, texts):
        assert texts == [self.case["query"]]
        return [self.case["vector"]]


def old_pack(state, case):
    plan = query.plan_query(state["tenant_id"], state["company_id"], case["query"], embedder=FixedReplay(case),
        embedding_dim=len(case["vector"]), budgets=RetrievalBudgets(hit_limit=5, max_paths=5, lateral_budget=2))
    with connection(state, vectors=True) as conn:
        return context_pack.build_context_pack(query.execute_query(plan, conn=conn))


def source_provenance(pack):
    return [{"owner_kind": owner.owner_kind, "owner_id": owner.owner_id, "resolvable": owner.resolvable,
        "refs": [{"fragment_id": ref.fragment_id, "document_id": ref.document_id,
            "content_hash": ref.content_hash, "resolved": ref.resolved} for ref in owner.refs]}
        for owner in pack.source_resolutions]


@contextmanager
def replay_provider(cases):
    mapping = {case["query"]: case["vector"] for case in cases}
    calls = Counter()
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_): pass
        def do_POST(self):
            try:
                length = int(self.headers.get("Content-Length", "0"))
                assert self.path == "/embeddings/multimodal" and 0 < length < 20_000
                body = json.loads(self.rfile.read(length))
                assert len(body["input"]) == 1 and body["input"][0]["type"] == "text"
                private_query = body["input"][0]["text"]
                vector = mapping[private_query]
                calls[private_query] += 1
                raw = json.dumps({"data": {"embedding": vector}}).encode()
                self.send_response(200)
            except Exception:
                raw = b'{"error":"replay query mismatch"}'
                self.send_response(400)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers(); self.wfile.write(raw)
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", calls
    finally:
        server.shutdown(); server.server_close(); thread.join(timeout=3)


def serve(ready_file):
    import asyncio
    import uvicorn
    from memory_service_app.main import app
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    api_port = sock.getsockname()[1]
    class Server(uvicorn.Server):
        async def startup(self, sockets=None):
            await super().startup(sockets=sockets)
            if self.started: save(ready_file, {"url": f"http://127.0.0.1:{api_port}"})
    try:
        asyncio.run(Server(uvicorn.Config(app, host="127.0.0.1", port=api_port, access_log=False, log_level="critical")).serve(sockets=[sock]))
    finally: sock.close()


@contextmanager
def runtime_api(state, private, embedding_url, dimension):
    ready = private / "api-ready.json"
    ready.unlink(missing_ok=True)
    env = {key: os.environ[key] for key in ("PATH", "HOME", "LANG", "LC_ALL") if key in os.environ}
    env.update({"PYTHONPATH": str(ROOT / "src") + os.pathsep + str(ROOT), "DATABASE_URL": state["APP_DATABASE_URL"],
        "MEMORY_TENANT": state["tenant_id"], "MEMORY_ORG": state["company_id"],
        "TKOS_NARRATIVE_ENABLED": "1", "TKOS_NARRATIVE_LEGACY_ENABLED": "1", "TKOS_NARRATIVE_COMPRESSION": "none",
        "TKOS_NARRATIVE_DOMAIN_ID": state["domain_id"], "MEMORY_EMBEDDING_BASE_URL": embedding_url,
        "MEMORY_EMBEDDING_API_KEY": "local-fixed-replay-placeholder", "MEMORY_EMBEDDING_MODEL": "fixed-replay-not-a-model",
        "MEMORY_EMBEDDING_DIM": str(dimension)})
    # Deliberately no model credentials, external endpoints, inherited provider
    # settings, receiver or Worker. This process owns one read-only API surface.
    log_path = private / "api.log"
    with log_path.open("ab") as log:
        log_path.chmod(0o600)
        proc = subprocess.Popen([sys.executable, str(Path(__file__)), "--serve", str(ready)], cwd=ROOT, env=env,
            stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT)
    try:
        deadline = time.monotonic() + 25
        while not ready.exists():
            assert proc.poll() is None, "Owned API process exited"
            assert time.monotonic() < deadline, "Owned API readiness timeout"
            time.sleep(0.05)
        yield json.loads(ready.read_text())["url"]
    finally:
        if proc.poll() is None:
            proc.terminate()
            try: proc.wait(timeout=5)
            except subprocess.TimeoutExpired: proc.kill(); proc.wait(timeout=5)


def compare(state_file, minimum, clark_root):
    state = trusted_state(state_file)
    private = state_file.resolve().parent / "legacy-narrative-compare"
    private.mkdir(exist_ok=True); private.chmod(0o700)
    report_path = ROOT / "artifacts/runtime-acceptance" / state_file.parent.name / "legacy-narrative-comparison.json"
    report = {"status": "running", "local_restore_only": True, "fixed_existing_vectors_replayed": True,
        "real_embedding_model_accepted": False, "chat_model_called": False, "remote_services_accessed": False,
        "production_deployed": False, "cases": []}
    stage = "load_confirmed_leaves"
    try:
        cases, available = leaves(state, minimum)
        assert cases, "No eligible confirmed leaf with an existing vector"
        dimension = len(cases[0]["vector"])
        assert all(len(case["vector"]) == dimension for case in cases)
        report.update(requested_queries=minimum, available_confirmed_leaves=available, tested_queries=len(cases),
                      requested_sample_satisfied=len(cases) >= minimum)
        before = fingerprint(state)
        expected_client = []
        with replay_provider(cases) as (provider_url, calls), runtime_api(state, private, provider_url, dimension) as api_url:
            with httpx.Client(base_url=api_url, headers={"Authorization": "Bearer " + state["reader_token"]}, timeout=45, trust_env=False) as client:
                for ordinal, case in enumerate(cases, 1):
                    stage = f"query_{ordinal:02d}_old_core"
                    pack = old_pack(state, case)
                    legacy_raw = context_pack.render_narrative(pack)
                    root = context_pack.root_statement(pack)
                    sources = source_provenance(pack)
                    expected = {"generation_id": pack.generation.generation_id, "pack_sha256": digest(pack.to_json()), "source_resolutions": sources}
                    stage = f"query_{ordinal:02d}_new_http"
                    response = client.post("/v1/context-graph/narrative", json={"query": case["query"], "include_raw": True,
                        "tenant": state["tenant_id"], "org": state["company_id"], "domain_id": state["domain_id"]})
                    report["last_http_status"] = response.status_code
                    assert response.status_code == 200, f"Narrative HTTP status {response.status_code}"
                    result = response.json()
                    stage = f"query_{ordinal:02d}_legacy_pack_and_sources"
                    actual_legacy = result["provenance"]["legacy"]
                    report["last_comparison"] = {"generation_equal": actual_legacy["generation_id"] == expected["generation_id"],
                        "pack_hash_equal": actual_legacy["pack_sha256"] == expected["pack_sha256"],
                        "source_refs_equal": actual_legacy["source_resolutions"] == expected["source_resolutions"]}
                    assert {key: actual_legacy[key] for key in expected} == expected, "Legacy pack or exact source provenance changed"
                    assert actual_legacy["temporal_mode"] == "current_generation"
                    assert isinstance(actual_legacy["retrieved_at"], str)
                    stage = f"query_{ordinal:02d}_root_and_path_counts"
                    assert result["root_statement"] == root and result["hit_paths"] == len(pack.main_paths)
                    assert result["lateral_nodes"] == len(pack.lateral_nodes)
                    assert result["model"] == "deterministic-v1" and result["provenance"]["compression_mode"] == "none"
                    background = LEGACY_HEADER + legacy_raw
                    stage = f"query_{ordinal:02d}_raw_background"
                    assert result["narrative_raw"].startswith(background)
                    remainder = result["narrative_raw"][len(background):]
                    assert not remainder or remainder.startswith("\n\n以下是当前读权限下")
                    assert result["narrative"] == result["narrative_raw"]
                    stage = f"query_{ordinal:02d}_scope_identity"
                    assert result["provenance"]["scope_id"] == state["scope_id"]
                    assert result["provenance"]["domain_id"] == state["domain_id"]
                    if state.get("reader_principal_id"):
                        assert result["provenance"]["principal_id"] == state["reader_principal_id"]
                    source_refs = sum(len(owner["refs"]) for owner in sources)
                    unresolved = sum(not ref["resolved"] for owner in sources for ref in owner["refs"])
                    row = {"ordinal": ordinal, "matched": True, "paths": len(pack.main_paths), "lateral_nodes": len(pack.lateral_nodes),
                        "source_owners": len(sources), "source_refs": source_refs, "unresolved_source_refs": unresolved,
                        "all_source_owners_resolvable": all(owner["resolvable"] for owner in sources),
                        "pack_sha256": expected["pack_sha256"], "source_provenance_sha256": digest(sources),
                        "root_sha256": digest(root), "raw_background_sha256": digest(legacy_raw)}
                    report["cases"].append(row)
                    expected_client.append({"query": case["query"], "background_utf8_bytes": len(background.encode()),
                        "background_sha256": digest(background), "root_sha256": digest(root), "paths": len(pack.main_paths)})
                    print(json.dumps(row), flush=True)
                stage = "real_clark_client"
                config = private / "clark-client-config.json"
                client_report_path = private / "clark-client-report.json"
                save(config, {"state_file": str(state_file.resolve()), "clark_root": str(clark_root), "api_url": api_url,
                    "output_file": str(client_report_path)})
                node = shutil.which("node"); assert node
                completed = subprocess.run([node, str(Path(__file__).with_name("clark_legacy_client.cjs")), str(config)],
                    input=json.dumps(expected_client, ensure_ascii=False), capture_output=True, text=True, timeout=180, cwd=clark_root)
                assert completed.returncode == 0, "Real Clark client replay failed"
                client_report = json.loads(client_report_path.read_text())
                assert client_report["status"] == "passed" and len(client_report["cases"]) == len(cases)
                report["clark_client"] = client_report
                assert all(calls[case["query"]] == 3 for case in cases), "Expected one adapter and two real Clark calls per vector"
                report["embedding_replay_http_calls"] = sum(calls.values())
        stage = "verify_read_only_rows"
        after = fingerprint(state)
        assert before == after, "Legacy comparison changed restored database rows"
        report.update(status="passed", database_unchanged=True, row_fingerprints=after,
                      legacy_pack_and_sources_equal=True, root_and_raw_background_equal=True)
        report["source_sha256"] = {str(path.relative_to(ROOT)): digest(path.read_text()) for path in (
            Path(__file__), Path(__file__).with_name("clark_legacy_client.cjs"), ROOT / "src/memory_service_app/narrative.py",
            ROOT / "src/memory_service/context_graph/query.py", ROOT / "src/memory_service/context_graph/context_pack.py",
            ROOT / "src/memory_service/context_graph/query_repository.py")}
    except Exception as exc:
        report.update(status="failed", failed_stage=stage, error_type=type(exc).__name__)
        save(report_path, report)
        print(json.dumps({"status": "failed", "stage": stage, "error_type": type(exc).__name__}), flush=True)
        return 1
    save(report_path, report)
    print(json.dumps({"status": "passed", "tested_queries": len(report["cases"]), "report": str(report_path), "real_embedding_model_accepted": False}), flush=True)
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("state", type=Path, nargs="?")
    parser.add_argument("--queries", type=int, default=10)
    parser.add_argument("--clark-root", type=Path, default=Path("/Users/yusiyi/ysy/clark"))
    parser.add_argument("--serve", type=Path)
    args = parser.parse_args()
    if args.serve:
        serve(args.serve); return 0
    if not args.state or not 10 <= args.queries <= 50:
        parser.error("Provide private state; --queries must be between 10 and 50")
    try: return compare(args.state, args.queries, args.clark_root)
    except Exception as exc:
        print(json.dumps({"status": "failed", "stage": "validate_private_state", "error_type": type(exc).__name__}), flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
