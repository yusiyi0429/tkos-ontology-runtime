#!/usr/bin/env python3
"""Runtime 容量与恢复基线（票 #32 批次 C），按 docs/runtime-capacity-baseline.md 的 T1–T8 测量。

在 `acceptance/runtime/infra.py up` 的隔离栈上运行：
- 播种两家合成公司（A、B），用测试专用启动器 acceptance/runtime/server.py 起 API；
- 它能在拿到 scope 栅栏之后挂起一个事务，用来模拟慢事务；
- 客户端逐场景计时，另从 API 的运行记录（governed_transaction）读取等锁耗时。

凭证只在私有目录。结果只有耗时、计数、状态码与通过与否，写到 artifacts/ 下本次运行的目录；
加 --summary 时把同一份结果另写到给定路径，便于入库。
"""
from __future__ import annotations

import argparse
import asyncio
import base64
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import signal
import statistics
import sys
import threading
import time
from urllib.parse import urlsplit
import uuid

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

import httpx
import psycopg

from acceptance.runtime.harness import Harness, wait_until
from memory_service_runtime.governed import db
from memory_service_runtime.governed.bootstrap import seed_scope

VARIANTS = {
    # 关掉锁与语句超时，近似改动之前的行为。
    "V0": {"GOVERNED_LOCK_TIMEOUT_MS": "0", "GOVERNED_STATEMENT_TIMEOUT_MS": "0"},
    # 当前默认值。
    "V1": {},
}
LOCK_BOUND_S = 5 + 1  # T4/T8：默认 GOVERNED_LOCK_TIMEOUT_MS 加 1 秒
DENIED = {401, 403, 404}


