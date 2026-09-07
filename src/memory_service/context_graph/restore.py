"""graph lifecycle 快照恢复（MVP：核心链路打通 + 模块解耦）。

- ``clear_graph_data``：FK 安全顺序清除 generation 图数据（不删治理台账）。
  硬门禁：目标代 status 必须为 'retired'，防止误删 current 代真实数据。
- ``restore_from_snapshot``：把已验证快照 payload 写入目标 generation（单事务）。
  幂等：先清图数据再写全量。SHA-256 精确往返验证通过。
- ``verify_restore_roundtrip``：临时 schema 内真实恢复 + 再导出 + SHA-256 比对账本。
"""
from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, ContextManager

from psycopg import IsolationLevel
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from memory_service.context_graph.snapshot import (
    FORMAT_V1,
    SnapshotError,
    SnapshotObjectStore,
    canonical_json_bytes,
    generate_snapshot,
    readback_snapshot,
)


class RestoreError(SnapshotError):
    """恢复过程错误。"""


@dataclass(frozen=True)
class RestoreResult:
    generation_id: str
    entities_written: int
    relations_written: int
    entity_refs_written: int
    relation_refs_written: int
    proposals_written: int
    audits_written: int


def clear_graph_data(
    conn, *, generation_id: str, tenant_id: str, organization_id: str,
) -> dict[str, int]:
    """FK 安全顺序清除 generation 图数据并返回删除计数。不删治理台账。

    硬门禁：目标代 status 必须为 'retired'。status='current' 时拒绝执行，
    防止误删用户真实数据（误删无回滚路径）。
    """
    row = conn.execute(
        "SELECT status FROM context_graph_versions "
        "WHERE generation_id=%(gen)s AND tenant_id=%(t)s AND organization_id=%(o)s",
        {"gen": generation_id, "t": tenant_id, "o": organization_id},
    ).fetchone()
    if row is None:
        raise RestoreError(f"generation {generation_id} not found")
    if row[0] != "retired":
        raise RestoreError(
            f"clear_graph_data requires retired generation (got status={row[0]!r}); "
            f"refusing to clear current/shadow generation to prevent data loss"
        )
    scope = {"gen": generation_id, "t": tenant_id, "o": organization_id}
    conn.execute(
        "DELETE FROM semantic_relation_source_refs "
        "WHERE graph_generation_id=%(gen)s AND tenant_id=%(t)s AND organization_id=%(o)s", scope)
    conn.execute(
        "DELETE FROM semantic_entity_source_refs "
        "WHERE graph_generation_id=%(gen)s AND tenant_id=%(t)s AND organization_id=%(o)s", scope)
    relations = conn.execute(
        "DELETE FROM semantic_relations "
        "WHERE graph_generation_id=%(gen)s AND tenant_id=%(t)s AND organization_id=%(o)s", scope
    ).rowcount
    entities = conn.execute(
        "DELETE FROM semantic_entities "
        "WHERE graph_generation_id=%(gen)s AND tenant_id=%(t)s AND organization_id=%(o)s", scope
    ).rowcount
    return {"deleted_entity_count": entities, "deleted_relation_count": relations}


def restore_from_snapshot(
    conn, payload: dict[str, Any], *,
    target_generation_id: str,
    tenant_id: str,
    organization_id: str,
) -> RestoreResult:
    """把已验证快照 payload 写入目标 generation（单事务，调用方管提交/回滚）。

    幂等：先 clear_graph_data（要求 retired）再 INSERT 全量。FK 安全顺序。
    audits 使用 OVERRIDING SYSTEM VALUE 保留原 audit_seq（SHA-256 精确往返必须）。
    """
    row = conn.execute(
        "SELECT 1 FROM context_graph_versions "
        "WHERE generation_id=%(gen)s AND tenant_id=%(t)s AND organization_id=%(o)s",
        {"gen": target_generation_id, "t": tenant_id, "o": organization_id},
    ).fetchone()
    if row is None:
        raise RestoreError(f"target generation {target_generation_id} not found")

    clear_graph_data(conn, generation_id=target_generation_id,
                     tenant_id=tenant_id, organization_id=organization_id)
    return _write_payload(conn, payload, target_generation_id=target_generation_id,
                          tenant_id=tenant_id, organization_id=organization_id)


