"""S3/MinIO snapshot adapter with create-only conditional writes.

Implements ``snapshot.SnapshotObjectStore`` with one injected boto3 client and bucket.
``put_if_absent`` uses the server-side ``IfNoneMatch='*'`` condition.  Botocore and callers own
network/timeout/5xx retry policy; only 409 ConditionalRequestConflict receives bounded jittered
backoff here.
"""
from __future__ import annotations

import random
import time
from typing import Callable

from botocore.exceptions import ClientError

from memory_service.context_graph.snapshot import SnapshotStorageError

_CONFLICT_CODE = "ConditionalRequestConflict"
_PRECONDITION_CODE = "PreconditionFailed"
_RETENTION_MODES = ("GOVERNANCE", "COMPLIANCE")


class S3SnapshotObjectStore:
    """不可覆盖快照对象存储，持有单个 S3 client 与 bucket。

    线程安全：boto3 client 并发安全，实例只读共享；退避用本地可变状态但仅在
    put_if_absent 单次调用内使用，不跨调用共享。
    """

    def __init__(
        self,
        s3_client,
        *,
        bucket: str,
        max_conflict_retries: int = 5,
        base_backoff: float = 0.2,
        backoff_max: float = 5.0,
        jitter_max: float = 0.1,
        sleep: Callable[[float], None] = time.sleep,
        random_float: Callable[[], float] = random.random,
    ) -> None:
        self._client = s3_client
        self._bucket = bucket
        self._max_conflict_retries = max_conflict_retries
        self._base_backoff = base_backoff
        self._backoff_max = backoff_max
        self._jitter_max = jitter_max
        self._sleep = sleep
        self._random_float = random_float

    def put_if_absent(self, key: str, data: bytes, *, content_type: str) -> bool:
        """仅当 key 不存在时写入；存在返回 False，不覆盖。"""
        for attempt in range(self._max_conflict_retries + 1):
            try:
                self._client.put_object(
                    Bucket=self._bucket, Key=key, Body=data,
                    ContentType=content_type, IfNoneMatch="*",
                )
                return True
            except ClientError as exc:
                code = _error_code(exc)
                if code == _PRECONDITION_CODE:
                    return False
                if code == _CONFLICT_CODE and attempt < self._max_conflict_retries:
                    self._sleep(self._backoff_delay(attempt))
                    continue
                raise

    def get(self, key: str) -> bytes:
        resp = self._client.get_object(Bucket=self._bucket, Key=key)
        return resp["Body"].read()

    def preflight_immutability(self) -> None:
        """生产启动门禁：versioning + Object Lock + 默认 retention 必须齐备。

        仅开启开关而没有 retention 同样失败；任一缺失均抛 SnapshotStorageError，
        防止在不可变保证未就绪时接收快照写。
        """
        try:
            versioning = self._client.get_bucket_versioning(Bucket=self._bucket)
            if versioning.get("Status") != "Enabled":
                raise SnapshotStorageError("bucket versioning must be Enabled")
            lock = self._client.get_object_lock_configuration(Bucket=self._bucket)
            config = lock.get("ObjectLockConfiguration") or {}
            if config.get("ObjectLockEnabled") != "Enabled":
                raise SnapshotStorageError("bucket object lock must be Enabled")
            retention = config.get("Rule", {}).get("DefaultRetention")
            if not retention:
                raise SnapshotStorageError("bucket default retention is required")
            mode = retention.get("Mode")
            if mode not in _RETENTION_MODES:
                raise SnapshotStorageError(
                    "default retention mode must be GOVERNANCE or COMPLIANCE"
                )
            days = retention.get("Days")
            years = retention.get("Years")
            valid_days = isinstance(days, int) and days > 0
            valid_years = isinstance(years, int) and years > 0
            if not (valid_days ^ valid_years):
                raise SnapshotStorageError(
                    "default retention requires exactly one positive Days or Years"
                )
        except SnapshotStorageError:
            raise
        except ClientError as exc:
            raise SnapshotStorageError(f"immutability preflight failed: {exc}") from exc

    def _backoff_delay(self, attempt: int) -> float:
        base = min(self._base_backoff * (2**attempt), self._backoff_max)
        return base * (1.0 + self._jitter_max * self._random_float())


def _error_code(exc: ClientError) -> str:
    return exc.response.get("Error", {}).get("Code", "")
