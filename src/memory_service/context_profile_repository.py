"""Repository and six-tier section builder for Working Memory Context Profiles.

All SQL and read-only ``working`` calls live here.  Narrative rendering and orchestration live in
separate modules.  Scoped lookups never disclose another tenant, Signal ordering follows stable
object identity, and shared section dependencies are queried once.  Current and latest-confirmed
markers derive from one version-chain snapshot.  View content is deep-copied into read-only maps so
callers cannot mutate shared state.
"""
from __future__ import annotations

import hashlib
from types import MappingProxyType
from typing import Any

from psycopg.rows import dict_row

from memory_service import working
from memory_service.context_profile_contracts import (
    AgreementSection,
    ChainScopeError,
    ContextProfileError,
    CloseSection,
    IssueSection,
    JudgmentSection,
    MissionSection,
    PartyView,
    SignalSection,
    SourceRefView,
    SourceResolutionAudit,
    VersionView,
    iso_or_none,
)

_TIER_TYPES = ("Issue", "Judgment")  # 需要完整版本链的档位


# ---------------------------------------------------------------------------
# 链 scope
# ---------------------------------------------------------------------------


def load_chain_scoped(conn, chain_id, tenant_id: str, organization_id: str) -> dict:
    """按 chain_id + tenant + org 取链；不存在与 scope 不匹配统一泛化报错。

    错误信息刻意不区分两种情况、不携带链行真实 tenant/org：跨租户调用方
    无法借错误信息探测链是否存在于他域。
    """
    chain = working.get_chain_scoped(
        conn, chain_id=chain_id, tenant_id=tenant_id, organization_id=organization_id
    )
    if chain is None:
        raise ChainScopeError(
            f"议题链不存在或不属于请求声明的 tenant/organization（chain_id={chain_id}）"
        )
    return chain


# ---------------------------------------------------------------------------
# 来源引用解析（每版本、每引用；零来源合法）
# ---------------------------------------------------------------------------


def resolve_source_refs(conn, record_ids) -> dict[str, tuple[SourceRefView, ...]]:
    """Read frozen source snapshots without depending on distill-owned tables."""
    ids = [str(r) for r in record_ids]
    if not ids:
        return {}
    rows = conn.execute(
        """SELECT r.record_id, r.ordinal, r.fragment_id, r.excerpt_snapshot,
                  r.content_hash_snapshot, r.source_locator_snapshot
           FROM wm_version_source_refs r
           WHERE r.record_id = ANY(%s::uuid[])
           ORDER BY r.record_id, r.ordinal""",
        (ids,),
    ).fetchall()
    out: dict[str, list[SourceRefView]] = {}
    for record_id, ordinal, fragment_id, excerpt_snapshot, content_hash_snapshot, locator_data in rows:
        locator_data = locator_data or {}
        document_id = locator_data.get("document_id")
        heading_path = locator_data.get("heading_path")
        page_no = locator_data.get("page_no")
        paragraph_pos = locator_data.get("paragraph_pos")
        chunk_index = locator_data.get("chunk_index")
        fragment_ordinal = locator_data.get("fragment_ordinal")
        filename = locator_data.get("filename")
        locator_parts: list[str] = []
        if heading_path is not None:
            locator_parts.append(f"「{heading_path}」")
        if page_no is not None:
            locator_parts.append(f"第{page_no}页")
        if paragraph_pos is not None:
            locator_parts.append(f"第{paragraph_pos}段")
        actual_hash = hashlib.sha256((excerpt_snapshot or "").encode("utf-8")).hexdigest()
        if actual_hash == content_hash_snapshot:
            resolution, note = "resolved", "冻结摘录与 SHA-256 快照一致"
        else:
            resolution, note = "hash_and_excerpt_mismatch", "冻结摘录与 SHA-256 快照不一致"
        out.setdefault(str(record_id), []).append(
            SourceRefView(
                ordinal=ordinal,
                fragment_id=str(fragment_id),
                document_id=str(document_id) if document_id is not None else None,
                filename=filename,
                locator="".join(locator_parts),
                chunk_index=chunk_index,
                fragment_ordinal=fragment_ordinal,
                excerpt_snapshot=excerpt_snapshot,
                content_hash_snapshot=content_hash_snapshot,
                resolution=resolution,
                resolution_note=note,
            )
        )
    return {k: tuple(v) for k, v in out.items()}


