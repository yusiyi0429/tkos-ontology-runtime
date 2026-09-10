"""tkos-json-v1 规范化与摘要（从已验收契约校验器抽取，语义不变）。

规则（服务器固定）：
- UTF-8 编码；
- 对象键按 Unicode 码点升序排序（递归）；
- 紧凑分隔符（"," 与 ":"），字符串按 JSON 标准转义，非 ASCII 不转义；
- 禁止 NaN / Infinity（含 1e999 这类指数溢出）与孤立 Unicode surrogate；
- 摘要是规范化字节的 SHA-256 十六进制小写。

本方案不宣称符合 RFC 8785 (JCS)。canonical_hash / manifest_hash 字段本身不进入
各自摘要输入。来源：契约包 A 已验收 validator/canon.py；运行时不得依赖 semantica
目录的绝对路径，故在此保留一份等价实现。
"""

from __future__ import annotations

import hashlib
import json
import math
from typing import Any

HASH_SCHEME = "tkos-json-v1"


class CanonError(ValueError):
    """输入无法按 tkos-json-v1 规范化（如 NaN/Infinity、孤立 surrogate、重复键）。"""


def _check_string(value: str, path: str) -> None:
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise CanonError(f"string not encodable as UTF-8 (lone surrogate?) at {path}") from exc


def _check_value(value: Any, path: str) -> None:
    if isinstance(value, bool) or value is None or isinstance(value, int):
        return
    if isinstance(value, str):
        _check_string(value, path)
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise CanonError(f"non-finite number at {path}")
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise CanonError(f"non-string key at {path}")
            _check_string(key, f"{path}.<key>")
            _check_value(item, f"{path}.{key}")
        return
    if isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            _check_value(item, f"{path}[{index}]")
        return
    raise CanonError(f"unsupported type {type(value).__name__} at {path}")


def canonicalize(value: Any) -> bytes:
    """返回 tkos-json-v1 规范字节。"""
    _check_value(value, "$")
    text = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return text.encode("utf-8")


def digest(value: Any) -> str:
    """规范化后取 SHA-256 十六进制小写。"""
    return hashlib.sha256(canonicalize(value)).hexdigest()


def digest_excluding(value: dict[str, Any], exclude_keys: frozenset[str]) -> str:
    """对顶层剔除指定键后的 dict 计算摘要（用于 hash 不自包含）。"""
    if not isinstance(value, dict):
        raise CanonError("digest_excluding expects a top-level object")
    filtered = {k: v for k, v in value.items() if k not in exclude_keys}
    return digest(filtered)


def load_json_bytes(data: bytes) -> Any:
    """从原始字节装载：非 UTF-8 输入拒绝为 CanonError，不产生 traceback。"""
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise CanonError(f"input is not valid UTF-8: {exc}") from exc
    return load_json_strict(text)


def load_json_strict(text: str) -> Any:
    """严格 JSON 解析：拒绝语法错误、NaN/Infinity、指数溢出、重复键、孤立 surrogate。"""

    def _reject_constant(name: str) -> Any:
        raise CanonError(f"non-finite number literal {name}")

    def _finite_float(raw: str) -> float:
        value = float(raw)
        if not math.isfinite(value):
            raise CanonError(f"non-finite number literal {raw}")
        return value

    def _object_no_dup(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        obj: dict[str, Any] = {}
        for key, val in pairs:
            if key in obj:
                raise CanonError(f"duplicate key {key!r}")
            obj[key] = val
        return obj

    try:
        data = json.loads(
            text,
            parse_constant=_reject_constant,
            parse_float=_finite_float,
            object_pairs_hook=_object_no_dup,
        )
    except json.JSONDecodeError as exc:
        raise CanonError(f"malformed JSON: {exc}") from exc
    _check_value(data, "$")
    return data
