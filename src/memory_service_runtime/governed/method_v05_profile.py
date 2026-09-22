"""Method 0.5 ontology-v0.7 alignment contract identity, independent of 0.1-0.4.

Besides the contract bytes, a 0.5 profile pins the exact bytes of the ontology
registry JSON (docs/contracts/ontology-registry-0.7.json).
"""
from typing import Literal
from pydantic import BaseModel, ConfigDict
from . import canon, method_profile

PROTOCOL_ID = "tkos.method"
CONTRACT_VERSION = "tkos.method/0.5"
SCHEMA_VERSION = "tkos.method-profile/0.5"
CONTRACT_SHA256 = "d2ea113231533862fb2aed1611608b54c00fe66db0bbb99fe904ec6c69ed6d6f"
ONTOLOGY_REGISTRY_ID = "tkos.ontology-registry"
ONTOLOGY_REGISTRY_REVISION = "0.7.1"
ONTOLOGY_REGISTRY_SHA256 = "4f44c759d26db4e6812c60b11664add106697a1abf69935b9a9ca316e62220f8"
DISPLAY_NAME = "Method 0.5 ontology v0.7 alignment 2026-09-22"


class ContractRef(BaseModel):
    model_config = ConfigDict(extra="forbid")
    contract_id: Literal["tkos.method"] = "tkos.method"
    revision: Literal["0.5"] = "0.5"
    content_sha256: Literal[CONTRACT_SHA256] = CONTRACT_SHA256


class OntologyRegistryRef(BaseModel):
    """本体登记 JSON 原始字节的 SHA256；登记改动必须产生新 revision 并重新 pin。"""
    model_config = ConfigDict(extra="forbid")
    registry_id: Literal[ONTOLOGY_REGISTRY_ID] = ONTOLOGY_REGISTRY_ID
    revision: Literal[ONTOLOGY_REGISTRY_REVISION] = ONTOLOGY_REGISTRY_REVISION
    content_sha256: Literal[ONTOLOGY_REGISTRY_SHA256] = ONTOLOGY_REGISTRY_SHA256


class Profile(method_profile.MethodProfileCore):
    profile_core_schema_version: Literal[SCHEMA_VERSION]
    revision: Literal["0.5.0"]
    display_name: Literal[DISPLAY_NAME]
    action_contract_ref: ContractRef
    ontology_registry_ref: OntologyRegistryRef


def validate(data):
    value = Profile.model_validate(data)
    if value.canonical_hash != canon.digest_excluding(value.model_dump(mode="json"), frozenset({"canonical_hash"})):
        raise ValueError("Method profile canonical_hash mismatch")
    return value


def content():
    value = {**method_profile.content(), "profile_core_schema_version": SCHEMA_VERSION,
             "revision": "0.5.0", "display_name": DISPLAY_NAME,
             "action_contract_ref": ContractRef().model_dump(mode="json"),
             "ontology_registry_ref": OntologyRegistryRef().model_dump(mode="json")}
    value["canonical_hash"] = canon.digest_excluding(value, frozenset({"canonical_hash"}))
    return value
