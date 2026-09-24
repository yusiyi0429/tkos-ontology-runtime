"""tkos.world/0.1 的 profile 身份，与所有 Method profile 无关。

world profile 钉定契约正文（docs/contracts/tkos-world-0.1.md）与 world 登记
（docs/contracts/world-registry-0.1.json）的原始字节。
"""
from typing import Literal
from pydantic import BaseModel, ConfigDict, StrictStr
from . import canon

PROTOCOL_ID = "tkos.world"
CONTRACT_REVISION = "0.1"
CONTRACT_VERSION = "tkos.world/0.1"
SCHEMA_VERSION = "tkos.world-profile/0.1"
PROFILE_ID = "urn:tkos:world"
PROFILE_REVISION = "0.1.3"
DISPLAY_NAME = "World 0.1 business world model 2026-09-24"
CONTRACT_SHA256 = "72e3b8032d81c8d993efc496d702d139f9ea1599c58d12adaf4aca020f9fff8f"
REGISTRY_ID = "tkos.world-registry"
REGISTRY_REVISION = "0.1.3"
REGISTRY_SHA256 = "62c69f1b8a388f385e86a474865d3f327df3c2c93a0e607459b4494c1610a545"


class ContractRef(BaseModel):
    model_config = ConfigDict(extra="forbid")
    contract_id: Literal[PROTOCOL_ID] = PROTOCOL_ID
    revision: Literal[CONTRACT_REVISION] = CONTRACT_REVISION
    content_sha256: Literal[CONTRACT_SHA256] = CONTRACT_SHA256


class RegistryRef(BaseModel):
    """world 登记 JSON 原始字节的 SHA256；登记改动必须产生新 revision 并重新钉定。"""
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
        raise ValueError("World profile canonical_hash mismatch")
    return value


def content() -> dict[str, object]:
    value: dict[str, object] = {"profile_core_schema_version": SCHEMA_VERSION, "profile_id": PROFILE_ID,
             "revision": PROFILE_REVISION, "display_name": DISPLAY_NAME,
             "record_origin": "synthetic", "experimental": True, "corporate_approved": False,
             "action_contract_ref": ContractRef().model_dump(mode="json"),
             "world_registry_ref": RegistryRef().model_dump(mode="json")}
    value["canonical_hash"] = canon.digest_excluding(value, frozenset({"canonical_hash"}))
    return value
