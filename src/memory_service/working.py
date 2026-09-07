"""Working Memory governed writes on a caller-owned transaction.

Enforces typed version transitions, scoped human confirmation, and frozen source integrity.
"""
from __future__ import annotations

import hashlib
import re
import unicodedata
import uuid
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

from memory_service.actors import (
    ActorRequiredError,
    HumanActorRequiredError,
    require_actor,
    require_human,
)
from memory_service.working_contracts import (
    AGREEMENT_DISPOSITIONS as _AGREEMENT_DISPOSITIONS,
    OBJECT_TYPE_ORDER,
    OBJECT_TYPES,
    missing_required_content,
)
from memory_service.working_repository import (
    _VERSION_UUID_KEYS, _dictcur, _stringify,
    get_chain, get_chain_scoped, get_current_version, get_latest_confirmed_version,
    get_object, get_version, get_version_chain, list_chains, lock_current_version,
    lock_version, resolve_chain_by_title,
    list_chain_current_versions_by_type as _list_chain_current_versions_by_type,
)

_get_object = get_object
_get_chain = get_chain
_get_version = get_version
_lock_version = lock_version
_lock_current_version = lock_current_version

# Re-export stable public constants while keeping working_contracts as their sole declaration.
AGREEMENT_DISPOSITIONS: frozenset[str] = frozenset(_AGREEMENT_DISPOSITIONS)
_SHA256_HEX_RE = re.compile(r"^[0-9a-f]{64}$")

# Required Agreement disposition for each downstream object type.
_MISSION_LIKE_DISPOSITION = {"Strategic Mission": "strategic_mission", "Close": "close"}


# 异常


class WorkingMemoryError(Exception):
    """Working Memory 服务层错误基类。"""


class NotHumanApproverError(WorkingMemoryError):
    """Confirmation and transition actors must be real human users."""


class ActorOutOfScopeError(WorkingMemoryError):
    """提案 actor 不存在、不属于目标 tenant/org scope，或 kind 不允许发起普通提案。"""


class ChainNotFoundError(WorkingMemoryError):
    pass


class ChainTitleConflictError(WorkingMemoryError):
    """A case-insensitive chain title already exists in this scope."""


class ObjectNotFoundError(WorkingMemoryError):
    pass


class VersionNotFoundError(WorkingMemoryError):
    pass


class InvalidObjectTypeError(WorkingMemoryError):
    """The referenced object or version type is invalid for the operation."""


class CrossChainReferenceError(WorkingMemoryError):
    """A referenced object belongs to a different chain."""


class FormationError(WorkingMemoryError):
    """Issue formation lacks valid Signal sources."""


class IssueStateTransitionError(WorkingMemoryError):
    """The Issue state transition is invalid or non-monotonic."""


class AlreadyConfirmedError(WorkingMemoryError):
    """目标版本行已经是 confirmed，不能重复确认（"单调"）。"""


class NotAPartyError(WorkingMemoryError):
    """The confirmer is not a party to this Agreement version."""


class AlreadyConfirmedByPartyError(WorkingMemoryError):
    """同一人对同一 Agreement 版本重复确认（PRIMARY KEY (agreement_record_id, confirmer) 兜底）。"""


class EmptyPartiesError(WorkingMemoryError):
    """An Agreement version must have at least one party."""


class InvalidDispositionError(WorkingMemoryError):
    """Agreement disposition must be strategic_mission or close."""


class NotConfirmedError(WorkingMemoryError):
    """引用的版本行尚未 confirmed，不能作为承接/确认依据。"""


class DispositionMismatchError(WorkingMemoryError):
    """Agreement disposition does not match the downstream object type."""


class SourceRefImmutableError(WorkingMemoryError):
    """confirmed 后的版本行来源引用不可再增删（需新建版本）。"""


class InvalidSourceRefError(WorkingMemoryError):
    """来源引用参数不合法（如 content_hash_snapshot 不是 sha256 十六进制格式）。"""


class SourceRefDriftError(WorkingMemoryError):
    """来源引用的摘录与哈希快照不一致，拒绝确认。"""


# 内部帮助函数


def _validate_object_content(object_type: str, content: dict[str, Any]) -> None:
    if not isinstance(content, dict):
        raise WorkingMemoryError(f"{object_type} content 必须是对象")
    missing = missing_required_content(object_type, content)
    if missing:
        raise WorkingMemoryError(f"{object_type} content 缺少非空字段：{list(missing)}")
    if object_type == "Agreement" and content["disposition"] not in AGREEMENT_DISPOSITIONS:
        raise InvalidDispositionError(
            f"disposition 必须 ∈ {sorted(AGREEMENT_DISPOSITIONS)}，实际 {content['disposition']!r}"
        )


def _require_human(
    conn, user_id: str, *, tenant_id: str, organization_id: str,
    field_name: str = "confirmed_by", lock: bool = False,
) -> None:
    try:
        require_human(
            conn,
            user_id,
            tenant_id=tenant_id,
            organization_id=organization_id,
            field_name=field_name,
            lock=lock,
        )
    except HumanActorRequiredError as exc:
        raise NotHumanApproverError(str(exc)) from exc


# 普通提案（非确认）允许的 actor kind；确认路径仍只允许 human。
_PROPOSER_KINDS = frozenset({"human", "agent_service"})


def _require_proposer(
    conn, user_id, *, tenant_id: str, organization_id: str,
    field_name: str = "created_by", lock: bool = False,
) -> None:
    """统一的 actor-in-scope 校验：普通提案允许 human / agent_service，失败即零写入。"""
    try:
        kind = require_actor(
            conn,
            str(user_id),
            tenant_id=tenant_id,
            organization_id=organization_id,
            field_name=field_name,
            lock=lock,
        )
    except ActorRequiredError as exc:
        raise ActorOutOfScopeError(str(exc)) from exc
    if kind not in _PROPOSER_KINDS:
        raise ActorOutOfScopeError(
            f"{field_name} 的 kind={kind!r} 不允许发起提案（允许 {sorted(_PROPOSER_KINDS)}）"
        )


# ---------------------------------------------------------------------------
# 写入口授权读取（scoped lookup + 事务内行锁）
#
# actor 与目标 chain/object/version 的同 tenant/org JOIN 一次完成授权：目标缺失、
# actor 缺失、actor 跨 scope 在 SQL 层同为零行，统一泛化错误，不泄露外部 chain_id、
# object_type、status、disposition 等元数据。FOR SHARE/FOR UPDATE 行锁持有至调用方
# 事务 commit，消除"校验后、写入前"的身份/目标 TOCTOU 窗口。
# ---------------------------------------------------------------------------

_TARGET_OR_ACTOR_ERROR = "目标不存在或 actor 不属于其 tenant/organization scope"


def _canonical_uuid(value) -> uuid.UUID | None:
    """Python 侧 UUID 规范化：非法输入返回 None（由调用处映射为与 missing 完全同文的
    泛化领域错误，不送 PG、不毒化调用方事务）；合法输入返回 canonical ``uuid.UUID``，
    SQL 一律绑定该值而非原始替代表示。"""
    try:
        return uuid.UUID(str(value))
    except (ValueError, AttributeError, TypeError):
        return None


