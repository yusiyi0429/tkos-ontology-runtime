"""Frozen Contract-A offline artifacts shipped inside the package.

The runtime must never resolve Semantica output paths at run time: the exact
main-contract bytes and the frozen profile-core fixture are bundled here and
travel with the wheel.  Hashes are pinned constants; loaders verify before
returning, so a corrupted or substituted artifact fails closed.
"""
from __future__ import annotations

import hashlib
from importlib.resources import files

from . import profile

PROFILE_CORE_FILE = "profile-core.json"
PROFILE_CORE_SHA256 = "2b7ec89bc489090f4d12d094d19ba49a52124425e094c6131d68c94488efe654"
CONTRACT_FILE = "tkos-contract-a-0.1.md"


def _read(name: str, expected_sha256: str) -> bytes:
    data = files("memory_service_runtime.governed").joinpath("resources", name).read_bytes()
    actual = hashlib.sha256(data).hexdigest()
    if actual != expected_sha256:
        raise RuntimeError(
            f"bundled artifact {name} hash mismatch: expected {expected_sha256}, got {actual}"
        )
    return data


def contract_a_bytes() -> bytes:
    """Exact Contract-A v0.1 main-contract bytes (pinned SHA256)."""
    return _read(CONTRACT_FILE, profile.CONTRACT_A_MAIN_CONTRACT_SHA256)


def profile_core_bytes() -> bytes:
    """Frozen profile-core.json fixture bytes (pinned SHA256)."""
    return _read(PROFILE_CORE_FILE, PROFILE_CORE_SHA256)
