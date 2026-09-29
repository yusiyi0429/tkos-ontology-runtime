"""对照实验 B 的 HTTP 传输：一条线就是一个已供给的 scope（目录里有 ids.json 与每个主体的 <键>.token）。

只用标准库：实例上在本机仓库检出里经域名跑（同 deploy/world-02/seed_eo.py），隔离库上同一段代码打本机起的 API。
凭证只从 <目录>/<主体键>.token 读，不打印、不写进日志。每个动作 prepare 再 commit；提交前把请求体记进调用者给的
pending（随运行日志落盘）：中断后重跑原样重发，已提交的拿回原回执；重发被拒（4xx）就丢掉它重新 prepare；
服务端错误（5xx）与连不上抛 TransportError，记下的请求留着，稍后重跑。
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import urllib.error
import urllib.request

V02 = "tkos.world/0.2"


class TransportError(RuntimeError):
    """服务端错误或连不上：已记下的请求留着，重跑即原样重发。"""


def utc_text(value: str) -> str:
    """UTC 规范文本 `YYYY-MM-DDTHH:MM:SS[.ffffff]Z`（同服务端的写法）。"""
    moment = datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
    text = moment.strftime("%Y-%m-%dT%H:%M:%S")
    return text + (f".{moment.microsecond:06d}" if moment.microsecond else "") + "Z"


def error_code(status: int, data) -> str:
    """拒绝的错误码：服务的 error.code；没有时（例如请求体校验）记 HTTP_<状态码>。"""
    if isinstance(data, dict) and isinstance(data.get("error"), dict) and data["error"].get("code"):
        return str(data["error"]["code"])
    return f"HTTP_{status}"


class Line:
    """一个已供给的 scope：base_url 加目录（ids.json 与凭证文件）。"""

    def __init__(self, base_url: str, out: Path):
        self.base, self.out = base_url.rstrip("/"), Path(out)
        self.ids = json.loads((self.out / "ids.json").read_text(encoding="utf-8"))
        self._tokens: dict[str, str] = {}

    @property
    def scope_id(self) -> str:
        return self.ids["scope_id"]

    def principal(self, key: str) -> str:
        return self.ids["principals"][key]["principal_id"]

    def principal_type(self, key: str) -> str:
        return self.ids["principals"][key]["type"]

    def domain(self, key: str) -> str:
        return self.ids["domains"][key]

    def call(self, method: str, path: str, body=None, who: str | None = None):
        request = urllib.request.Request(self.base + path, method=method,
                                         data=json.dumps(body).encode() if body is not None else None)
        request.add_header("Content-Type", "application/json")
        if who is not None:
            if who not in self._tokens:
                self._tokens[who] = (self.out / f"{who}.token").read_text(encoding="utf-8").strip()
            request.add_header("Authorization", "Bearer " + self._tokens[who])
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                return response.status, json.loads(response.read() or b"null")
        except urllib.error.HTTPError as exc:
            raw = exc.read()
            try:
                return exc.code, json.loads(raw)
            except ValueError:
                return exc.code, raw.decode(errors="replace")
        except (urllib.error.URLError, OSError) as exc:
            raise TransportError(f"连不上 {self.base}（{type(exc).__name__}）：重跑即可，已记下的请求会原样重发") from None

    def get(self, path: str, who: str):
        status, data = self.call("GET", path, who=who)
        if status != 200:
            raise TransportError(f"读 {path}：{status} {str(data)[:300]}")
        return data

    def view(self, object_id: str, who: str) -> dict:
        return self.get(f"/v1/world/objects/{object_id}", who)

    def events(self, object_id: str, who: str) -> list[dict]:
        return self.get(f"/v1/world/objects/{object_id}/events", who)["events"]

    def target(self, object_id: str, who: str) -> dict:
        """当前最新修订作为动作目标：0.2 读投影把修订 id 与对象行的并发版本放在 business 组里。"""
        business = self.view(object_id, who)["business"]
        return {"object_id": object_id, "revision_id": business["revision_id"],
                "expected_version": business["object_version"]}

    def ref(self, object_id: str, who: str) -> str:
        """对象形式的引用，钉到当前最新版本。"""
        return f"{object_id}@{self.view(object_id, who)['business']['version']}"


def committed(status: int, receipt) -> bool:
    return (status in (200, 201) and isinstance(receipt, dict) and receipt.get("status") == "committed"
            and (receipt.get("result") or {}).get("contract_version") == V02)


def submit(line: Line, pending: dict, save, key: str, who: str, build) -> dict:
    """prepare 再 commit 一个动作，返回结果：committed 为真时带 receipt；被拒时带 stage（prepare 或 commit）、
    status 与 error_code。build() 给出请求体（idempotency_key 由这里写成 key）。被拒是正常结果，不抛异常；
    5xx 与连不上抛 TransportError。

    提交了的请求留在 pending 里，由调用者在把结果记进运行日志之后清掉：中途断了再跑，同一个键原样重发、拿回原回执，
    不会因为对象的并发版本已经前进而重建出另一个请求体（那样会是 IDEMPOTENCY_CONFLICT）。被拒的请求从 pending 删掉。"""
    held = pending.get(key)
    if held is not None:
        if held["who"] != who:
            raise TransportError(f"{key} 上次由 {held['who']} 提交时中断，这次换成了 {who}：运行日志与脚本对不上")
        status, receipt = line.call("POST", "/v1/actions", held["body"], who)
        if committed(status, receipt):
            return {"committed": True, "receipt": receipt, "resent": True}
        if status >= 500:
            raise TransportError(f"重发 {key}：服务端 {status}，稍后重跑（记下的请求留着）")
        del pending[key]
        save()
    body = build()
    body["idempotency_key"] = key
    status, prepared = line.call("POST", "/v1/actions/prepare", body, who)
    if status >= 500:
        raise TransportError(f"prepare {body['action_type']}（{key}）：服务端 {status}")
    if status != 200:
        return {"committed": False, "stage": "prepare", "status": status, "error_code": error_code(status, prepared)}
    body["expected_versions"] = prepared.get("expected_versions", [])
    pending[key] = {"who": who, "body": body}
    save()
    status, receipt = line.call("POST", "/v1/actions", body, who)
    if status >= 500:
        raise TransportError(f"commit {body['action_type']}（{key}）：服务端 {status}，稍后重跑（记下的请求留着）")
    if not committed(status, receipt):
        del pending[key]
        save()
        return {"committed": False, "stage": "commit", "status": status, "error_code": error_code(status, receipt)}
    return {"committed": True, "receipt": receipt}