def _require_canonical_uuid(value) -> uuid.UUID:
    """取【已通过 scoped 授权/校验】的值的 canonical 形式。

    授权路径已在 Python 侧拒绝非法 UUID，故此处的 None 分支理论不可达：用断言暴露
    编程错误，绝不回退为“绑定原始字符串”（raw fallback 会让 urn:uuid: 等 PG 不接受
    的表示穿透到 SQL）。
    """
    canonical = _canonical_uuid(value)
    assert canonical is not None, f"authorized UUID expected, got {value!r}"
    return canonical

_CHAIN_AUTH_SQL = """
SELECT c.*, u.kind AS actor_kind
  FROM wm_issue_chains c
  JOIN users u ON u.tenant_id = c.tenant_id AND u.organization_id = c.organization_id
 WHERE c.chain_id = %s AND u.user_id = %s
 FOR SHARE OF c, u
"""

_OBJECT_AUTH_SQL = """
SELECT o.*, c.tenant_id, c.organization_id, u.kind AS actor_kind
  FROM wm_objects o
  JOIN wm_issue_chains c ON c.chain_id = o.chain_id
  JOIN users u ON u.tenant_id = c.tenant_id AND u.organization_id = c.organization_id
 WHERE o.object_id = %s AND u.user_id = %s
 FOR SHARE OF o, c, u
"""

_VERSION_AUTH_SQL = """
SELECT v.*, o.chain_id, o.issue_id, c.tenant_id, c.organization_id, u.kind AS actor_kind
  FROM wm_object_versions v
  JOIN wm_objects o ON o.object_id = v.object_id
  JOIN wm_issue_chains c ON c.chain_id = o.chain_id
  JOIN users u ON u.tenant_id = c.tenant_id AND u.organization_id = c.organization_id
 WHERE v.record_id = %s AND u.user_id = %s
 FOR UPDATE OF v FOR SHARE OF o, c, u
"""


def _authorized_query(conn, sql: str, params: tuple, *, field_name: str, human_only: bool) -> dict:
    target_id, actor_id = params
    # 非法 UUID 在 Python 侧转稳定领域错误：不外泄 psycopg 类型错误，也避免服务端
    # 错误毒化调用方事务。actor 非法与目标非法走各自泛化文案（目标非法视同不存在）。
    # SQL 绑定 canonical uuid.UUID，不继续绑定原始替代表示。
    canonical_actor = _canonical_uuid(actor_id)
    if canonical_actor is None:
        raise ActorOutOfScopeError(f"{field_name} 不是合法的用户标识")
    canonical_target = _canonical_uuid(target_id)
    if canonical_target is None:
        raise ActorOutOfScopeError(_TARGET_OR_ACTOR_ERROR)
    try:
        with _dictcur(conn) as cur:
            cur.execute(sql, (canonical_target, canonical_actor))
            row = cur.fetchone()
    except psycopg.errors.InvalidTextRepresentation as exc:
        raise ActorOutOfScopeError(f"{field_name} 不是合法的用户标识") from exc
    if row is None:
        raise ActorOutOfScopeError(_TARGET_OR_ACTOR_ERROR)
    kind = str(row["actor_kind"])
    if human_only:
        if kind != "human":
            raise NotHumanApproverError(
                f"{field_name} 必须是真实用户（kind='human'），当前 kind={kind!r}"
            )
    elif kind not in _PROPOSER_KINDS:
        raise ActorOutOfScopeError(
            f"{field_name} 的 kind={kind!r} 不允许发起提案（允许 {sorted(_PROPOSER_KINDS)}）"
        )
    return row


def _authorized_chain(
    conn, *, chain_id, actor_id, field_name: str = "created_by", human_only: bool = False
) -> dict:
    return _authorized_query(
        conn, _CHAIN_AUTH_SQL, (str(chain_id), str(actor_id)),
        field_name=field_name, human_only=human_only,
    )


def _authorized_object(
    conn, *, object_id, actor_id, field_name: str = "created_by", human_only: bool = False
) -> dict:
    return _authorized_query(
        conn, _OBJECT_AUTH_SQL, (str(object_id), str(actor_id)),
        field_name=field_name, human_only=human_only,
    )


def _authorized_version(
    conn, *, record_id, actor_id, field_name: str = "confirmed_by", human_only: bool = True
) -> dict:
    return _authorized_query(
        conn, _VERSION_AUTH_SQL, (str(record_id), str(actor_id)),
        field_name=field_name, human_only=human_only,
    )


def _object_scope(conn, obj: dict) -> tuple[str, str]:
    chain = _get_chain(conn, obj["chain_id"])
    if chain is None:
        raise ChainNotFoundError(f"对象 {obj['object_id']} 的链不存在：{obj['chain_id']}")
    return chain["tenant_id"], chain["organization_id"]


def _validate_issue_ref(conn, *, chain_id, issue_id) -> None:
    """Require an Issue identity in the given chain (chain_id constrained in SQL).

    非法 UUID 在 Python 侧预校验：与不存在同为 ObjectNotFoundError 泛化文案，不送 PG。
    跨链/跨 scope 与不存在同为零行：泛化错误，不泄露外部对象的任何元数据。
    FOR SHARE 锁持有至调用方事务结束，消除“校验后、挂载前”的目标 TOCTOU 窗口。
    """
    canonical_issue_id = _canonical_uuid(issue_id)
    canonical_chain_id = _canonical_uuid(chain_id)
    if canonical_issue_id is None or canonical_chain_id is None:
        raise ObjectNotFoundError("issue_id 指向的对象不存在或不在本链")
    row = conn.execute(
        "SELECT object_type FROM wm_objects WHERE object_id=%s AND chain_id=%s FOR SHARE",
        (canonical_issue_id, canonical_chain_id),
    ).fetchone()
    if row is None:
        raise ObjectNotFoundError("issue_id 指向的对象不存在或不在本链")
    if row[0] != "Issue":
        raise InvalidObjectTypeError(
            f"issue_id 必须指向 Issue 身份行，实际 object_type={row[0]!r}"
        )


def _assert_source_refs_fresh(conn, record_id) -> None:
    """确认前验证服务自有的冻结摘录与 SHA-256 快照互相一致。"""
    rows = conn.execute(
        """SELECT fragment_id, excerpt_snapshot, content_hash_snapshot
           FROM wm_version_source_refs WHERE record_id = %s""",
        (record_id,),
    ).fetchall()
    for fragment_id, excerpt, snapshot_hash in rows:
        if hashlib.sha256(excerpt.encode("utf-8")).hexdigest() != snapshot_hash:
            raise SourceRefDriftError(
                f"来源引用 fragment_id={fragment_id} 的摘录与哈希快照不一致，拒绝确认"
            )


