#!/usr/bin/env python3
"""world-lab 第 7 步冒烟：健康、world 路由、401/404、CEO 建 Company、天枢读回。
用法：python3 smoke.py <base_url> <out目录>   （凭证只从 out/*.token 读，不打印）"""
import json, sys, urllib.request, urllib.error, uuid
from pathlib import Path

base, out = sys.argv[1].rstrip("/"), Path(sys.argv[2])
ids = json.loads((out / "ids.json").read_text())
ceo = (out / "ceo.token").read_text().strip()
agent = (out / "tianshu-coagent.token").read_text().strip()
ZERO = "00000000-0000-0000-0000-000000000000"

def call(method, path, body=None, token=None):
    req = urllib.request.Request(base + path, method=method, data=json.dumps(body).encode() if body is not None else None)
    req.add_header("Content-Type", "application/json")
    if token: req.add_header("Authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(req, timeout=30) as r: return r.status, json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e:
        raw = e.read()
        try: return e.code, json.loads(raw)
        except Exception: return e.code, raw.decode(errors="replace")

def check(name, ok, detail=""):
    print(("PASS " if ok else "FAIL ") + name + ("  " + detail if detail else ""))
    if not ok: sys.exit(1)

s, h = call("GET", "/v1/health"); check("health", s == 200 and h.get("ok") is True and h.get("db") is True, f"{s} {h}")
s, o = call("GET", "/openapi.json"); world = [p for p in o["paths"] if "world" in p]
check("openapi world routes", s == 200 and len(world) >= 5, f"version={o['info']['version']} {world}")
s, _ = call("GET", f"/v1/world/objects/{ZERO}"); check("no token -> 401", s == 401, str(s))
s, _ = call("GET", f"/v1/world/objects/{ZERO}", token=agent); check("agent token, missing object -> 404", s == 404, str(s))

body = {"action_type": "world_create_object", "contract_version": "tkos.world/0.1", "target": None, "expected_versions": [],
        "idempotency_key": "smoke-" + uuid.uuid4().hex, "reason": "world-lab 冒烟：建一个 Company",
        "params": {"domain_id": ids["domains"]["company"], "object_type": "Company",
                   # 一个 scope 只有一个 Company，冒烟建的就是这个 scope 的 Company：起真实的名字，内容留给 CEO 之后用 world_revise_object 补。
                   "payload": {"title": "词元云集（TokenKing）",
                               "blocks": {"identity": {"text": "world-lab 联调实例的公司对象，由冒烟脚本以 CEO 凭证创建；身份与约束待 CEO 修订补入。"}}}}}
s, prep = call("POST", "/v1/actions/prepare", body, token=ceo); check("ceo prepare", s == 200, f"{s} {str(prep)[:300]}")
body["expected_versions"] = prep.get("expected_versions", [])
s, receipt = call("POST", "/v1/actions", body, token=ceo)
marker = out / "smoke-company.json"
if s == 409 and "exactly one Company" in str(receipt):
    # 一个 scope 只有一个 Company：冒烟重跑时用上次记下的那个。
    check("scope already has its Company (recorded by an earlier smoke run)", marker.exists(), "" if marker.exists() else "no out/smoke-company.json")
    oid = json.loads(marker.read_text())["object_id"]; body["params"]["payload"]["title"] = None
else:
    check("ceo create Company committed", s in (200, 201) and receipt.get("status") == "committed", f"{s} {str(receipt)[:400]}")
    oid = (receipt.get("result") or {}).get("object_id")
    check("receipt carries result.object_id", bool(oid), str(list(receipt.keys())))
    marker.write_text(json.dumps({"object_id": oid, "title": body["params"]["payload"]["title"]}, ensure_ascii=False))
s, view = call("GET", f"/v1/world/objects/{oid}", token=agent)
check("agent reads the Company", s == 200 and view.get("object_type") == "Company" and (body["params"]["payload"]["title"] is None or view.get("title") == body["params"]["payload"]["title"]), f"{s} {str(view)[:300]}")
s, ev = call("GET", f"/v1/world/objects/{oid}/events", token=agent); check("agent reads events", s == 200, str(s))
s, st = call("GET", f"/v1/world/objects/{oid}/state", token=agent); check("agent reads state", s == 200, str(s))
print("SMOKE_OK object_id=" + oid)