def _write_payload(
    conn, payload: dict[str, Any], *,
    target_generation_id: str,
    tenant_id: str,
    organization_id: str,
) -> RestoreResult:
    """低层写入：把 payload 全量 INSERT 进目标 generation（无 gate、无 clear）。"""

    # 图数据
    entities = payload.get("entities") or []
    for entity in entities:
        conn.execute(
            """INSERT INTO semantic_entities(entity_id,tenant_id,organization_id,entity_type,
                  name,content,revision,status,created_at,updated_at,normalized_name,type_key,
                  rationale,confirmed_by,confirmed_at,graph_generation_id,strategic_level,
                  strategic_period,outcome_level,status_scope,org_subtype,is_moat)
               VALUES (%(entity_id)s,%(tenant_id)s,%(organization_id)s,%(entity_type)s,
                  %(name)s,%(content)s,%(revision)s,%(status)s,%(created_at)s,%(updated_at)s,
                  %(normalized_name)s,%(type_key)s,%(rationale)s,%(confirmed_by)s,
                  %(confirmed_at)s,%(graph_generation_id)s,%(strategic_level)s,
                  %(strategic_period)s,%(outcome_level)s,%(status_scope)s,%(org_subtype)s,
                  %(is_moat)s)""",
            {**entity, "graph_generation_id": target_generation_id,
             "content": Jsonb(entity.get("content"))})

    relations = payload.get("relations") or []
    for relation in relations:
        conn.execute(
            """INSERT INTO semantic_relations(relation_id,tenant_id,organization_id,source_id,
                  target_id,relation_type,content,revision,status,created_at,rationale,
                  confirmed_by,confirmed_at,graph_generation_id,updated_at)
               VALUES (%(relation_id)s,%(tenant_id)s,%(organization_id)s,%(source_id)s,
                  %(target_id)s,%(relation_type)s,%(content)s,%(revision)s,%(status)s,
                  %(created_at)s,%(rationale)s,%(confirmed_by)s,%(confirmed_at)s,
                  %(graph_generation_id)s,%(updated_at)s)""",
            {**relation, "graph_generation_id": target_generation_id,
             "content": Jsonb(relation.get("content"))})

    entity_refs = payload.get("entity_source_refs") or []
    for ref in entity_refs:
        conn.execute(
            """INSERT INTO semantic_entity_source_refs(entity_id,graph_generation_id,
                  tenant_id,organization_id,fragment_id,ordinal,excerpt_snapshot,
                  content_hash_snapshot,source_locator_snapshot)
               VALUES (%(entity_id)s,%(graph_generation_id)s,%(tenant_id)s,
                  %(organization_id)s,%(fragment_id)s,%(ordinal)s,%(excerpt_snapshot)s,
                  %(content_hash_snapshot)s,%(source_locator_snapshot)s)""",
            {**ref, "graph_generation_id": target_generation_id,
             "source_locator_snapshot": Jsonb(ref.get("source_locator_snapshot") or {})})

    relation_refs = payload.get("relation_source_refs") or []
    for ref in relation_refs:
        conn.execute(
            """INSERT INTO semantic_relation_source_refs(relation_id,graph_generation_id,
                  tenant_id,organization_id,fragment_id,ordinal,excerpt_snapshot,
                  content_hash_snapshot,source_locator_snapshot)
               VALUES (%(relation_id)s,%(graph_generation_id)s,%(tenant_id)s,
                  %(organization_id)s,%(fragment_id)s,%(ordinal)s,%(excerpt_snapshot)s,
                  %(content_hash_snapshot)s,%(source_locator_snapshot)s)""",
            {**ref, "graph_generation_id": target_generation_id,
             "source_locator_snapshot": Jsonb(ref.get("source_locator_snapshot") or {})})

    # 治理台账
    proposals = payload.get("proposals") or []
    for prop in proposals:
        conn.execute(
            """INSERT INTO memory_proposals(proposal_id,tenant_id,organization_id,target_kind,
                  target_id,base_revision,action,proposed_content,change_reason,evidence,source,
                  import_session_id,confidence,proposed_by,status,created_at,graph_generation_id)
               VALUES (%(proposal_id)s,%(tenant_id)s,%(organization_id)s,%(target_kind)s,
                  %(target_id)s,%(base_revision)s,%(action)s,%(proposed_content)s,
                  %(change_reason)s,%(evidence)s,%(source)s,%(import_session_id)s,
                  %(confidence)s,%(proposed_by)s,%(status)s,%(created_at)s,
                  %(graph_generation_id)s)
               ON CONFLICT (proposal_id) DO NOTHING""",
            {**prop, "graph_generation_id": target_generation_id,
             "proposed_content": Jsonb(prop.get("proposed_content")),
             "evidence": Jsonb(prop.get("evidence")),
             "source": Jsonb(prop.get("source"))})

    audits = payload.get("audits") or []
    for audit in audits:
        conn.execute(
            """INSERT INTO memory_audit(audit_id,proposal_id,target_kind,target_id,revision,
                  before_content,after_content,decision,decided_by,decision_note,decided_at,
                  audit_seq)
               OVERRIDING SYSTEM VALUE
               VALUES (%(audit_id)s,%(proposal_id)s,%(target_kind)s,%(target_id)s,
                  %(revision)s,%(before_content)s,%(after_content)s,%(decision)s,
                  %(decided_by)s,%(decision_note)s,%(decided_at)s,%(audit_seq)s)
               ON CONFLICT (audit_id) DO NOTHING""",
            {**audit,
             "before_content": Jsonb(audit.get("before_content")),
             "after_content": Jsonb(audit.get("after_content"))})

    return RestoreResult(
        generation_id=target_generation_id,
        entities_written=len(entities),
        relations_written=len(relations),
        entity_refs_written=len(entity_refs),
        relation_refs_written=len(relation_refs),
        proposals_written=len(proposals),
        audits_written=len(audits),
    )