def _validate_confirmed_agreement_ref(
    conn, agreement_record_id, expected_disposition: str, chain_id, expected_issue_id=None,
) -> dict:
    """Require a confirmed Agreement in the given chain (SQL-scoped) with the expected disposition.

    非法 UUID 在 Python 侧预校验：与不存在同为 VersionNotFoundError 泛化文案，不送 PG。
    chain_id 在 SQL 层约束：跨链/跨 scope 与不存在同为零行，泛化错误不泄露外部元数据。
    FOR SHARE OF v, o 锁持有至调用方事务结束，消除“校验后、承接前”的 TOCTOU 窗口。
    """
    canonical_record_id = _canonical_uuid(agreement_record_id)
    canonical_chain_id = _canonical_uuid(chain_id)
    if canonical_record_id is None or canonical_chain_id is None:
        raise VersionNotFoundError("agreement_record_id 指向的版本行不存在或不在本链")
    with _dictcur(conn) as cur:
        cur.execute(
            """SELECT v.*, o.issue_id AS obj_issue_id
                 FROM wm_object_versions v
                 JOIN wm_objects o ON o.object_id = v.object_id
                WHERE v.record_id=%s AND o.chain_id=%s
                FOR SHARE OF v, o""",
            (canonical_record_id, canonical_chain_id),
        )
        row = _stringify(cur.fetchone(), _VERSION_UUID_KEYS + ("obj_issue_id",))
    if row is None:
        raise VersionNotFoundError("agreement_record_id 指向的版本行不存在或不在本链")
    if row["object_type"] != "Agreement":
        raise InvalidObjectTypeError(
            f"agreement_record_id 必须指向 Agreement 版本行，实际 object_type={row['object_type']!r}"
        )
    if expected_issue_id is not None and row["obj_issue_id"] != str(
        _canonical_uuid(expected_issue_id) or expected_issue_id
    ):
        raise CrossChainReferenceError(
            f"Agreement 版本 {agreement_record_id} 针对的 Issue 为 {row['obj_issue_id']}，"
            f"与承接方声明的 Issue {expected_issue_id} 不一致"
        )
    if row["confirmation_status"] != "confirmed":
        raise NotConfirmedError(
            f"Agreement 版本 {agreement_record_id} 尚未 confirmed，不能被承接/引用"
        )
    disposition = (row["content"] or {}).get("disposition")
    if disposition != expected_disposition:
        raise DispositionMismatchError(
            f"Agreement 版本 {agreement_record_id} 的 disposition={disposition!r}，"
            f"与承接方要求的 {expected_disposition!r} 不一致"
        )
    return row


def _validate_confirmed_judgment_ref(conn, judgment_record_id, *, chain_id, issue_id) -> dict:
    """Require a confirmed Judgment version in the given chain (SQL-scoped) and Issue.

    非法 UUID 在 Python 侧预校验：与不存在同为 VersionNotFoundError 泛化文案，不送 PG；
    FOR SHARE OF v, o 锁持有至调用方事务结束（TOCTOU 防护）。
    """
    canonical_record_id = _canonical_uuid(judgment_record_id)
    canonical_chain_id = _canonical_uuid(chain_id)
    if canonical_record_id is None or canonical_chain_id is None:
        raise VersionNotFoundError("confirmed_judgment_record_id 指向的版本行不存在或不在本链")
    with _dictcur(conn) as cur:
        cur.execute(
            """SELECT v.*, o.issue_id AS obj_issue_id
                 FROM wm_object_versions v
                 JOIN wm_objects o ON o.object_id = v.object_id
                WHERE v.record_id=%s AND o.chain_id=%s
                FOR SHARE OF v, o""",
            (canonical_record_id, canonical_chain_id),
        )
        row = _stringify(cur.fetchone(), _VERSION_UUID_KEYS + ("obj_issue_id",))
    if row is None:
        raise VersionNotFoundError("confirmed_judgment_record_id 指向的版本行不存在或不在本链")
    if row["object_type"] != "Judgment":
        raise InvalidObjectTypeError(
            f"confirmed_judgment_record_id 必须指向 Judgment 版本行，实际 {row['object_type']!r}"
        )
    if row["obj_issue_id"] != str(_canonical_uuid(issue_id) or issue_id):
        raise CrossChainReferenceError(
            f"Judgment 版本 {judgment_record_id} 针对的 Issue 为 {row['obj_issue_id']}，"
            f"与声明的 Issue {issue_id} 不一致"
        )
    if row["confirmation_status"] != "confirmed":
        raise NotConfirmedError(f"Judgment 版本 {judgment_record_id} 尚未 confirmed，不能被 Agreement 引用")
    return row


def _create_object_with_first_version(
    conn,
    *,
    chain_id,
    object_type: str,
    created_by,
    content: dict[str, Any],
    issue_id=None,
    issue_state: str | None = None,
    confirmed_judgment_record_id=None,
    agreement_record_id=None,
) -> dict:
    """Insert one stable object and its first version without updating either row.

    A Python-generated UUID lets an Issue self-anchor ``issue_id`` in the initial INSERT.
    """
    if object_type not in OBJECT_TYPES:
        raise InvalidObjectTypeError(f"非法 object_type：{object_type!r}，必须 ∈ {sorted(OBJECT_TYPES)}")
    _validate_object_content(object_type, content)

    object_id = uuid.uuid4()
    if object_type == "Signal":
        real_issue_id = None
    elif object_type == "Issue":
        real_issue_id = object_id
    else:
        if not issue_id:
            raise InvalidObjectTypeError(f"{object_type} 必须提供 issue_id（挂载的稳定 Issue）")
        _validate_issue_ref(conn, chain_id=chain_id, issue_id=issue_id)
        real_issue_id = issue_id

    # 写入绑定 canonical UUID（上游已完成授权/校验，严格 helper 无 raw fallback）。
    canonical_chain_id = _require_canonical_uuid(chain_id)
    canonical_created_by = _require_canonical_uuid(created_by)
    canonical_issue_id = _require_canonical_uuid(real_issue_id) if real_issue_id is not None else None
    canonical_judgment_ref = (
        _require_canonical_uuid(confirmed_judgment_record_id)
        if confirmed_judgment_record_id is not None else None
    )
    canonical_agreement_ref = (
        _require_canonical_uuid(agreement_record_id) if agreement_record_id is not None else None
    )

    conn.execute(
        """INSERT INTO wm_objects(object_id, chain_id, object_type, issue_id, created_by)
           VALUES (%s,%s,%s,%s,%s)""",
        (object_id, canonical_chain_id, object_type, canonical_issue_id, canonical_created_by),
    )

    record_id = uuid.uuid4()
    conn.execute(
        """INSERT INTO wm_object_versions
             (record_id, object_id, object_type, version, supersedes, issue_state,
              confirmation_status, content, confirmed_judgment_record_id, agreement_record_id,
              created_by)
           VALUES (%s,%s,%s,1,NULL,%s,'unconfirmed',%s,%s,%s,%s)""",
        (
            record_id, object_id, object_type, issue_state, Jsonb(content),
            canonical_judgment_ref, canonical_agreement_ref, canonical_created_by,
        ),
    )
    return {
        "object_id": str(object_id),
        "chain_id": str(chain_id),
        "object_type": object_type,
        "issue_id": str(real_issue_id) if real_issue_id else None,
        "record_id": str(record_id),
        "version": 1,
        "supersedes": None,
        "issue_state": issue_state,
        "confirmation_status": "unconfirmed",
        "content": content,
    }


