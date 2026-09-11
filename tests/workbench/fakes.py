"""无数据库的 workbench 读取聚焦测试。

运行方式（避免 tests/conftest.py 的 DATABASE_URL 夹具）：
    PYTHONPATH=src pytest tests/workbench --confcutdir=tests/workbench

FakeConn 只响应 workbench.py 里带 /*workbench:*/ 标记的查询；授权钩子
（db.object_row / db.authorize_domain / db.revision_row /
readers.authorize_receipt）由 monkeypatch 替换为内存判定，从而在无数据库
环境下验证 current policy、分页授权过滤、恶意 cursor、精确 revision 关系
和回执整体授权的行为。
"""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from memory_service_runtime.governed.db import AuthContext
from tests import legacy_protocol_fixture


def uid(value: int) -> str:
    return str(UUID(int=value))


def ts(second: int) -> datetime:
    return datetime(2026, 9, 1, 0, 0, second, tzinfo=timezone.utc)


CTX = AuthContext(scope_id=uid(1000), tenant_id="t", company_id="c",
                  principal_id=uid(1001), principal_type="human", auth_epoch=1,
                  assignments=[{"assignment_id": uid(1002), "domain_id": uid(1), "role": "CEO"}])


class Result:
    def __init__(self, rows):
        self._rows = list(rows)

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)


class FakeConn:
    """按 SQL 标记分发；实现与生产查询相同的过滤、keyset 与 LIMIT 语义。"""

    row_factory = None

    def __init__(self, *, domains=(), objects=(), revisions=(), receipts=(),
                 work_item_state=None, assignment_rows=(), revision_owners=None,
                 protocol_objects=None):
        self._domains = [dict(row) for row in domains]
        self._objects = [dict(row) for row in objects]
        self._revisions = [dict(row) for row in revisions]
        self._receipts = [dict(row) for row in receipts]
        self._work_item_state = work_item_state
        self._assignment_rows = {str(row["assignment_id"]): dict(row) for row in assignment_rows}
        self._revision_owners = dict(revision_owners or {})
        # Objects explicitly registered under the frozen legacy protocol rows.
        # Defaults to the listed objects; tests with mocked heads/targets must
        # name their known fixture objects here.  Unknown objects stay
        # unregistered and are never read as legacy by default.
        self._protocol_objects = (
            {str(oid) for oid in protocol_objects} if protocol_objects is not None
            else {str(row["object_id"]) for row in self._objects})

    def execute(self, sql, params=()):
        # A2 routing may inspect a known header before the ordinary legacy
        # reader runs. This fixture returns only explicitly supplied heads;
        # it does not manufacture protocol membership or grant any rights.
        if " ".join(sql.replace("/*a3:head*/", "").split()) == "SELECT * FROM gov_objects WHERE scope_id=%s AND object_id=%s":
            return Result([row for row in self._objects
                           if params[0] == CTX.scope_id and str(row['object_id']) == str(params[1])])
        if "/*workbench:domain-check*/" in sql:
            return Result([row for row in self._domains if str(row["domain_id"]) == str(params[1])])
        if "/*workbench:domains*/" in sql:
            rows = sorted(self._domains, key=lambda row: str(row["domain_id"]))
            if "domain_id > %s" in sql:
                rows = [row for row in rows if str(row["domain_id"]) > params[1]]
            return Result(rows[: params[-1]])
        if "/*workbench:objects*/" in sql:
            domain_id, index = params[1], 2
            rows = [row for row in self._objects if str(row["domain_id"]) == domain_id]
            if "AND o.object_type=%s" in sql:
                rows = [row for row in rows if row["object_type"] == params[index]]
                index += 1
            if "(o.created_at, o.object_id) > (%s, %s)" in sql:
                key = (params[index], params[index + 1])
                index += 2
                rows = [row for row in rows
                        if (row["created_at"], str(row["object_id"])) > (key[0], str(key[1]))]
            rows = sorted(rows, key=lambda row: (row["created_at"], str(row["object_id"])))
            titles = {str(rev["revision_id"]): rev.get("payload", {}).get("title")
                      for rev in self._revisions}
            for row in rows:
                row["title"] = titles.get(str(row["latest_revision_id"]))
            return Result(rows[: params[-1]])
        if "/*workbench:revisions*/" in sql:
            object_id = params[1]
            rows = [row for row in self._revisions if str(row["object_id"]) == object_id]
            if "(recorded_at, revision_id) > (%s, %s)" in sql:
                key = (params[2], params[3])
                rows = [row for row in rows
                        if (row["recorded_at"], str(row["revision_id"])) > (key[0], str(key[1]))]
            rows = sorted(rows, key=lambda row: (row["recorded_at"], str(row["revision_id"])))
            return Result(rows[: params[-1]])
        if "/*workbench:action-receipts*/" in sql:
            object_id = params[1]

            def related(row):
                if str(row.get("target_object_id")) == object_id:
                    return True
                if any(str(item.get("object_id")) == object_id for item in row["object_versions"]):
                    return True
                return object_id in [str(item) for item in row["result"].get("referenced_object_ids", [])]

            rows = [row for row in self._receipts if related(row)]
            if "(recorded_at, receipt_id) > (%s, %s)" in sql:
                rows = [row for row in rows
                        if (row["recorded_at"], str(row["receipt_id"])) > (params[4], params[5])]
            rows = sorted(rows, key=lambda row: (row["recorded_at"], str(row["receipt_id"])))
            return Result(rows[: params[-1]])
        if "/*workbench:revision-owner*/" in sql:
            owner = self._revision_owners.get(str(params[1]))
            return Result([{"object_id": owner}] if owner else [])
        if "/*workbench:responsibility*/" in sql:
            return Result([self._work_item_state] if self._work_item_state else [])
        if "/*workbench:assignment*/" in sql:
            row = self._assignment_rows.get(str(params[1]))
            return Result([row] if row else [])
        # A1 protocol registration reads (single + batched binding, installed
        # profile, support registry): only objects explicitly present in this
        # fake are registered; unknown objects stay unregistered, never legacy.
        # The expected scope is the fixture-fixed CTX scope, never params[0]
        # itself (that would be self-proving).
        protocol_rows = legacy_protocol_fixture.answer_query(
            " ".join(sql.split()), params, CTX.scope_id, self._protocol_objects)
        if protocol_rows is not None:
            return Result(protocol_rows)
        raise AssertionError(f"unhandled SQL: {sql[:120]}")


def domain_row(number: int, name: str) -> dict:
    return {"domain_id": uid(number), "name": name}


def object_row(number: int, domain: int, *, second: int = 0, object_type: str = "FeedbackThread",
               latest: int | None = None, effective: int | None = None) -> dict:
    return {"object_id": uid(number), "domain_id": uid(domain), "object_type": object_type,
            "lifecycle_status": "open", "object_version": 1,
            "latest_revision_id": uid(latest) if latest else None,
            "effective_revision_id": uid(effective) if effective else None,
            "created_at": ts(second)}


def revision_row(number: int, obj: int, *, second: int = 0, payload=None) -> dict:
    return {"revision_id": uid(number), "object_id": uid(obj), "object_version": 1,
            "payload": payload or {}, "payload_hash": "a" * 64,
            "recorded_at": ts(second), "valid_from": ts(0), "valid_to": None}