def verify_restore_roundtrip(
    db_connect: Callable[..., ContextManager[Any]],
    object_store: SnapshotObjectStore,
    *,
    migrations_dir: Path,
    snapshot_id: str,
    tenant_id: str | None = None,
    organization_id: str | None = None,
) -> bool:
    """在临时 schema 内真实恢复 + 再导出 + SHA-256 精确比对账本哈希。

    通过返回 True；失败抛 RestoreError（含原因）。临时 schema 创建后必清理。
    不写入 recovery_drill_* 账本字段（MVP 不需要演练仪式）。
    不用 session_replication_role=replica：恢复前把 payload 引用的 user_id
    预填入临时 schema 的 users 表（ON CONFLICT DO NOTHING），FK 保持开启。
    """
    # 回读 + 账本
    with db_connect() as conn:
        payload = readback_snapshot(
            conn, object_store, snapshot_id=snapshot_id,
            tenant_id=tenant_id, organization_id=organization_id)
    with db_connect() as conn:
        ledger = conn.execute(
            "SELECT snapshot_sha256 FROM context_graph_snapshots WHERE snapshot_id=%s",
            (snapshot_id,)).fetchone()
    if ledger is None:
        raise RestoreError("snapshot ledger entry not found")
    ledger_sha256 = ledger[0]

    generation = payload["generation"]
    gen_id = str(generation["generation_id"])
    scope_tenant = str(generation["tenant_id"])
    scope_org = str(generation["organization_id"])
    schema_name = f"cg_verify_{uuid.uuid4().hex[:12]}"

    try:
        # 建临时 schema + 跑迁移（migrations_dir 由宿主显式传入，host capability boundary）
        with db_connect(autocommit=True) as conn:
            conn.execute(f"CREATE SCHEMA {schema_name}")
            conn.execute(f"SET search_path TO {schema_name}, public")
            for path in sorted(migrations_dir.glob("*.sql")):
                conn.execute(path.read_text(encoding="utf-8"))

        # 恢复（FK 开启：预填 users + strategic_periods + generation 行）
        with db_connect() as conn:
            conn.execute(f"SET search_path TO {schema_name}, public")
            conn.commit()
            _prefill_fk_targets(conn, payload, gen_id=gen_id,
                                tenant_id=scope_tenant, organization_id=scope_org)
            _write_payload(
                conn, payload, target_generation_id=gen_id,
                tenant_id=scope_tenant, organization_id=scope_org)

        # 再导出
        with db_connect() as conn:
            conn.execute(f"SET search_path TO {schema_name}, public")
            conn.commit()
            conn.isolation_level = IsolationLevel.REPEATABLE_READ
            artifact = generate_snapshot(
                conn, tenant_id=scope_tenant, organization_id=scope_org,
                generation_id=gen_id)

        roundtrip_sha256 = artifact.sha256
        if payload.get("format") == FORMAT_V1:
            # Immutable v1 objects use the pre-extraction package identifier and include
            # distill-owned sections.  The one production v1 snapshot predates source refs
            # and carries empty document sections, so its service-owned graph can still be
            # verified exactly without reintroducing a Memory Service → distill write path.
            if payload.get("documents") or payload.get("fragments"):
                raise RestoreError(
                    "legacy snapshot contains distill-owned rows; restore them through "
                    "a distill recovery boundary before verifying the Memory Service graph"
                )
            legacy_payload = dict(artifact.payload)
            legacy_payload["format"] = FORMAT_V1
            legacy_payload["documents"] = []
            legacy_payload["fragments"] = []
            for section in ("entity_source_refs", "relation_source_refs"):
                legacy_payload[section] = [
                    {key: value for key, value in ref.items()
                     if key != "source_locator_snapshot"}
                    for ref in legacy_payload[section]
                ]
            roundtrip_sha256 = hashlib.sha256(
                canonical_json_bytes(legacy_payload)
            ).hexdigest()
        if roundtrip_sha256 != ledger_sha256:
            raise RestoreError(
                f"roundtrip hash mismatch: re-export={roundtrip_sha256}, "
                f"ledger={ledger_sha256}")
        return True
    finally:
        with db_connect(autocommit=True) as conn:
            conn.execute(f"DROP SCHEMA IF EXISTS {schema_name} CASCADE")