# 建链 + 首 Signal（全局不变量）


def _validate_chain_title(title: str) -> str:
    """Normalize a chain title and reject characters that break inline-reference syntax."""
    if not isinstance(title, str):
        raise WorkingMemoryError(f"链 title 必须是字符串，实际类型 {type(title).__name__}")
    # 先在【原始 title】上拒绝任何位置的换行/控制字符，再 strip：
    # 若先 strip 会静默吃掉 'A\n'/'\tA' 这类首尾控制字符，违背"任何原始换行/控制字符都拒绝"契约。
    # Zl(U+2028 行分隔)/Zp(U+2029 段分隔) 同属换行语义，一并拒绝。
    bad_chars = sorted({ch for ch in title if unicodedata.category(ch) in ("Cc", "Zl", "Zp")})
    if bad_chars:
        raise WorkingMemoryError(f"链 title 不能包含换行/控制字符：{bad_chars!r}")
    cleaned = title.strip()
    if not cleaned:
        raise WorkingMemoryError("链 title 不能为空（strip 后为空）")
    for marker in ("{{", "}}"):
        if marker in cleaned:
            raise WorkingMemoryError(
                f"链 title 不能包含模板控制符 {marker}：该链将无法通过 {{{{对象类型:主线名}}}} 显式引用"
            )
    return cleaned


def create_chain_with_first_signal(
    conn,
    *,
    title: str,
    signal_content: dict[str, Any],
    created_by,
    tenant_id: str = "local",
    organization_id: str = "local-org",
) -> dict:
    """Insert a chain and its first Signal atomically in the caller-owned transaction."""
    _validate_object_content("Signal", signal_content)
    title = _validate_chain_title(title)
    # actor 校验先于任何写入（scoped SQL + FOR SHARE）：失败时本函数不产生部分记录。
    _require_proposer(
        conn, created_by, tenant_id=tenant_id, organization_id=organization_id, lock=True
    )
    # 授权通过即说明 created_by 已被 Python 规范：后续写入一律绑定 canonical 值。
    created_by = _require_canonical_uuid(created_by)

    try:
        chain_row = conn.execute(
            """INSERT INTO wm_issue_chains(tenant_id, organization_id, title, created_by)
               VALUES (%s,%s,%s,%s)
               RETURNING chain_id, status, created_at""",
            (tenant_id, organization_id, title, created_by),
        ).fetchone()
    except psycopg.errors.UniqueViolation as e:
        raise ChainTitleConflictError(
            f"同 tenant+org 下主线名（大小写不敏感）已存在：{title!r}（uq_wm_chains_title）"
        ) from e

    chain_id, status, created_at = chain_row
    signal = _create_object_with_first_version(
        conn, chain_id=chain_id, object_type="Signal", created_by=created_by, content=signal_content,
    )
    return {
        "chain_id": str(chain_id),
        "tenant_id": tenant_id,
        "organization_id": organization_id,
        "title": title,
        "status": status,
        "created_at": created_at,
        "signal": signal,
    }


def create_signal(conn, *, chain_id, content: dict[str, Any], created_by) -> dict:
    """Append one independent, stable Signal object to an existing chain."""
    # actor 与目标链的同 scope JOIN 授权读取（FOR SHARE）：缺失/跨 scope 泛化错误。
    _authorized_chain(conn, chain_id=chain_id, actor_id=created_by)
    # 授权后统一 canonical：后续 INSERT 不绑定原始替代表示。
    chain_id = _require_canonical_uuid(chain_id)
    created_by = _require_canonical_uuid(created_by)
    return _create_object_with_first_version(
        conn, chain_id=chain_id, object_type="Signal", created_by=created_by, content=content,
    )


# 通用版本 CAS 追加（旧版本行业务列永不 UPDATE）


def append_version(
    conn,
    object_id,
    *,
    content: dict[str, Any],
    created_by,
    issue_state: str | None = None,
    confirmed_judgment_record_id=None,
    agreement_record_id=None,
) -> dict:
    """Append one CAS version after locking the current version.

    State changes use dedicated operations; compatible Judgment and Agreement refs are revalidated.
    """
    obj = _authorized_object(conn, object_id=object_id, actor_id=created_by)
    # 授权后统一 canonical（严格 helper，无 raw fallback）：后续版本 INSERT / parties 复制
    # INSERT 不绑定原始替代表示。
    object_id = _require_canonical_uuid(object_id)
    created_by = _require_canonical_uuid(created_by)
    object_type = obj["object_type"]
    _validate_object_content(object_type, content)
    current = _lock_current_version(conn, object_id)
    if current is None:
        raise VersionNotFoundError(f"对象 {object_id} 没有任何版本行（数据不一致）")

    if object_type == "Issue":
        if issue_state is None:
            issue_state = current["issue_state"]
        elif issue_state != current["issue_state"]:
            raise IssueStateTransitionError(
                "Issue 的 issue_state 变更必须经 advance_issue_to_strategic/confirm_close，"
                "不能通过 append_version 直接改变（写字段白名单）"
            )
    elif issue_state is not None:
        raise InvalidObjectTypeError(f"{object_type} 版本行不允许填 issue_state（仅 Issue 可填）")

    if object_type == "Agreement":
        if confirmed_judgment_record_id is None:
            # 继承值来自 DB 行（_stringify 后的 canonical 字符串），可直接绑定。
            confirmed_judgment_record_id = current["confirmed_judgment_record_id"]
        else:
            # 显式传入先 canonicalize 再比较/校验：非法 UUID 与不存在/跨链同文泛化；
            # urn/无连字符等表示与当前值等价时不触发多余的重校验，INSERT 绑定 uuid.UUID。
            canonical_ref = _canonical_uuid(confirmed_judgment_record_id)
            if canonical_ref is None:
                raise VersionNotFoundError("confirmed_judgment_record_id 指向的版本行不存在或不在本链")
            if str(canonical_ref) != str(current["confirmed_judgment_record_id"]):
                _validate_confirmed_judgment_ref(
                    conn, canonical_ref, chain_id=obj["chain_id"], issue_id=obj["issue_id"],
                )
            confirmed_judgment_record_id = canonical_ref
    elif confirmed_judgment_record_id is not None:
        raise InvalidObjectTypeError(
            f"{object_type} 版本行不允许填 confirmed_judgment_record_id（仅 Agreement 可填）"
        )

    if object_type in ("Strategic Mission", "Close"):
        if agreement_record_id is None:
            # 继承值来自 DB 行，可直接绑定。
            agreement_record_id = current["agreement_record_id"]
        else:
            canonical_ref = _canonical_uuid(agreement_record_id)
            if canonical_ref is None:
                raise VersionNotFoundError("agreement_record_id 指向的版本行不存在或不在本链")
            if str(canonical_ref) != str(current["agreement_record_id"]):
                _validate_confirmed_agreement_ref(
                    conn, canonical_ref, _MISSION_LIKE_DISPOSITION[object_type], obj["chain_id"],
                    expected_issue_id=obj["issue_id"],
                )
            agreement_record_id = canonical_ref
    elif agreement_record_id is not None:
        raise InvalidObjectTypeError(
            f"{object_type} 版本行不允许填 agreement_record_id（仅 Strategic Mission/Close 可填）"
        )

    prior_parties: list = []
    if object_type == "Agreement":
        # Validate before INSERT so corrupt historical data cannot create an unconfirmable version.
        # append_version does not change membership, so it copies the previous parties.
        prior_parties = [
            r[0]
            for r in conn.execute(
                "SELECT party FROM wm_agreement_parties WHERE agreement_record_id=%s", (current["record_id"],),
            ).fetchall()
        ]
        if not prior_parties:
            raise EmptyPartiesError(
                f"Agreement 版本 {current['record_id']} 的 parties 名单为空，无法复制到新版（数据不一致，"
                "拒绝创建一个也无法被确认的新版本）"
            )

    new_version = current["version"] + 1
    record_id = uuid.uuid4()
    conn.execute(
        """INSERT INTO wm_object_versions
             (record_id, object_id, object_type, version, supersedes, issue_state,
              confirmation_status, content, confirmed_judgment_record_id, agreement_record_id,
              created_by)
           VALUES (%s,%s,%s,%s,%s,%s,'unconfirmed',%s,%s,%s,%s)""",
        (
            record_id, object_id, object_type, new_version, current["record_id"], issue_state,
            Jsonb(content), confirmed_judgment_record_id, agreement_record_id, created_by,
        ),
    )

    for party in prior_parties:
        conn.execute(
            "INSERT INTO wm_agreement_parties(agreement_record_id, party, added_by) VALUES (%s,%s,%s)",
            (record_id, party, created_by),
        )

    return {
        "object_id": str(object_id),
        "object_type": object_type,
        "record_id": str(record_id),
        "version": new_version,
        "supersedes": current["record_id"],
        "issue_state": issue_state,
        "confirmation_status": "unconfirmed",
        "content": content,
    }


