"""P1/P2 Working Memory projections for the Clark GraphKnowledge shape.

The code index is deliberately rebuilt for every request.  It is scoped by the
caller-provided tenant/org and uses only public ``memory_service.working`` read
functions; no WM/semantic/memory table SQL belongs here.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from memory_service import working

from adapter import codes
from adapter.contracts import GkEntity, GkEntitySummary, GkVersion


EntityKind = Literal["ISS", "JDG", "SGN", "AGR"]


@dataclass(frozen=True, slots=True)
class EntityTarget:
    """One stateless code-index entry."""

    code: str
    kind: EntityKind
    chain_id: str | None = None
    object_id: str | None = None
    issue_id: str | None = None
    label: str = ""


@dataclass(frozen=True, slots=True)
class CodeIndex:
    """Immutable per-request index; no process-level mapping is retained."""

    entries: tuple[EntityTarget, ...]

    def find(self, code: str) -> EntityTarget | None:
        return next((entry for entry in self.entries if entry.code == code), None)


def _ordered_chains(conn, *, tenant_id: str, organization_id: str) -> list[dict]:
    chains = working.list_chains(
        conn, tenant_id=tenant_id, organization_id=organization_id,
    )
    # list_chains orders by created_at only.  Add chain_id as a deterministic
    # tie-breaker because PostgreSQL now() is transaction-scoped.
    return sorted(chains, key=lambda chain: (chain["created_at"], chain["chain_id"]))


def _current_objects(
    conn,
    chains: list[dict],
    object_type: str,
) -> list[tuple[dict, dict]]:
    """Return (current_version, object) pairs for every scope object of a type."""
    result: list[tuple[dict, dict]] = []
    for chain in chains:
        current_versions = working.list_chain_current_versions_by_type(
            conn, chain["chain_id"], object_type,
        )
        for current in current_versions:
            obj = working.get_object(conn, current["object_id"])
            if obj is None:
                # An index entry cannot safely point at a missing object.  Use
                # the canonical WM read error rather than silently returning 404.
                raise working.ObjectNotFoundError(
                    f"对象不存在：{current['object_id']}"
                )
            result.append((current, obj))
    return result


def build_code_index(
    conn,
    *,
    tenant_id: str,
    organization_id: str,
) -> CodeIndex:
    """Build the deterministic, full-scope ISS/JDG/SGN/AGR index for one request."""
    chains = _ordered_chains(
        conn, tenant_id=tenant_id, organization_id=organization_id,
    )
    entries: list[EntityTarget] = []
    taken_slugs: set[str] = set()
    # ISS is allocated from the chain title, with stable collision suffixes.
    for chain in chains:
        slug = codes.dedupe(codes.slug_of(chain["title"]), taken_slugs)
        taken_slugs.add(slug)
        issue_versions = working.list_chain_current_versions_by_type(
            conn, chain["chain_id"], "Issue",
        )
        issue_object_id = issue_versions[0]["object_id"] if issue_versions else None
        issue_target = EntityTarget(
            code=f"ISS-{slug}",
            kind="ISS",
            chain_id=chain["chain_id"],
            object_id=issue_object_id,
            issue_id=issue_object_id,
            label=chain["title"],
        )
        entries.append(issue_target)

    # JDG/SGN/AGR ordinals are global within the scope, never chain-local.
    for object_type, kind, code_factory in (
        ("Judgment", "JDG", codes.judgment_code),
        ("Signal", "SGN", codes.signal_code),
        ("Agreement", "AGR", codes.agreement_code),
    ):
        objects = _current_objects(conn, chains, object_type)
        objects.sort(key=lambda pair: (pair[1]["created_at"], pair[1]["object_id"]))
        for ordinal, (_current, obj) in enumerate(objects, start=1):
            entries.append(
                EntityTarget(
                    code=code_factory(ordinal),
                    kind=kind,
                    chain_id=str(obj["chain_id"]),
                    object_id=str(obj["object_id"]),
                    issue_id=str(obj["issue_id"]) if obj.get("issue_id") else None,
                )
            )

    return CodeIndex(entries=tuple(entries))


def _content(version: dict | None) -> dict:
    value = (version or {}).get("content")
    return value if isinstance(value, dict) else {}


def _current_version(conn, object_id: str | None) -> dict | None:
    if object_id is None:
        return None
    return working.get_current_version(conn, object_id)


def _issue_judgment_codes(
    issue_target: EntityTarget,
    judgment_targets: tuple[EntityTarget, ...],
) -> list[str]:
    """Map an Issue to all of its Judgment objects in the same scope."""
    issue_id = issue_target.object_id
    if issue_id is None:
        return []
    return sorted(
        target.code
        for target in judgment_targets
        if target.issue_id == issue_id
    )


def _judgment_issue_codes(
    judgment_target: EntityTarget,
    issue_targets: tuple[EntityTarget, ...],
) -> list[str]:
    """Map a Judgment back to the Issues that shake it —— ``shakes`` 的反向。

    索引里已经有这条边了：建 JDG 条目时 ``issue_id`` 就抄下来了（``build_code_index``），
    ``_issue_judgment_codes`` 走的是同一条边的正向。缺的只是判断这一侧没往外发。

    clark 的配方在判断卡上读 ``shakenBy``，渲染成「相邻动摇」（``recipes.ts``）。它读不到
    时是 ``?? []`` —— **不报错，那一行直接不显示**。所以少发这一个的表现不是出错，是
    装配出来的上下文薄了一截，而屏幕上和正常跑完长得一模一样。
    """
    issue_id = judgment_target.issue_id
    if issue_id is None:
        return []
    return sorted(
        target.code for target in issue_targets if target.object_id == issue_id
    )


def _signal_issue_codes(
    conn,
    signal_target: EntityTarget,
    issue_targets: tuple[EntityTarget, ...],
) -> list[str]:
    """Reverse-scan Issue formation through canonical list_issue_signals()."""
    signal_id = signal_target.object_id
    if signal_id is None:
        return []
    spawned: list[str] = []
    for issue_target in issue_targets:
        issue_id = issue_target.object_id
        if issue_id is None:
            continue
        signal_ids = working.list_issue_signals(conn, issue_id)
        if signal_id in signal_ids:
            spawned.append(issue_target.code)
    return sorted(spawned)


def _issue_signal_codes(
    conn,
    issue_target: EntityTarget,
    signal_targets: tuple[EntityTarget, ...],
) -> list[str]:
    """Map an Issue back to the Signals that formed it —— ``spawns`` 的反向。"""
    issue_id = issue_target.object_id
    if issue_id is None:
        return []
    signal_ids = set(working.list_issue_signals(conn, issue_id))
    by_object = {target.object_id: target.code for target in signal_targets}
    return sorted(code for oid, code in by_object.items() if oid in signal_ids)


def _issue_agreement_codes(
    conn,
    issue_target: EntityTarget,
    agreement_targets: tuple[EntityTarget, ...],
) -> list[str]:
    """Map an Issue to its **confirmed** Agreements —— ``resolvedBy`` 的真源。

    未确认的 Agreement 只是提案，不是「既有共识」，不进入 resolvedBy（但它仍可
    经 AGR 短码 P1/P2 读取，状态如实展示为 unconfirmed）。
    """
    issue_id = issue_target.object_id
    if issue_id is None:
        return []
    resolved: list[str] = []
    for target in agreement_targets:
        if target.issue_id != issue_id:
            continue
        current = _current_version(conn, target.object_id)
        if current is not None and current.get("confirmation_status") == "confirmed":
            resolved.append(target.code)
    return sorted(resolved)


def _project_signal(
    conn,
    target: EntityTarget,
    current: dict,
    issue_targets: tuple[EntityTarget, ...],
) -> GkEntity:
    content = _content(current)
    title = content.get("title", "")
    description = content.get("description", "")
    return GkEntity(
        code=target.code,
        label=title,
        properties={"title": title, "why": description},
        relations={"spawns": _signal_issue_codes(conn, target, issue_targets)},
    )


def _project_issue(
    conn,
    target: EntityTarget,
    current: dict | None,
    judgment_targets: tuple[EntityTarget, ...],
    signal_targets: tuple[EntityTarget, ...],
    agreement_targets: tuple[EntityTarget, ...],
) -> GkEntity:
    if current is None:
        return GkEntity(
            code=target.code,
            label=target.label,
            properties=None,
            relations=None,
        )
    content = _content(current)
    key_question = content.get("key_question", "")
    state = current.get("issue_state") or ""
    monitoring_note = content.get("monitoringNote", content.get("monitoring_note", ""))
    judgment_codes = _issue_judgment_codes(target, judgment_targets)
    agreement_codes = _issue_agreement_codes(conn, target, agreement_targets)
    signal_codes = _issue_signal_codes(conn, target, signal_targets)
    return GkEntity(
        code=target.code,
        label=key_question,
        properties={
            "key_question": key_question,
            "state": state,
            "why": content.get("rationale", ""),
            "monitoringNote": monitoring_note,
        },
        relations={
            "shakes": judgment_codes,
            "hasJudgement": judgment_codes,
            "resolvedBy": agreement_codes,
            "spawnsOf": signal_codes,
        },
    )


def _project_judgment(
    target: EntityTarget,
    current: dict | None,
    issue_targets: tuple[EntityTarget, ...],
) -> GkEntity:
    content = _content(current)
    statement = content.get("statement", "")
    return GkEntity(
        code=target.code,
        label=statement,
        properties=None,
        relations={"shakenBy": _judgment_issue_codes(target, issue_targets)},
    )


def _project_agreement(target: EntityTarget, current: dict | None) -> GkEntity:
    content = _content(current)
    statement = content.get("statement", "")
    status = (current or {}).get("confirmation_status") or ""
    return GkEntity(
        code=target.code,
        label=statement,
        properties={
            "agreementStatus": status,
            "text": statement,
            "boundary": content.get("boundary", ""),
        },
    )


def _iso_at(version: dict) -> str | None:
    value = version.get("confirmed_at") or version.get("created_at")
    if value is None:
        return None
    return value.isoformat() if hasattr(value, "isoformat") else str(value)


def _project_versions(conn, object_id: str) -> list[GkVersion]:
    versions = working.get_version_chain(conn, object_id)
    return [
        GkVersion(
            index=version.get("version"),
            status=version.get("confirmation_status"),
            at=_iso_at(version),
            statement=_content(version).get("statement"),
            meetingLabel=None,
        )
        for version in versions
    ]


def list_issue_summaries(
    conn,
    *,
    tenant_id: str,
    organization_id: str,
) -> list[GkEntitySummary]:
    """List scoped ISS anchors without inventing a semantic similarity score."""
    index = build_code_index(
        conn, tenant_id=tenant_id, organization_id=organization_id,
    )
    summaries: list[GkEntitySummary] = []
    for target in index.entries:
        if target.kind != "ISS":
            continue
        current = _current_version(conn, target.object_id)
        label = _content(current).get("key_question", "") if current else target.label
        summaries.append(GkEntitySummary(code=target.code, label=str(label)))
    return summaries


def project_entity(
    conn,
    code: str,
    *,
    view: Literal["latest", "chain"] = "latest",
    tenant_id: str,
    organization_id: str,
) -> GkEntity:
    """Resolve and project one P1/P2 entity for the requested scope."""
    index = build_code_index(
        conn, tenant_id=tenant_id, organization_id=organization_id,
    )
    info = codes.parse(code)
    target = index.find(info.code)
    if target is None or target.kind != info.kind:
        raise working.ObjectNotFoundError(f"实体不存在：{code}")
    issue_targets = tuple(entry for entry in index.entries if entry.kind == "ISS")
    judgment_targets = tuple(entry for entry in index.entries if entry.kind == "JDG")
    signal_targets = tuple(entry for entry in index.entries if entry.kind == "SGN")
    agreement_targets = tuple(entry for entry in index.entries if entry.kind == "AGR")
    current = _current_version(conn, target.object_id)

    if target.kind == "SGN":
        entity = _project_signal(conn, target, current or {}, issue_targets)
    elif target.kind == "JDG":
        entity = _project_judgment(target, current, issue_targets)
    elif target.kind == "AGR":
        entity = _project_agreement(target, current)
    else:
        entity = _project_issue(
            conn, target, current, judgment_targets, signal_targets, agreement_targets,
        )

    if view == "chain":
        entity.versions = _project_versions(conn, target.object_id) if target.object_id else []
    return entity


__all__ = (
    "CodeIndex",
    "EntityTarget",
    "build_code_index",
    "list_issue_summaries",
    "project_entity",
)