def _prefill_fk_targets(conn, payload: dict[str, Any], *, gen_id: str,
                        tenant_id: str, organization_id: str) -> None:
    """预填临时 schema 的 FK 目标行（users / generation / strategic_periods）。

    不用 replica mode：FK 保持开启，恢复过程正常校验。
    """
    # generation 行（带全部字段，确保再导出 hash 一致）。
    # 此处必须 DO UPDATE 而不能 DO NOTHING：0005_context_graph.sql:78 会 seed
    # legacy-phase1 那一行（generation_id 00000000-...-e1），所以每个临时 schema
    # 跑完迁移后该行已存在且 created_at=now()、baseline_audit_seq=0。若 DO NOTHING，
    # payload 原值会被默默丢弃，再导出的 generation 节就和快照不一致，hash 必败。
    # 只在 verify_restore_roundtrip 的临时 schema 内调用，不会影响真库。
    generation = payload["generation"]
    conn.execute(
        """INSERT INTO context_graph_versions(generation_id,tenant_id,organization_id,
              label,status,baseline_audit_seq,created_at,retired_at)
           VALUES (%(generation_id)s,%(tenant_id)s,%(organization_id)s,%(label)s,
              %(status)s,%(baseline_audit_seq)s,%(created_at)s,%(retired_at)s)
           ON CONFLICT (generation_id) DO UPDATE SET
              tenant_id=EXCLUDED.tenant_id, organization_id=EXCLUDED.organization_id,
              label=EXCLUDED.label, status=EXCLUDED.status,
              baseline_audit_seq=EXCLUDED.baseline_audit_seq,
              created_at=EXCLUDED.created_at, retired_at=EXCLUDED.retired_at""",
        generation,
    )

    # 收集所有引用的 user_id
    user_ids: set[str] = set()
    for entity in payload.get("entities") or []:
        if entity.get("confirmed_by"):
            user_ids.add(str(entity["confirmed_by"]))
    for relation in payload.get("relations") or []:
        if relation.get("confirmed_by"):
            user_ids.add(str(relation["confirmed_by"]))
    for prop in payload.get("proposals") or []:
        if prop.get("proposed_by"):
            user_ids.add(str(prop["proposed_by"]))
    for audit in payload.get("audits") or []:
        if audit.get("decided_by"):
            user_ids.add(str(audit["decided_by"]))
    for doc in payload.get("documents") or []:
        if doc.get("uploaded_by"):
            user_ids.add(str(doc["uploaded_by"]))
    for uid in user_ids:
        conn.execute(
            "INSERT INTO users(user_id,display_name,kind) VALUES (%s,%s,'human')"
            " ON CONFLICT (user_id) DO NOTHING",
            (uid, f"restore-placeholder-{uid[:8]}"))

    # strategic_periods
    for entity in payload.get("entities") or []:
        if entity.get("strategic_period"):
            conn.execute(
                "INSERT INTO strategic_periods(tenant_id,organization_id,period_code,starts_on,ends_on)"
                " VALUES (%s,%s,%s,'2020-01-01','2030-12-31') ON CONFLICT DO NOTHING",
                (tenant_id, organization_id, entity["strategic_period"]))
    conn.commit()