# Issue：formation + 状态机


def form_issue(
    conn,
    *,
    chain_id,
    signal_object_ids: list[str],
    key_question: str,
    rationale: str,
    created_by,
    extra_content: dict[str, Any] | None = None,
) -> dict:
    """Create a potential Issue and link at least one same-chain Signal."""
    if not signal_object_ids:
        raise FormationError("Issue 创建必须关联至少一条同链 Signal")
    # actor 与目标链的同 scope JOIN 授权读取（FOR SHARE）：缺失/跨 scope 泛化错误。
    _authorized_chain(conn, chain_id=chain_id, actor_id=created_by)

    seen: set[str] = set()
    canonical_chain_id = _canonical_uuid(chain_id)
    created_by = _require_canonical_uuid(created_by)
    for sid in signal_object_ids:
        canonical_sid = _canonical_uuid(sid)
        # 按 canonical 形式去重：urn/大写/无连字符形式的同一 Signal 只关联一次。
        sid_key = str(canonical_sid) if canonical_sid is not None else str(sid)
        if sid_key in seen:
            continue
        seen.add(sid_key)
        # 非法 UUID 与不存在同文；chain_id 在 SQL 层约束（跨链/跨 scope 与不存在同为
        # 零行，泛化错误）；FOR SHARE 锁持有至调用方事务结束（TOCTOU 防护）。
        # SQL 绑定 canonical uuid.UUID，不绑定原始替代表示。
        if canonical_sid is None or canonical_chain_id is None:
            raise ObjectNotFoundError("formation 来源对象不存在或不在本链")
        signal_row = conn.execute(
            "SELECT object_type FROM wm_objects WHERE object_id=%s AND chain_id=%s FOR SHARE",
            (canonical_sid, canonical_chain_id),
        ).fetchone()
        if signal_row is None:
            raise ObjectNotFoundError("formation 来源对象不存在或不在本链")
        if signal_row[0] != "Signal":
            raise FormationError(
                f"formation 来源对象 {sid} 的 object_type={signal_row[0]!r}，必须是 Signal"
            )

    content: dict[str, Any] = {**(extra_content or {}), "key_question": key_question, "rationale": rationale}
    issue = _create_object_with_first_version(
        conn, chain_id=canonical_chain_id, object_type="Issue", created_by=created_by, content=content,
        issue_state="potential",
    )
    issue_object_id = issue["object_id"]
    for sid in seen:
        conn.execute(
            """INSERT INTO wm_issue_signals(chain_id, issue_object_id, signal_object_id)
               VALUES (%s,%s,%s)""",
            (canonical_chain_id, issue_object_id, _require_canonical_uuid(sid)),
        )
    issue["signal_object_ids"] = sorted(seen)
    return issue


def list_issue_signals(conn, issue_object_id) -> list[str]:
    """Return the Signals from which an Issue was formed."""
    rows = conn.execute(
        "SELECT signal_object_id FROM wm_issue_signals WHERE issue_object_id=%s ORDER BY noted_at",
        (issue_object_id,),
    ).fetchall()
    return [str(r[0]) for r in rows]


def advance_issue_to_strategic(
    conn, issue_object_id, *, confirmed_by, transition_note: str | None = None,
) -> dict:
    """Advance a potential Issue to a human-confirmed strategic version."""
    # actor 与目标对象的同 scope JOIN 授权读取（FOR SHARE）：缺失/跨 scope 泛化错误。
    obj = _authorized_object(
        conn, object_id=issue_object_id, actor_id=confirmed_by,
        field_name="confirmed_by", human_only=True,
    )
    # 授权后统一 canonical：Issue 新版本 INSERT 不绑定原始替代表示。
    issue_object_id = _require_canonical_uuid(issue_object_id)
    confirmed_by = _require_canonical_uuid(confirmed_by)
    if obj["object_type"] != "Issue":
        raise InvalidObjectTypeError(f"{issue_object_id} 不是 Issue 身份行")
    current = _lock_current_version(conn, issue_object_id)
    if current is None:
        raise VersionNotFoundError(f"Issue {issue_object_id} 没有任何版本行")
    if current["issue_state"] != "potential":
        raise IssueStateTransitionError(
            f"只能从 potential 推进到 strategic，当前 issue_state={current['issue_state']!r}"
        )
    _assert_source_refs_fresh(conn, current["record_id"])

    new_content = dict(current["content"])
    if transition_note is not None:
        new_content["transition_note"] = transition_note
    new_version = current["version"] + 1
    record_id = uuid.uuid4()
    conn.execute(
        """INSERT INTO wm_object_versions
             (record_id, object_id, object_type, version, supersedes, issue_state,
              confirmation_status, content, created_by, confirmed_by, confirmed_at)
           VALUES (%s,%s,'Issue',%s,%s,'strategic','confirmed',%s,%s,%s,now())""",
        (record_id, issue_object_id, new_version, current["record_id"], Jsonb(new_content),
         confirmed_by, confirmed_by),
    )
    return {
        "object_id": str(issue_object_id), "object_type": "Issue", "record_id": str(record_id),
        "version": new_version, "supersedes": current["record_id"], "issue_state": "strategic",
        "confirmation_status": "confirmed", "confirmed_by": str(confirmed_by), "content": new_content,
    }


