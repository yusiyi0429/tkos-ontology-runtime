"""Frozen Method protocol identity; independent of historical Contract-A rules."""
from __future__ import annotations

from typing import Literal
from pydantic import BaseModel, ConfigDict, StrictStr
from . import canon

PROTOCOL_ID = "tkos.method"
CONTRACT_VERSION = "tkos.method/0.1"
SCHEMA_VERSION = "tkos.method-profile/0.1"
CONTRACT_SHA256 = "d108d228e903384182b6945181aa5bcafde4a3f64b4c564d897372805588d1c3"
PROFILE_ID = "urn:tkos:method:m1a-m1b"
PROFILE_REVISION = "0.1.0"


class ContractRef(BaseModel):
    model_config = ConfigDict(extra="forbid")
    contract_id: Literal["tkos.method"] = "tkos.method"
    revision: Literal["0.1"] = "0.1"
    content_sha256: Literal[CONTRACT_SHA256] = CONTRACT_SHA256


class MethodProfileCore(BaseModel):
    model_config = ConfigDict(extra="forbid")
    profile_core_schema_version: Literal[SCHEMA_VERSION]
    profile_id: Literal[PROFILE_ID]
    revision: Literal[PROFILE_REVISION]
    display_name: Literal["M1A L4 r21 + M1B L5 r837"]
    record_origin: Literal["synthetic"]
    experimental: Literal[True]
    corporate_approved: Literal[False]
    m1a_source_revision: Literal[21]
    m1b_source_revision: Literal[837]
    action_contract_ref: ContractRef
    canonical_hash: StrictStr


def validate(data):
    core = MethodProfileCore.model_validate(data)
    if core.canonical_hash != canon.digest_excluding(core.model_dump(mode="json"), frozenset({"canonical_hash"})):
        raise ValueError("Method profile canonical_hash mismatch")
    return core


def content():
    value = {
        "profile_core_schema_version": SCHEMA_VERSION, "profile_id": PROFILE_ID,
        "revision": PROFILE_REVISION, "display_name": "M1A L4 r21 + M1B L5 r837",
        "record_origin": "synthetic", "experimental": True, "corporate_approved": False,
        "m1a_source_revision": 21, "m1b_source_revision": 837,
        "action_contract_ref": ContractRef().model_dump(mode="json"),
    }
    value["canonical_hash"] = canon.digest_excluding(value, frozenset({"canonical_hash"}))
    return value
