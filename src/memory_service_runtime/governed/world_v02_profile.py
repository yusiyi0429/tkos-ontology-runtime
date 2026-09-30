"""tkos.world/0.2 的 profile 身份，与 world 0.1 和所有 Method profile 无关。

world 0.2 profile 钉定契约正文（docs/contracts/tkos-world-0.2.md）与 world 0.2 登记
（docs/contracts/world-registry-0.2.json）的原始字节。2026-09-30 锁版（ADR-0009）：修订 0.2.0
与迁移 0039 一起冻结。之后契约或登记的任何改动都新增迁移、重钉这里与
docs/contracts/world-profile-0.2.json，profile 与登记各出新修订号，不再改写 0039。
"""
from typing import Literal
from pydantic import BaseModel, ConfigDict, StrictStr
from . import canon

PROTOCOL_ID = "tkos.world"
CONTRACT_REVISION = "0.2"
CONTRACT_VERSION = "tkos.world/0.2"
SCHEMA_VERSION = "tkos.world-profile/0.2"
PROFILE_ID = "urn:tkos:world"
PROFILE_REVISION = "0.2.0"
DISPLAY_NAME = "World 0.2 business world model 2026-09-30"
CONTRACT_SHA256 = "aac8b40d6f3310d55667dfc41f3225fb95f6649495c6c919f3a8ef694e7dc6ef"
REGISTRY_ID = "tkos.world-registry"
REGISTRY_REVISION = "0.2.0"
REGISTRY_SHA256 = "1d5731ce51596d932be1e4fdff8a6c62c9f5bd64b48f11aa7fc36081ac729d1d"


class ContractRef(BaseModel):
    model_config = ConfigDict(extra="forbid")
    contract_id: Literal[PROTOCOL_ID] = PROTOCOL_ID
    revision: Literal[CONTRACT_REVISION] = CONTRACT_REVISION
    content_sha256: Literal[CONTRACT_SHA256] = CONTRACT_SHA256


class RegistryRef(BaseModel):
    """world 0.2 登记 JSON 原始字节的 SHA256。"""
    model_config = ConfigDict(extra="forbid")
    registry_id: Literal[REGISTRY_ID] = REGISTRY_ID
    revision: Literal[REGISTRY_REVISION] = REGISTRY_REVISION
    content_sha256: Literal[REGISTRY_SHA256] = REGISTRY_SHA256


class Profile(BaseModel):
    model_config = ConfigDict(extra="forbid")
    profile_core_schema_version: Literal[SCHEMA_VERSION]
    profile_id: Literal[PROFILE_ID]
    revision: Literal[PROFILE_REVISION]
    display_name: Literal[DISPLAY_NAME]
    record_origin: Literal["synthetic"]
    experimental: Literal[True]
    corporate_approved: Literal[False]
    action_contract_ref: ContractRef
    world_registry_ref: RegistryRef
    canonical_hash: StrictStr


def validate(data: object) -> Profile:
    value = Profile.model_validate(data)
    if value.canonical_hash != canon.digest_excluding(value.model_dump(mode="json"), frozenset({"canonical_hash"})):
        raise ValueError("World 0.2 profile canonical_hash mismatch")
    return value


def content() -> dict[str, object]:
    value: dict[str, object] = {"profile_core_schema_version": SCHEMA_VERSION, "profile_id": PROFILE_ID,
             "revision": PROFILE_REVISION, "display_name": DISPLAY_NAME,
             "record_origin": "synthetic", "experimental": True, "corporate_approved": False,
             "action_contract_ref": ContractRef().model_dump(mode="json"),
             "world_registry_ref": RegistryRef().model_dump(mode="json")}
    value["canonical_hash"] = canon.digest_excluding(value, frozenset({"canonical_hash"}))
    return value