# Judgment


def create_judgment(
    conn, *, chain_id, issue_id, statement: str, responsible_party: str, created_by,
    extra_content: dict[str, Any] | None = None,
) -> dict:
    # actor 与目标链的同 scope JOIN 授权读取（FOR SHARE）：缺失/跨 scope 泛化错误。
    _authorized_chain(conn, chain_id=chain_id, actor_id=created_by)
    # 授权后统一 canonical：对象/版本 INSERT 不绑定原始替代表示。
    # issue_id 由下游 _validate_issue_ref（_create_object_with_first_version 内）做
    # Python 侧规范化与泛化错误映射，INSERT 前再严格 canonicalize。
    chain_id = _require_canonical_uuid(chain_id)
    created_by = _require_canonical_uuid(created_by)
    content = {**(extra_content or {}), "statement": statement, "responsible_party": responsible_party}
    return _create_object_with_first_version(
        conn, chain_id=chain_id, object_type="Judgment", issue_id=issue_id, created_by=created_by,
        content=content,
    )


def confirm_judgment(conn, judgment_record_id, *, confirmed_by) -> dict:
    """Judgment 属于 confirmation_required_types，unconfirmed→confirmed 单调、原位更新确认元数据。"""
    # actor 与目标版本行的同 scope JOIN 授权读取（FOR UPDATE OF v）：缺失/跨 scope 泛化错误。
    row = _authorized_version(conn, record_id=judgment_record_id, actor_id=confirmed_by)
    # 授权后统一 canonical：确认 UPDATE 不绑定原始替代表示。
    judgment_record_id = _require_canonical_uuid(judgment_record_id)
    confirmed_by = _require_canonical_uuid(confirmed_by)
    if row["object_type"] != "Judgment":
        raise InvalidObjectTypeError(f"{judgment_record_id} 不是 Judgment 版本行（object_type={row['object_type']!r}）")
    if row["confirmation_status"] == "confirmed":
        raise AlreadyConfirmedError(f"Judgment 版本 {judgment_record_id} 已经是 confirmed")
    _assert_source_refs_fresh(conn, judgment_record_id)

    conn.execute(
        """UPDATE wm_object_versions SET confirmation_status='confirmed', confirmed_by=%s, confirmed_at=now()
           WHERE record_id=%s AND confirmation_status='unconfirmed'""",
        (confirmed_by, judgment_record_id),
    )
    return {"record_id": str(judgment_record_id), "confirmation_status": "confirmed", "confirmed_by": str(confirmed_by)}


# Agreement：创建 + parties + 逐人确认


def create_agreement(
    conn,
    *,
    chain_id,
    issue_id,
    statement: str,
    disposition: str,
    confirmed_judgment_record_id,
    party_ids: list[str],
    created_by,
    added_by=None,
    extra_content: dict[str, Any] | None = None,
) -> dict:
    """Create an unconfirmed Agreement and non-empty parties in one transaction.

    ``confirmed_judgment_record_id`` must identify a confirmed Judgment in the same chain and Issue.
    """
    if disposition not in AGREEMENT_DISPOSITIONS:
        raise InvalidDispositionError(f"disposition 必须 ∈ {sorted(AGREEMENT_DISPOSITIONS)}，实际 {disposition!r}")
    if not party_ids:
        raise EmptyPartiesError("Agreement 版本创建时 parties 不能为空")
    # actor 与目标链的同 scope JOIN 授权读取（FOR SHARE OF c, u）：缺失/跨 scope 泛化错误。
    # added_by 显式给出且与 created_by 不同时，同样要求落在同一 scope（加行锁）。
    chain = _authorized_chain(conn, chain_id=chain_id, actor_id=created_by)
    # 授权后统一 canonical（严格 helper，无 raw fallback）：后续校验比较与全部 INSERT
    # 不绑定原始替代表示（urn/大写/无连字符等同一人不重复校验、不重复 INSERT）。
    chain_id = _require_canonical_uuid(chain_id)
    created_by = _require_canonical_uuid(created_by)
    if added_by is not None:
        canonical_added_by = _canonical_uuid(added_by)
        if canonical_added_by != created_by:
            _require_proposer(
                conn,
                added_by,
                tenant_id=chain["tenant_id"],
                organization_id=chain["organization_id"],
                field_name="added_by",
                lock=True,
            )
        added_by = _require_canonical_uuid(added_by)
    # issue_id / confirmed_judgment_record_id 由下游校验器做 Python 规范化与泛化错误映射
    #（非法值与 missing 同文，不送 PG），INSERT 前由 _create_object_with_first_version
    # 严格 canonicalize。
    _validate_confirmed_judgment_ref(conn, confirmed_judgment_record_id, chain_id=chain_id, issue_id=issue_id)

    # Validate every human party before INSERT so a swallowed exception cannot commit a partial object.
    seen: set[str] = set()
    for party in party_ids:
        canonical_party = _canonical_uuid(party)
        if canonical_party is not None and str(canonical_party) in seen:
            continue
        _require_human(
            conn,
            party,
            tenant_id=chain["tenant_id"],
            organization_id=chain["organization_id"],
            lock=True,
        )
        # 校验通过后按 canonical 形式去重并入库：同一人的 urn/大写/无连字符表示只 INSERT 一次。
        seen.add(str(_require_canonical_uuid(party)))

    content: dict[str, Any] = {**(extra_content or {}), "statement": statement, "disposition": disposition}
    agreement = _create_object_with_first_version(
        conn, chain_id=chain_id, object_type="Agreement", issue_id=issue_id, created_by=created_by,
        content=content, confirmed_judgment_record_id=confirmed_judgment_record_id,
    )

    if added_by is None:
        added_by = created_by
    for party in seen:
        conn.execute(
            "INSERT INTO wm_agreement_parties(agreement_record_id, party, added_by) VALUES (%s,%s,%s)",
            (agreement["record_id"], party, added_by),
        )
    agreement["party_ids"] = sorted(seen)
    return agreement