def _deep_freeze(value: Any) -> Any:
    """把 jsonb content 递归转为不可变：dict→MappingProxyType、list/tuple→tuple。

    只冻结顶层不够（嵌套 dict/list 仍可变）——深冻结后视图持有者无法改到共享状态。
    """
    if isinstance(value, dict):
        return MappingProxyType({k: _deep_freeze(v) for k, v in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_deep_freeze(v) for v in value)
    return value


def _version_views(conn, rows: list[dict]) -> tuple[VersionView, ...]:
    """把版本行 dict 转成 VersionView（含批量来源解析 + content 只读深冻结）。"""
    refs = resolve_source_refs(conn, [r["record_id"] for r in rows])
    views: list[VersionView] = []
    for r in rows:
        views.append(
            VersionView(
                record_id=str(r["record_id"]),
                object_id=str(r["object_id"]),
                object_type=r["object_type"],
                version=r["version"],
                supersedes=str(r["supersedes"]) if r["supersedes"] is not None else None,
                confirmation_status=r["confirmation_status"],
                confirmed_by=str(r["confirmed_by"]) if r["confirmed_by"] is not None else None,
                confirmed_at=iso_or_none(r["confirmed_at"]),
                created_by=str(r["created_by"]),
                issue_state=r["issue_state"],
                confirmed_judgment_record_id=(
                    str(r["confirmed_judgment_record_id"])
                    if r["confirmed_judgment_record_id"] is not None else None
                ),
                agreement_record_id=(
                    str(r["agreement_record_id"]) if r["agreement_record_id"] is not None else None
                ),
                # Deeply immutable snapshot; view holders cannot mutate shared state.
                content=_deep_freeze(r["content"] or {}),
                source_refs=refs.get(str(r["record_id"]), ()),
            )
        )
    return tuple(views)


def latest_confirmed_id(versions: tuple[VersionView, ...]) -> str | None:
    """从同一版本链快照派生最新 confirmed record_id（升序取最后一个 confirmed）。"""
    for v in reversed(versions):
        if v.confirmation_status == "confirmed":
            return v.record_id
    return None


# ---------------------------------------------------------------------------
# 仓储：一次连接内共享的依赖图查询
# ---------------------------------------------------------------------------


class ContextProfileRepository:
    """Read-only repository for the six profile tiers.

    Issue and Judgment version chains are read once.  Current and latest-confirmed markers derive
    from those snapshots.  Agreement, Mission, and Close current rows are cached individually.
    The facade calls ``build_tier`` in canonical order.
    """

    def __init__(self, conn, chain_id):
        self._conn = conn
        self._chain_id = chain_id
        self._object_ids: dict[str, str | None] = {}
        self._version_chains: dict[str, tuple[VersionView, ...]] = {}
        self._single_current: dict[str, VersionView | None] = {}
        self._version_row_cache: dict[str, dict] = {}

    # -- 基础对象身份 ------------------------------------------------------

    def _chain_object(self, object_type: str) -> dict | None:
        with self._conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                "SELECT * FROM wm_objects WHERE chain_id=%s AND object_type=%s LIMIT 2",
                (self._chain_id, object_type),
            )
            rows = cur.fetchall()
        if len(rows) > 1:
            raise ContextProfileError(
                f"链 {self._chain_id} 存在多个 {object_type} 对象，无法构建确定性投影"
            )
        return dict(rows[0]) if rows else None

    def _object_id(self, object_type: str) -> str | None:
        if object_type not in self._object_ids:
            obj = self._chain_object(object_type)
            self._object_ids[object_type] = str(obj["object_id"]) if obj is not None else None
        return self._object_ids[object_type]

    def _current_version_row(self, object_id: str) -> dict | None:
        return working.get_current_version(self._conn, object_id)

    def _version_row(self, record_id) -> dict | None:
        key = str(record_id)
        if key not in self._version_row_cache:
            self._version_row_cache[key] = working.get_version(self._conn, record_id)
        return self._version_row_cache[key]

    # -- 共享子结果（单一快照） ---------------------------------------------

    def _version_chain(self, object_type: str) -> tuple[VersionView, ...]:
        """给定类型的完整版本链视图（单次查询、一次快照；Issue/Judgment 复用）。

        current 与 latest-confirmed 都从这一份视图派生，
        不再调 get_current_version / get_latest_confirmed_version。
        """
        if object_type not in self._version_chains:
            oid = self._object_id(object_type)
            rows = working.get_version_chain(self._conn, oid) if oid else []
            self._version_chains[object_type] = _version_views(self._conn, rows)
        return self._version_chains[object_type]

    def issue_versions(self) -> tuple[VersionView, ...]:
        """链上 Issue 的完整版本链视图（Issue/Close/Mission 档共享；空=无 Issue）。"""
        return self._version_chain("Issue")

    def issue_current_view(self) -> VersionView | None:
        vs = self.issue_versions()
        return vs[-1] if vs else None

    def judgment_section(self) -> JudgmentSection:
        """Judgment 档：完整版本链 + 最新 confirmed 标记（单一快照派生）。"""
        vs = self._version_chain("Judgment")
        if not vs:
            return JudgmentSection()
        return JudgmentSection(
            judgment_object_id=vs[0].object_id,
            versions=vs,
            latest_confirmed_record_id=latest_confirmed_id(vs),
        )

    def _single_current_view(self, object_type: str) -> VersionView | None:
        """链上给定类型（无版本链需求）的当前版视图，单行查询并缓存。"""
        if object_type not in self._single_current:
            oid = self._object_id(object_type)
            row = self._current_version_row(oid) if oid else None
            self._single_current[object_type] = (
                _version_views(self._conn, [row])[0] if row else None
            )
        return self._single_current[object_type]

    def agreement_current_view(self) -> VersionView | None:
        return self._single_current_view("Agreement")

    def _agreement_version(self, agreement_record_id) -> int | None:
        if not agreement_record_id:
            return None
        row = self._version_row(agreement_record_id)
        return row["version"] if row else None

    def _agreement_statement(self, agreement_record_id) -> str | None:
        if not agreement_record_id:
            return None
        row = self._version_row(agreement_record_id)
        if row is None:
            return None
        statement = (row["content"] or {}).get("statement")
        return statement if isinstance(statement, str) and statement.strip() else None

    def _parties_for(self, agreement_record_id: str) -> tuple[PartyView, ...]:
        party_rows = self._conn.execute(
            """SELECT p.party, u.display_name
               FROM wm_agreement_parties p JOIN users u ON u.user_id = p.party
               WHERE p.agreement_record_id=%s
               ORDER BY p.added_at, p.party""",
            (agreement_record_id,),
        ).fetchall()
        conf_rows = self._conn.execute(
            """SELECT confirmer, confirmed_at, note FROM wm_agreement_confirmations
               WHERE agreement_record_id=%s ORDER BY confirmed_at, confirmer""",
            (agreement_record_id,),
        ).fetchall()
        conf_by_party = {str(r[0]): r for r in conf_rows}
        views: list[PartyView] = []
        for party, display_name in party_rows:
            c = conf_by_party.get(str(party))
            views.append(
                PartyView(
                    party_id=str(party),
                    display_name=display_name,
                    confirmed_at=iso_or_none(c[1]) if c is not None else None,
                    confirmation_note=(c[2] if c is not None else None),
                )
            )
        return tuple(views)

    # -- Current Signal versions in stable object order ---------------------------

    def _signal_current_rows(self) -> list[dict]:
        """同链全部 Signal 的当前版，按 wm_objects.created_at, object_id 升序。

        DISTINCT ON (o.object_id) 取每对象最大版本；排序键取稳定对象身份的
        created_at（wm_objects），不是版本行 created_at——Signal 修订会推进版本
        时间戳，但不得改变它在链内的展示顺序。
        """
        with self._conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """SELECT DISTINCT ON (o.object_id) v.*, o.created_at AS _object_created_at
                   FROM wm_object_versions v JOIN wm_objects o ON o.object_id = v.object_id
                   WHERE o.chain_id = %s AND o.object_type = 'Signal'
                   ORDER BY o.object_id, v.version DESC""",
                (self._chain_id,),
            )
            rows = cur.fetchall()
        dicts = [dict(r) for r in rows]
        dicts.sort(key=lambda d: (d["_object_created_at"], d["object_id"]))
        return dicts

    # -- Six fixed retrieval tiers -------------------------------------------

    def build_tier(self, object_type: str):
        """按对象类型构建对应 section；返回 (section, 是否链上存在)。"""
        if object_type == "Signal":
            rows = self._signal_current_rows()
            views = _version_views(self._conn, rows)
            return SignalSection(signals=views), bool(views)
        if object_type == "Issue":
            vs = self.issue_versions()
            if not vs:
                return IssueSection(), False
            current = vs[-1]
            return (
                IssueSection(
                    issue_object_id=vs[0].object_id,
                    versions=vs,
                    current_record_id=current.record_id,
                    current_state=current.issue_state,
                    latest_confirmed_record_id=latest_confirmed_id(vs),
                ),
                True,
            )
        if object_type == "Judgment":
            sec = self.judgment_section()
            return sec, sec.judgment_object_id is not None
        if object_type == "Agreement":
            judgment = self.judgment_section()
            current = self.agreement_current_view()
            if current is None:
                return AgreementSection(judgment=judgment), False
            parties = self._parties_for(current.record_id)
            cj_rid = current.confirmed_judgment_record_id
            cj_version = None
            if cj_rid:
                jrow = self._version_row(cj_rid)
                cj_version = jrow["version"] if jrow else None
            complete = bool(parties) and all(p.confirmed_at is not None for p in parties)
            return (
                AgreementSection(
                    judgment=judgment,
                    agreement_object_id=current.object_id,
                    current=current,
                    confirmed_judgment_record_id=cj_rid,
                    confirmed_judgment_version=cj_version,
                    confirmation_complete=complete,
                    parties=parties,
                ),
                True,
            )
        if object_type == "Strategic Mission":
            current = self._single_current_view("Strategic Mission")
            if current is None:
                return MissionSection(), False
            issue_current = self.issue_current_view()
            arid = current.agreement_record_id
            return (
                MissionSection(
                    mission_object_id=current.object_id,
                    current=current,
                    issue_current=issue_current,
                    agreement_record_id=arid,
                    agreement_version=self._agreement_version(arid),
                    agreement_statement=self._agreement_statement(arid),
                ),
                True,
            )
        if object_type == "Close":
            current = self._single_current_view("Close")
            if current is None:
                return CloseSection(), False
            reason = (current.content or {}).get("reason")
            issue_current = self.issue_current_view()
            arid = current.agreement_record_id
            return (
                CloseSection(
                    close_object_id=current.object_id,
                    current=current,
                    reason=reason if isinstance(reason, str) else None,
                    issue_current=issue_current,
                    agreement_record_id=arid,
                    agreement_version=self._agreement_version(arid),
                    agreement_statement=self._agreement_statement(arid),
                ),
                True,
            )
        raise working.WorkingMemoryError(f"未实现的档位：{object_type}")  # pragma: no cover