def pct(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return round(ordered[min(len(ordered) - 1, int(round(q * (len(ordered) - 1))))], 1)


def stats(values: list[float]) -> dict:
    return {"n": len(values), "p50_ms": pct(values, 0.5), "p95_ms": pct(values, 0.95),
            "max_ms": round(max(values), 1) if values else None,
            "mean_ms": round(statistics.fmean(values), 1) if values else None}


class DelayProxy:
    """对象存储前的 TCP 转发：每个响应分块晚 delay 秒送回，模拟慢存储。"""

    def __init__(self, upstream: str, delay: float) -> None:
        parts = urlsplit(upstream)
        self.host, self.port, self.delay = parts.hostname, parts.port, delay
        self.loop = asyncio.new_event_loop()
        self.ready = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()
        self.ready.wait(10)

    def _run(self) -> None:
        asyncio.set_event_loop(self.loop)
        self.server = self.loop.run_until_complete(asyncio.start_server(self._handle, "127.0.0.1", 0))
        self.url = f"http://127.0.0.1:{self.server.sockets[0].getsockname()[1]}"
        self.ready.set()
        self.loop.run_forever()

    async def _handle(self, reader, writer) -> None:
        up_reader, up_writer = await asyncio.open_connection(self.host, self.port)

        async def pump(source, sink, delay):
            try:
                while data := await source.read(65536):
                    if delay:
                        await asyncio.sleep(delay)
                    sink.write(data)
                    await sink.drain()
            finally:
                sink.close()

        await asyncio.gather(pump(reader, up_writer, 0), pump(up_reader, writer, self.delay),
                             return_exceptions=True)

    def close(self) -> None:
        self.loop.call_soon_threadsafe(self.server.close)
        self.loop.call_soon_threadsafe(self.loop.stop)


class Baseline:
    def __init__(self) -> None:
        self.h = Harness(run_id=f"baseline-{uuid.uuid4().hex[:12]}")
        self.api = None
        self.api_log: Path | None = None

    def seed(self, variant: str) -> None:
        """每种配置用两家新公司：撤权场景会永久收回一个指派。"""
        label = f"{self.h.run_id}-{variant.lower()}"
        with psycopg.connect(self.h.env["MIGRATION_DATABASE_URL"]) as conn:
            self.a = seed_scope(conn, f"runtime-acceptance-{label}-a", f"runtime-acceptance-{label}-a-company")
            self.b = seed_scope(conn, f"runtime-acceptance-{label}-b", f"runtime-acceptance-{label}-b-company")
        self.h.env.update(MEMORY_TENANT=self.a["tenant_id"], MEMORY_ORG=self.a["company_id"])

    # -- process ---------------------------------------------------------------
    def start_api(self, variant: str, extra: dict | None = None) -> None:
        name = f"api-{variant}-{uuid.uuid4().hex[:6]}"
        ready = self.h.private / f"{name}-ready.json"
        updates = {**VARIANTS[variant], **(extra or {})}
        self.api = self.h.spawn(name, "server.py", "--port", 0, "--control-dir", self.h.control,
                                "--key-file", self.h.key_file, "--ready-file", ready, updates=updates)
        self.api_log = self.h.private / f"{name}.log"
        self.url = wait_until(lambda: json.loads(ready.read_text()) if ready.exists() else None,
                              timeout=25, message="API start")["url"]
        self.h.wait_http(self.url + "/v1/health")

    def stop_api(self, *, kill: bool = False) -> None:
        if self.api is not None:
            if kill:
                self.api.send_signal(signal.SIGKILL)
                self.api.wait(timeout=10)
            else:
                self.h.stop(self.api)
            self.api = None

    def records(self) -> dict[str, list[dict]]:
        found: dict[str, list[dict]] = {}
        for line in self.api_log.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.startswith('{"event"'):
                record = json.loads(line)
                found.setdefault(record.get("request_id") or "", []).append(record)
        return found

    # -- requests --------------------------------------------------------------
    def client(self) -> httpx.Client:
        return httpx.Client(base_url=self.url, trust_env=False, timeout=60)

    def call(self, client, method, path, token, body=None, *, rid=None, headers=None):
        rid = rid or f"bl-{uuid.uuid4().hex}"
        started = time.perf_counter()
        try:
            response = client.request(method, path, json=body, headers={
                "Authorization": f"Bearer {token}", "X-Request-ID": rid, **(headers or {})})
            status = response.status_code
            data = response.json() if "json" in response.headers.get("content-type", "") else None
        except httpx.TransportError as exc:
            status, data = f"transport:{type(exc).__name__}", None
        return {"rid": rid, "status": status, "ms": (time.perf_counter() - started) * 1000, "data": data}

    def read(self, client, fixture, actor="ceo", **kwargs):
        return self.call(client, "GET", f"/v1/objects/{fixture['outcome']['object_id']}",
                         fixture["actors"][actor]["token"], **kwargs)

    def write(self, client, fixture, key=None):
        command = {"action_type": "create_object", "target": None, "expected_versions": [],
                   "idempotency_key": key or f"baseline-{uuid.uuid4()}",
                   "reason": "Runtime capacity baseline with synthetic data",
                   "params": {"object_type": "CompanyOutcome", "domain_id": fixture["domain_id"],
                              "payload": {"title": "Synthetic baseline outcome", "terms": {"target": 1}}}}
        token = fixture["actors"]["ceo"]["token"]
        started = time.perf_counter()
        prepared = self.call(client, "POST", "/v1/actions/prepare", token, command)
        if prepared["status"] != 200:
            return {**prepared, "ms": (time.perf_counter() - started) * 1000, "key": command["idempotency_key"]}
        committed = self.call(client, "POST", "/v1/actions", token, command)
        return {**committed, "ms": (time.perf_counter() - started) * 1000, "key": command["idempotency_key"]}

    def hold_fence(self, fixture, token_name: str):
        """发一个在拿到 scope 栅栏后挂起的读，返回线程；release 之前这家公司的栅栏一直被占着。"""
        box: dict = {}

        def run():
            with self.client() as client:
                box["result"] = self.read(client, fixture, headers=self.h.headers(
                    "auth_fence_acquired", "barrier", token_name))

        thread = threading.Thread(target=run, daemon=True)
        thread.start()
        self.h.reached(token_name)
        return thread, box

    # -- scenarios -------------------------------------------------------------
    def t1(self) -> dict:
        with self.client() as client:
            self.read(client, self.a)
            reads = [self.read(client, self.a) for _ in range(50)]
            writes = [self.write(client, self.a) for _ in range(20)]
        read, write = stats([r["ms"] for r in reads]), stats([w["ms"] for w in writes])
        ok = all(r["status"] == 200 for r in reads + writes)
        return {"reads": read, "writes": write, "all_ok": ok,
                "pass": ok and read["p95_ms"] <= 150 and write["p95_ms"] <= 400}

    def t2(self) -> dict:
        def worker(_):
            with self.client() as client:
                return [self.read(client, self.a) for _ in range(25)]

        started = time.perf_counter()
        with ThreadPoolExecutor(8) as pool:
            results = [item for batch in pool.map(worker, range(8)) for item in batch]
        elapsed = time.perf_counter() - started
        records = self.records()
        waits = [rec["lock_wait_ms"] for r in results for rec in records.get(r["rid"], [])
                 if rec["event"] == "governed_transaction" and rec.get("lock_wait_ms") is not None]
        ok = all(r["status"] == 200 for r in results)
        throughput = round(len(results) / elapsed, 1)
        return {"requests": len(results), "throughput_per_s": throughput, "client": stats([r["ms"] for r in results]),
                "lock_wait": stats(waits), "all_ok": ok, "pass": ok and throughput >= 50}

    def held(self, queued: int, hold_s: float) -> dict:
        token = f"hold-{uuid.uuid4().hex[:8]}"
        with self.client() as client:
            baseline_b = [self.read(client, self.b) for _ in range(30)]
        thread, box = self.hold_fence(self.a, token)
        held_at = time.monotonic()

        def queued_read(_):
            with self.client() as client:
                return self.read(client, self.a)

        with ThreadPoolExecutor(queued) as pool:
            futures = [pool.submit(queued_read, n) for n in range(queued)]
            time.sleep(1)
            during_b: list[dict] = []

            def read_b():
                with self.client() as client:
                    while time.monotonic() - held_at < hold_s - 0.5:
                        during_b.append(self.read(client, self.b))

            reader = threading.Thread(target=read_b, daemon=True)
            reader.start()
            time.sleep(max(0.0, hold_s - (time.monotonic() - held_at)))
            self.h.release(token)
            reader.join(timeout=60)
            queued_results = [f.result(timeout=120) for f in futures]
        thread.join(timeout=60)
        return {"baseline_b": baseline_b, "during_b": during_b, "queued": queued_results,
                "holder": box.get("result")}

    def t3_t4(self) -> dict:
        run = self.held(queued=8, hold_s=10)
        base = stats([r["ms"] for r in run["baseline_b"]])
        during = stats([r["ms"] for r in run["during_b"]])
        b_errors = sum(r["status"] != 200 for r in run["during_b"])
        queued = run["queued"]
        t3 = {"b_baseline": base, "b_during": during, "b_errors": b_errors,
              "pass": b_errors == 0 and bool(run["during_b"]) and during["p95_ms"] <= 3 * base["p95_ms"]}
        # T4 看的是等 scope 栅栏的时长，按服务端运行记录算：拿到锁的取 lock_wait_ms，
        # 超时的取事务里停留的 held_ms；取连接的等待另列，客户端总时长也另列。
        records = self.records()
        fence, pool_wait = [], []
        for r in queued:
            for rec in records.get(r["rid"], []):
                if rec["event"] == "governed_transaction":
                    fence.append(rec["lock_wait_ms"] if rec["lock_wait_ms"] is not None else rec["held_ms"])
                    pool_wait.append(rec["pool_wait_ms"])
        statuses = sorted({str(r["status"]) for r in queued})
        t4 = {"queued_statuses": statuses, "queued_client": stats([r["ms"] for r in queued]),
              "fence_wait": stats(fence), "pool_wait": stats(pool_wait), "fence_records": len(fence),
              "pass": len(fence) == len(queued) and max(fence) / 1000 <= LOCK_BOUND_S
              and all(r["status"] in (200, 503) for r in queued) and any(r["status"] == 503 for r in queued)}
        return {"T3": t3, "T4": t4}

    def t8(self) -> dict:
        run = self.held(queued=12, hold_s=15)
        during = run["during_b"]
        errors = [str(r["status"]) for r in during if r["status"] != 200]
        slowest = max((r["ms"] for r in during), default=0) / 1000
        return {"b_during": stats([r["ms"] for r in during]), "b_errors": errors,
                "b_slowest_s": round(slowest, 2),
                "queued_statuses": sorted({str(r["status"]) for r in run["queued"]}),
                "pass": bool(during) and not errors and slowest <= LOCK_BOUND_S}

    def t5(self, variant: str) -> dict:
        proxy = DelayProxy(self.h.env["TKOS_OBJECT_STORE_ENDPOINT"], 2.0)
        self.stop_api()
        self.start_api(variant, {"TKOS_OBJECT_STORE_ENDPOINT": proxy.url})
        try:
            box: dict = {}

            def upload():
                with self.client() as client:
                    box["upload"] = self.call(client, "POST", "/v1/evidence-assets",
                                              self.a["actors"]["ceo"]["token"],
                                              {"domain_id": self.a["domain_id"], "title": "Synthetic slow-storage evidence",
                                               "content_base64": base64.b64encode(b"baseline evidence\n").decode(),
                                               "media_type": "text/plain"})

            uploader = threading.Thread(target=upload, daemon=True)
            uploader.start()
            time.sleep(0.3)
            reads = []
            with self.client() as client:
                while uploader.is_alive():
                    reads.append(self.read(client, self.a))
                    time.sleep(0.1)
            uploader.join()
            upload_result = box["upload"]
            read = stats([r["ms"] for r in reads])
            return {"upload_status": upload_result["status"], "upload_ms": round(upload_result["ms"], 1),
                    "reads_during_upload": read,
                    "read_statuses": sorted(str(r["status"]) for r in reads),
                    "pass": upload_result["status"] == 200 and bool(reads) and read["p95_ms"] <= 500}
        finally:
            self.stop_api()
            proxy.close()
            self.start_api(variant)

    def t6(self) -> dict:
        dri = self.a["actors"]["domain_dri"]
        with self.client() as client:
            before = self.read(client, self.a, actor="domain_dri")
        token = f"revoke-{uuid.uuid4().hex[:8]}"
        thread, _ = self.hold_fence(self.a, token)
        box: dict = {}

        def queued_read():
            with self.client() as client:
                box["in_flight"] = self.read(client, self.a, actor="domain_dri")

        def revoke():
            with self.client() as client:
                box["revoke"] = self.call(client, "POST", "/v1/actions", self.a["actors"]["ceo"]["token"], {
                    "action_type": "revoke_assignment", "target": None, "expected_versions": [],
                    "idempotency_key": f"baseline-revoke-{uuid.uuid4()}", "reason": "Baseline revocation race",
                    "params": {"assignment_id": dri["assignment_id"]}})

        first = threading.Thread(target=queued_read, daemon=True)
        first.start()
        time.sleep(0.5)
        second = threading.Thread(target=revoke, daemon=True)
        second.start()
        time.sleep(0.5)
        self.h.release(token)
        for item in (first, second, thread):
            item.join(timeout=60)
        with self.client() as client:
            after = self.read(client, self.a, actor="domain_dri")
        in_flight_first = self._completed_before(box["in_flight"]["rid"], box["revoke"]["rid"])
        consistent = (box["in_flight"]["status"] == 200) if in_flight_first else (box["in_flight"]["status"] in DENIED)
        return {"before": before["status"], "in_flight": box["in_flight"]["status"],
                "in_flight_completed_before_revoke": in_flight_first, "revoke": box["revoke"]["status"],
                "after": after["status"],
                "pass": before["status"] == 200 and box["revoke"]["status"] == 200
                and after["status"] in DENIED and consistent}

    def _completed_before(self, first_rid: str, second_rid: str) -> bool:
        seen = []
        for line in self.api_log.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.startswith('{"event":"governed_transaction"'):
                rid = json.loads(line).get("request_id")
                if rid in (first_rid, second_rid):
                    seen.append(rid)
        return bool(seen) and seen[0] == first_rid

    def t7(self, variant: str) -> dict:
        with self.client() as client:
            original = self.write(client, self.a)
        keys: list[str] = []
        stop = threading.Event()

        def burst():
            with self.client() as client:
                while not stop.is_set():
                    key = f"baseline-burst-{uuid.uuid4()}"
                    keys.append(key)
                    self.write(client, self.a, key)

        writer = threading.Thread(target=burst, daemon=True)
        writer.start()
        time.sleep(1.5)
        self.stop_api(kill=True)
        killed_at = time.monotonic()
        stop.set()
        writer.join(timeout=30)
        self.start_api(variant)
        recovered_s = round(time.monotonic() - killed_at, 2)
        with self.client() as client:
            replay = self.write(client, self.a, original["key"])
            replays = [self.write(client, self.a, key) for key in keys]
        same = replay["status"] == 200 and replay["data"]["receipt_id"] == original["data"]["receipt_id"]
        with psycopg.connect(self.h.env["MIGRATION_DATABASE_URL"]) as conn:
            conn.execute("SELECT set_config('app.governed_scope_id', %s, false)", (self.a["scope_id"],))
            db.set_write_capability(conn)
            receipts, objects = conn.execute(
                """SELECT (SELECT count(*) FROM gov_action_receipts WHERE scope_id=%s AND action_type='create_object'),
                          (SELECT count(*) FROM gov_objects WHERE scope_id=%s AND object_type='CompanyOutcome')""",
                (self.a["scope_id"], self.a["scope_id"])).fetchone()
        consistent = objects == receipts + 1  # 播种时直接写入的一个 CompanyOutcome 没有 create_object 回执
        return {"recovered_s": recovered_s, "replay_same_receipt": same,
                "burst_keys": len(keys), "burst_replays_ok": all(r["status"] == 200 for r in replays),
                "receipts": receipts, "objects": objects,
                "pass": recovered_s <= 10 and same and consistent and all(r["status"] == 200 for r in replays)}

    # -- run -------------------------------------------------------------------
    def run(self, variants: list[str]) -> dict:
        report = {"run_id": self.h.run_id, "thresholds": "docs/runtime-capacity-baseline.md", "variants": {}}
        try:
            for variant in variants:
                self.seed(variant)
                self.start_api(variant)
                result = {"T1": self.t1(), "T2": self.t2(), **self.t3_t4(), "T8": self.t8(),
                          "T5": self.t5(variant), "T6": self.t6(), "T7": self.t7(variant)}
                report["variants"][variant] = {key: result[key] for key in sorted(result)}
                self.stop_api()
                print(json.dumps({variant: {k: v["pass"] for k, v in report["variants"][variant].items()}}),
                      flush=True)
        finally:
            self.stop_api()
            self.h.stop_all()
        (self.h.output / "baseline.json").write_text(json.dumps(report, ensure_ascii=False, indent=2),
                                                     encoding="utf-8")
        return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--variant", action="append", choices=sorted(VARIANTS))
    parser.add_argument("--summary", type=Path, help="把同一份结果另写到这里（只有数字、状态码与通过与否）")
    args = parser.parse_args()
    report = Baseline().run(args.variant or sorted(VARIANTS))
    if args.summary:
        args.summary.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