def confirm_agreement(conn, agreement_record_id, *, confirmer, note: str | None = None) -> dict:
    """Record one party confirmation and confirm the Agreement when every party has signed."""
    # actor 与目标版本行的同 scope JOIN 授权读取（FOR UPDATE OF v）：缺失/跨 scope 泛化错误。
    version_row = _authorized_version(
        conn, record_id=agreement_record_id, actor_id=confirmer, field_name="confirmer"
    )
    # 授权后统一 canonical：party 成员比对、确认 INSERT 与版本 UPDATE 不绑定原始替代表示
    #（必须在 membership 检查之前，否则 urn/大写形式会被误判为 NotAPartyError）。
    agreement_record_id = _require_canonical_uuid(agreement_record_id)
    confirmer = _require_canonical_uuid(confirmer)
    if version_row["object_type"] != "Agreement":
        raise InvalidObjectTypeError(
            f"{agreement_record_id} 不是 Agreement 版本行（object_type={version_row['object_type']!r}）"
        )
    if version_row["confirmation_status"] == "confirmed":
        raise AlreadyConfirmedError(f"Agreement 版本 {agreement_record_id} 已经是 confirmed")

    with _dictcur(conn) as cur:
        cur.execute(
            "SELECT party FROM wm_agreement_parties WHERE agreement_record_id=%s FOR UPDATE",
            (agreement_record_id,),
        )
        party_ids = {str(r["party"]) for r in cur.fetchall()}
    if not party_ids:
        # 结构上不该发生（创建时已强制非空），防御性报错而非静默通过
        raise EmptyPartiesError(f"Agreement 版本 {agreement_record_id} 的 parties 名单为空（数据不一致）")
    if str(confirmer) not in party_ids:
        raise NotAPartyError(f"{confirmer} 不在 Agreement 版本 {agreement_record_id} 的确认人名单中")

    # Validate freshness before the first write so a swallowed exception cannot commit a party
    # confirmation without the Agreement version reaching a valid state.
    _assert_source_refs_fresh(conn, agreement_record_id)

    try:
        conn.execute(
            "INSERT INTO wm_agreement_confirmations(agreement_record_id, confirmer, note) VALUES (%s,%s,%s)",
            (agreement_record_id, confirmer, note),
        )
    except psycopg.errors.UniqueViolation as e:
        raise AlreadyConfirmedByPartyError(
            f"{confirmer} 已经对 Agreement 版本 {agreement_record_id} 确认过一次"
        ) from e

    confirmed_count = conn.execute(
        "SELECT count(*) FROM wm_agreement_confirmations WHERE agreement_record_id=%s",
        (agreement_record_id,),
    ).fetchone()[0]
    is_complete = confirmed_count >= len(party_ids)
    if is_complete:
        conn.execute(
            """UPDATE wm_object_versions SET confirmation_status='confirmed', confirmed_by=%s, confirmed_at=now()
               WHERE record_id=%s AND confirmation_status='unconfirmed'""",
            (confirmer, agreement_record_id),
        )
    return {
        "agreement_record_id": str(agreement_record_id),
        "confirmer": str(confirmer),
        "confirmed_count": confirmed_count,
        "party_count": len(party_ids),
        "is_complete": is_complete,
    }


def get_agreement_parties(conn, agreement_record_id) -> list[str]:
    rows = conn.execute(
        "SELECT party FROM wm_agreement_parties WHERE agreement_record_id=%s ORDER BY added_at",
        (agreement_record_id,),
    ).fetchall()
    return [str(r[0]) for r in rows]


def get_agreement_confirmations(conn, agreement_record_id) -> list[dict]:
    with _dictcur(conn) as cur:
        cur.execute(
            "SELECT confirmer, confirmed_at, note FROM wm_agreement_confirmations "
            "WHERE agreement_record_id=%s ORDER BY confirmed_at",
            (agreement_record_id,),
        )
        rows = cur.fetchall()
    return [_stringify(r, ("confirmer",)) for r in rows]


# Strategic Mission / Close：承接 confirmed Agreement


def create_mission(
    conn, *, chain_id, issue_id, title: str, outcome: str, owner: str, boundary: str,
    agreement_record_id, created_by, extra_content: dict[str, Any] | None = None,
) -> dict:
    # actor 与目标链的同 scope JOIN 授权读取（FOR SHARE）：缺失/跨 scope 泛化错误。
    _authorized_chain(conn, chain_id=chain_id, actor_id=created_by)
    _validate_confirmed_agreement_ref(
        conn, agreement_record_id, _MISSION_LIKE_DISPOSITION["Strategic Mission"], chain_id,
        expected_issue_id=issue_id,
    )
    # 授权/校验后统一 canonical：对象/版本 INSERT 不绑定原始替代表示。
    # issue_id / agreement_record_id 已经 _validate_confirmed_agreement_ref 做 Python 规范化
    # 与泛化错误映射，INSERT 前由 _create_object_with_first_version 严格 canonicalize。
    chain_id = _require_canonical_uuid(chain_id)
    created_by = _require_canonical_uuid(created_by)
    content = {**(extra_content or {}), "title": title, "outcome": outcome, "owner": owner, "boundary": boundary}
    return _create_object_with_first_version(
        conn, chain_id=chain_id, object_type="Strategic Mission", issue_id=issue_id, created_by=created_by,
        content=content, agreement_record_id=agreement_record_id,
    )


def create_close(
    conn, *, chain_id, issue_id, reason: str, assumptions: str, monitor: str, reopen_condition: str,
    agreement_record_id, created_by, extra_content: dict[str, Any] | None = None,
) -> dict:
    """Create an unconfirmed Close without changing the Issue or chain state."""
    # actor 与目标链的同 scope JOIN 授权读取（FOR SHARE）：缺失/跨 scope 泛化错误。
    _authorized_chain(conn, chain_id=chain_id, actor_id=created_by)
    _validate_confirmed_agreement_ref(
        conn, agreement_record_id, _MISSION_LIKE_DISPOSITION["Close"], chain_id, expected_issue_id=issue_id,
    )
    # 授权/校验后统一 canonical：对象/版本 INSERT 不绑定原始替代表示。
    # issue_id / agreement_record_id 已经 _validate_confirmed_agreement_ref 做 Python 规范化
    # 与泛化错误映射，INSERT 前由 _create_object_with_first_version 严格 canonicalize。
    chain_id = _require_canonical_uuid(chain_id)
    created_by = _require_canonical_uuid(created_by)
    content = {
        **(extra_content or {}), "reason": reason, "assumptions": assumptions,
        "monitor": monitor, "reopen_condition": reopen_condition,
    }
    return _create_object_with_first_version(
        conn, chain_id=chain_id, object_type="Close", issue_id=issue_id, created_by=created_by,
        content=content, agreement_record_id=agreement_record_id,
    )


