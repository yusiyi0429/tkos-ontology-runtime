"""Versioned lifecycle contract identity, independent of the frozen 0.1 core."""
from typing import Literal
from pydantic import BaseModel, ConfigDict
from . import canon, method_profile

PROTOCOL_ID = "tkos.method"
CONTRACT_VERSION = "tkos.method/0.2"
SCHEMA_VERSION = "tkos.method-profile/0.2"
CONTRACT_SHA256 = "82568bdff37c711207a1a010ae553f72b2f036122b1f88a3b92aec286f9fbbf3"


class ContractRef(BaseModel):
    model_config = ConfigDict(extra="forbid")
    contract_id: Literal["tkos.method"] = "tkos.method"
    revision: Literal["0.2"] = "0.2"
    content_sha256: Literal[CONTRACT_SHA256] = CONTRACT_SHA256


class Profile(method_profile.MethodProfileCore):
    profile_core_schema_version: Literal[SCHEMA_VERSION]
    revision: Literal["0.2.0"]
    display_name: Literal["Lifecycle alignment 2026-09-15"]
    action_contract_ref: ContractRef


def validate(data):
    value = Profile.model_validate(data)
    if value.canonical_hash != canon.digest_excluding(value.model_dump(mode="json"), frozenset({"canonical_hash"})):
        raise ValueError("Method profile canonical_hash mismatch")
    return value


def content():
    value = {**method_profile.content(), "profile_core_schema_version": SCHEMA_VERSION,
             "revision": "0.2.0", "display_name": "Lifecycle alignment 2026-09-15",
             "action_contract_ref": ContractRef().model_dump(mode="json")}
    value["canonical_hash"] = canon.digest_excluding(value, frozenset({"canonical_hash"}))
    return value