# ---------------------------------------------------------------------------
# Source-resolution audit with deduplication
# ---------------------------------------------------------------------------


def build_source_resolution_audit(sections: list[Any]) -> SourceResolutionAudit:
    """聚合各 section 的来源解析结论；同一 (record_id, fragment_id, ordinal) 只计一次。"""
    seen: set[tuple[str, str, int]] = set()
    resolved = mismatched = missing = 0
    details: list[tuple[str, str, str]] = []

    def _visit(v: VersionView) -> None:
        nonlocal resolved, mismatched, missing
        for ref in v.source_refs:
            key = (v.record_id, ref.fragment_id, ref.ordinal)
            if key in seen:
                continue
            seen.add(key)
            details.append((v.record_id, ref.fragment_id, ref.resolution))
            if ref.resolution == "resolved":
                resolved += 1
            else:
                mismatched += 1

    for sec in sections:
        if sec is None:
            continue
        if isinstance(sec, SignalSection):
            for v in sec.signals:
                _visit(v)
        elif isinstance(sec, IssueSection):
            for v in sec.versions:
                _visit(v)
        elif isinstance(sec, JudgmentSection):
            for v in sec.versions:
                _visit(v)
        elif isinstance(sec, AgreementSection):
            if sec.current is not None:
                _visit(sec.current)
            for v in sec.judgment.versions:
                _visit(v)
        elif isinstance(sec, MissionSection):
            if sec.current is not None:
                _visit(sec.current)
            if sec.issue_current is not None:
                _visit(sec.issue_current)
        elif isinstance(sec, CloseSection):
            if sec.current is not None:
                _visit(sec.current)
            if sec.issue_current is not None:
                _visit(sec.issue_current)
    return SourceResolutionAudit(
        refs_checked=len(details),
        resolved=resolved,
        mismatched=mismatched,
        missing=missing,
        details=tuple(details),
    )
