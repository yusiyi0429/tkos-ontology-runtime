"""Snapshot object-store adapter and immutable-storage probe.

The host supplies its S3 client and bucket; Memory reads no settings or credentials.  The read-only
probe reports versioning, Object Lock, and default retention without changing the bucket.

``object_store.preflight_immutability`` requires:
1. bucket versioning = Enabled（PutObject 条件写 IfNoneMatch 依赖版本控制语义）；
2. Object Lock = Enabled；
3. 默认 retention：Mode ∈ {GOVERNANCE, COMPLIANCE} 且 Days/Years 恰好一个为正。

关键运维事实（实测）：Object Lock 只能在 bucket 创建时（x-amz-bucket-object-lock-enabled）
启用，**现有 bucket 无法事后补开**。因此快照应使用独立专用 bucket，而非现有工作 bucket
（已存 documents 且无法开启 Object Lock）；bucket 的选择与创建属宿主职责。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from botocore.exceptions import ClientError

from memory_service.context_graph.object_store import S3SnapshotObjectStore
from memory_service.context_graph.snapshot import SnapshotStorageError

_RETENTION_MODES = ("GOVERNANCE", "COMPLIANCE")


def get_snapshot_object_store(client: Any, bucket: str, *, max_conflict_retries: int = 5) -> S3SnapshotObjectStore:
    """构造 S3SnapshotObjectStore。

    ``client``（boto3 S3 client）与 ``bucket`` 由宿主显式装配（host capability boundary：settings/S3
    凭证归宿主，Memory 不读 settings、不自取 S3）；测试可注入 stub client。
    ``max_conflict_retries``：仅 409 条件写冲突的有限重试上限（object_store 既定语义）。
    """
    return S3SnapshotObjectStore(client, bucket=bucket, max_conflict_retries=max_conflict_retries)


def preflight_snapshot_storage(bucket: str, client: Any) -> None:
    """Fail closed unless the bucket satisfies every immutability requirement."""
    get_snapshot_object_store(client=client, bucket=bucket).preflight_immutability()


# ---------------------------------------------------------------------------
# 只读探测：逐项报告现状（诊断用；不写对象、不改门禁）
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ImmutabilityProbe:
    """bucket 不可变门禁三要素的逐项现状。"""

    bucket: str
    versioning_enabled: bool = False
    object_lock_enabled: bool = False
    retention_mode: str | None = None
    retention_days: int | None = None
    retention_years: int | None = None
    api_errors: tuple[str, ...] = ()          # 探测期间个别 API 失败的说明（不阻塞其余项）
    passed: bool = False                       # 三要素全部满足才 True
    reasons: tuple[str, ...] = field(default_factory=tuple)  # 未通过的具体原因

    @property
    def gate(self) -> str:
        """人类可读的单行门禁结论（供报告/日志）。"""
        return "PASS" if self.passed else "FAIL:" + "; ".join(self.reasons) if self.reasons else "FAIL"


def _object_lock_config(client: Any, bucket: str) -> tuple[dict[str, Any] | None, str | None]:
    """返回 (ObjectLockConfiguration dict, api_error)；NotFound 视为未开启。"""
    try:
        resp = client.get_object_lock_configuration(Bucket=bucket)
        return resp.get("ObjectLockConfiguration") or {}, None
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "")
        if code in ("ObjectLockConfigurationNotFoundError", "NoSuchObjectLockConfiguration"):
            return None, None  # 未配置 = 未开启（不是探测错误）
        return None, f"get_object_lock_configuration: {code}"


def probe_immutability(bucket: str, client: Any) -> ImmutabilityProbe:
    """只读探测 bucket 不可变门禁三要素现状（versioning / Object Lock / 默认 retention）。

    - 不写任何对象、不修改任何配置；
    - 与 object_store.preflight_immutability 的校验口径一致，但逐项报告"现状"
      而不是只抛"不过"；
    - passed 仅在全部通过时为 True；api_errors 记录个别探测失败（如权限不足），
      不阻塞其余项。
    """
    b = bucket
    errors: list[str] = []
    reasons: list[str] = []

    # 1) versioning
    versioning_enabled = False
    try:
        v = client.get_bucket_versioning(Bucket=b)
        versioning_enabled = v.get("Status") == "Enabled"
    except ClientError as exc:
        errors.append(f"get_bucket_versioning: {exc.response.get('Error', {}).get('Code', '')}")
    if not versioning_enabled:
        reasons.append("bucket versioning 未启用")

    # 2) Object Lock
    lock_cfg, lock_err = _object_lock_config(client, b)
    if lock_err:
        errors.append(lock_err)
    object_lock_enabled = bool(lock_cfg and lock_cfg.get("ObjectLockEnabled") == "Enabled")
    if not object_lock_enabled:
        reasons.append("Object Lock 未启用（须在 bucket 创建时开启，无法事后补）")

    # 3) 默认 retention
    retention_mode = retention_days = retention_years = None
    if lock_cfg:
        rule = lock_cfg.get("Rule") or {}
        default = rule.get("DefaultRetention") or {}
        retention_mode = default.get("Mode")
        retention_days = default.get("Days")
        retention_years = default.get("Years")
    valid_mode = retention_mode in _RETENTION_MODES
    valid_days = isinstance(retention_days, int) and retention_days > 0
    valid_years = isinstance(retention_years, int) and retention_years > 0
    if not valid_mode:
        reasons.append(f"默认 retention Mode 非法/缺失（须 GOVERNANCE 或 COMPLIANCE，当前 {retention_mode!r}）")
    if not (valid_days ^ valid_years):
        reasons.append("默认 retention 需要且仅需要 Days 或 Years 一个为正")

    return ImmutabilityProbe(
        bucket=b,
        versioning_enabled=versioning_enabled,
        object_lock_enabled=object_lock_enabled,
        retention_mode=retention_mode,
        retention_days=retention_days,
        retention_years=retention_years,
        api_errors=tuple(errors),
        passed=not reasons and not errors,
        reasons=tuple(reasons),
    )