def confirm_close(conn, close_record_id, *, confirmed_by, transition_note: str | None = None) -> dict:
    """Atomically confirm Close, append a closed Issue version, and close the chain.

    All three writes share the caller's transaction, so no committed state can contain a confirmed
    Close while its Issue remains strategic.
    """
    # actor 与目标版本行的同 scope JOIN 授权读取（FOR UPDATE OF v）：缺失/跨 scope 泛化错误。
    close_row = _authorized_version(conn, record_id=close_record_id, actor_id=confirmed_by)
    # 授权后统一 canonical：确认 UPDATE 与 Issue 新版本 INSERT 不绑定原始替代表示。
    close_record_id = _require_canonical_uuid(close_record_id)
    confirmed_by = _require_canonical_uuid(confirmed_by)
    if close_row["object_type"] != "Close":
        raise InvalidObjectTypeError(f"{close_record_id} 不是 Close 版本行（object_type={close_row['object_type']!r}）")
    if close_row["confirmation_status"] == "confirmed":
        raise AlreadyConfirmedError(f"Close 版本 {close_record_id} 已经是 confirmed")
    _assert_source_refs_fresh(conn, close_record_id)

    issue_object_id = close_row["issue_id"]
    chain_id = close_row["chain_id"]

    issue_current = _lock_current_version(conn, issue_object_id)
    if issue_current is None:
        raise VersionNotFoundError(f"Issue {issue_object_id} 没有任何版本行（数据不一致）")
    if issue_current["issue_state"] == "closed":
        raise IssueStateTransitionError(f"Issue {issue_object_id} 已经是 closed 状态")
    # confirm_close 是 Issue 的确认时刻之一（新版本直接 confirmed），同样需要比对 Issue 当前版
    # Validate source freshness for the Issue as well as the Close version.
    _assert_source_refs_fresh(conn, issue_current["record_id"])

    # 1. Close 版本行确认（唯一允许的原位 UPDATE：确认元数据）
    conn.execute(
        """UPDATE wm_object_versions SET confirmation_status='confirmed', confirmed_by=%s, confirmed_at=now()
           WHERE record_id=%s AND confirmation_status='unconfirmed'""",
        (confirmed_by, close_record_id),
    )

    # 2. Create a confirmed closed Issue version from the previous business content.
    new_content = dict(issue_current["content"])
    if transition_note is not None:
        new_content["transition_note"] = transition_note
    issue_new_record_id = uuid.uuid4()
    issue_new_version = issue_current["version"] + 1
    conn.execute(
        """INSERT INTO wm_object_versions
             (record_id, object_id, object_type, version, supersedes, issue_state,
              confirmation_status, content, created_by, confirmed_by, confirmed_at)
           VALUES (%s,%s,'Issue',%s,%s,'closed','confirmed',%s,%s,%s,now())""",
        (issue_new_record_id, issue_object_id, issue_new_version, issue_current["record_id"],
         Jsonb(new_content), confirmed_by, confirmed_by),
    )

    # 3. 链状态：closed
    conn.execute(
        "UPDATE wm_issue_chains SET status='closed', closed_at=now() WHERE chain_id=%s",
        (chain_id,),
    )

    return {
        "close_record_id": str(close_record_id),
        "close_confirmation_status": "confirmed",
        "issue_object_id": str(issue_object_id),
        "issue_record_id": str(issue_new_record_id),
        "issue_version": issue_new_version,
        "issue_state": "closed",
        "chain_id": str(chain_id),
        "chain_status": "closed",
    }


# Frozen source references


def attach_source_ref(
    conn, record_id, *, actor, fragment_id, ordinal: int, excerpt_snapshot: str,
    content_hash_snapshot: str, source_locator_snapshot: dict[str, Any] | None = None,
) -> dict:
    """Attach one frozen source ref to an unconfirmed version.

    ``actor`` 必填（keyword-only）：一次 scoped version+object+chain+users JOIN 完成授权
    并持有行锁（FOR UPDATE OF v / FOR SHARE OF o, c, u）；human/agent_service 可给未确认
    版本加 ref。target missing、跨 scope actor、非法 record UUID 统一为不含
    ID/type/status 的 VersionNotFoundError 泛化文案；非法 actor 在 Python 侧拦截，不送
    PG。object/chain/tenant/org 全部从 scoped row 派生，不接受调用方 scope。

    ``fragment_id`` 只是冻结的外部标识：本阶段没有可信 fragment registry，不伪造
    fragment scope 校验（真正的 Evidence scope 校验在 P2E）；仅做 Python 侧 UUID
    规范化，非法时 InvalidSourceRefError，事务保持可读。
    """
    if ordinal < 1:
        raise InvalidSourceRefError("ordinal 必须 >= 1")
    if not excerpt_snapshot:
        raise InvalidSourceRefError("excerpt_snapshot 不能为空")
    if not _SHA256_HEX_RE.match(content_hash_snapshot or ""):
        raise InvalidSourceRefError("content_hash_snapshot 必须是 64 位十六进制 sha256")
    if hashlib.sha256(excerpt_snapshot.encode("utf-8")).hexdigest() != content_hash_snapshot:
        raise InvalidSourceRefError("excerpt_snapshot 与 content_hash_snapshot 不一致")
    canonical_fragment_id = _canonical_uuid(fragment_id)
    if canonical_fragment_id is None:
        raise InvalidSourceRefError("fragment_id 不是合法的 UUID")

    try:
        version_row = _authorized_version(
            conn, record_id=record_id, actor_id=actor, field_name="actor", human_only=False
        )
    except ActorOutOfScopeError as exc:
        # target missing / 跨 scope actor / 非法 record 或 actor UUID 同文泛化，
        # 不泄露外部 ID、type、status、disposition。
        raise VersionNotFoundError("版本行不存在或不在调用者 scope") from exc
    if version_row["confirmation_status"] == "confirmed":
        raise SourceRefImmutableError(
            "目标版本行已 confirmed，来源引用不可再增删（需新建版本）"
        )

    conn.execute(
        """INSERT INTO wm_version_source_refs
             (record_id, object_id, chain_id, tenant_id, organization_id, fragment_id,
              ordinal, excerpt_snapshot, content_hash_snapshot, source_locator_snapshot)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
        (
            version_row["record_id"], version_row["object_id"], version_row["chain_id"],
            version_row["tenant_id"], version_row["organization_id"], canonical_fragment_id,
            ordinal, excerpt_snapshot, content_hash_snapshot,
            Jsonb(source_locator_snapshot or {}),
        ),
    )
    return {
        "record_id": str(version_row["record_id"]), "fragment_id": str(canonical_fragment_id),
        "ordinal": ordinal,
        "excerpt_snapshot": excerpt_snapshot, "content_hash_snapshot": content_hash_snapshot,
        "source_locator_snapshot": source_locator_snapshot or {},
    }


def list_source_refs(conn, record_id) -> list[dict]:
    """Return frozen source refs in ordinal order without joining live fragments."""
    with _dictcur(conn) as cur:
        cur.execute(
            """SELECT record_id, fragment_id, ordinal, excerpt_snapshot, content_hash_snapshot,
                      source_locator_snapshot, created_at
               FROM wm_version_source_refs WHERE record_id=%s ORDER BY ordinal""",
            (record_id,),
        )
        rows = cur.fetchall()
    return [_stringify(r, ("record_id", "fragment_id")) for r in rows]


# Read-only retrieval


def list_chain_current_versions_by_type(conn, chain_id, object_type: str) -> list[dict]:
    """Return current versions of one object type in a chain."""
    if object_type not in OBJECT_TYPES:
        raise InvalidObjectTypeError(f"非法 object_type：{object_type!r}")
    return _list_chain_current_versions_by_type(conn, chain_id, object_type)
