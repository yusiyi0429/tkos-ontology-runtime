"""tkos.world/0.2 的 profile 身份，与 world 0.1 和所有 Method profile 无关。

world 0.2 profile 钉定契约正文（docs/contracts/tkos-world-0.2.md）与 world 0.2 登记
（docs/contracts/world-registry-0.2.json）的原始字节。锁版前两者可以改（ADR-0009）：
修订号保持 0.2.0，每改一次就重钉这里、迁移 0039 与 docs/contracts/world-profile-0.2.json，
实验库从头重建。
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
DISPLAY_NAME = "World 0.2 business world model draft 2026-09-28"
CONTRACT_SHA256 = "fb260b28283fa0fa1a0b7d17291e9b18cbbeea32db37f0408be44cd8bc24a0df"
REGISTRY_ID = "tkos.world-registry"
REGISTRY_REVISION = "0.2.0"
REGISTRY_SHA256 = "098dd564a5e4297602ef4e99871730998d6fff2efddcf9344b56abb8f1264bd1"


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
