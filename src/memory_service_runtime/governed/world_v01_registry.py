"""随包发布的 world 登记（docs/contracts/world-registry-0.1.json 的逐字节副本）。

运行时从登记取类型、块、属性与动作清单；首次使用时按 world profile 钉定的
SHA256 核对，被替换或损坏的副本一律拒绝，不回退到任何外部路径。按需加载：
登记出错只影响 world 请求，不影响其他协议的请求解析。
"""
from __future__ import annotations

from functools import lru_cache
import json
from typing import Any

from . import artifacts
from . import world_v01_profile as profile

REGISTRY_FILE = "world-registry-0.1.json"


def registry_bytes() -> bytes:
    return artifacts._read(REGISTRY_FILE, profile.REGISTRY_SHA256)


@lru_cache(maxsize=1)
def registry() -> dict[str, Any]:
    return json.loads(registry_bytes())


def object_types() -> frozenset[str]:
    return frozenset(item["type"] for item in registry()["objects"])


def action_spec(action: str) -> dict[str, Any]:
    return next(item for item in registry()["actions"] if item["action"] == action)


def object_spec(object_type: str) -> dict[str, Any]:
    for item in registry()["objects"]:
        if item["type"] == object_type:
            return item
    raise KeyError(object_type)
