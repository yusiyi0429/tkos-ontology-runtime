from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import re
import tarfile

import pytest


RELEASE = Path(__file__).resolve().parent


def load(name: str):
    spec = importlib.util.spec_from_file_location(name, RELEASE / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_vendor_plan_has_every_component_and_platform() -> None:
    plan = json.loads((RELEASE / "images.json").read_text())
    assert set(plan["components"]) == {"postgres_pgvector", "minio_server", "minio_client"}
    for component in plan["components"].values():
        assert set(component["platforms"]) == {"amd64", "arm64"}
        assert re.fullmatch(r"sha256:[0-9a-f]{64}", component["index_digest"])
        assert all(re.fullmatch(r"sha256:[0-9a-f]{64}", value)
                   for value in component["platforms"].values())


def test_dependency_lock_is_exported_from_the_frozen_project_lock() -> None:
    lock = (RELEASE.parents[1] / "requirements.lock").read_text()
    assert "--hash=sha256:" in lock
    assert not any(line.startswith("tkos-memory-service==") for line in lock.splitlines())


def test_database_privilege_classes_keep_control_plane_read_only() -> None:
    admin = load("db_admin")
    assert admin.expected_privileges("gov_method_agent_bindings") == {"SELECT"}
    assert admin.expected_privileges("gov_method_state") == {"SELECT", "INSERT", "UPDATE"}
    assert admin.expected_privileges("gov_method_reviews") == {"SELECT", "INSERT"}
    assert admin.expected_privileges("runtime_tasks") == {"SELECT", "INSERT", "UPDATE"}
    assert admin.expected_privileges("entities") == {"SELECT", "INSERT", "UPDATE", "DELETE"}


def test_bundle_rejects_parent_and_link_members() -> None:
    verifier = load("verify_bundle")
    with pytest.raises(RuntimeError, match="UNSAFE_ARCHIVE_MEMBER"):
        verifier.validate_member(tarfile.TarInfo("../outside"))
    link = tarfile.TarInfo("safe/link")
    link.type = tarfile.SYMTYPE
    with pytest.raises(RuntimeError, match="UNSAFE_ARCHIVE_MEMBER"):
        verifier.validate_member(link)


def test_compose_uses_exactly_the_five_release_images() -> None:
    compose = (RELEASE / "compose.yaml").read_text()
    env = (RELEASE / ".env.example").read_text()
    keys = {
        "RUNTIME_API_IMAGE", "RUNTIME_WORKER_IMAGE", "POSTGRES_IMAGE",
        "MINIO_IMAGE", "MINIO_MC_IMAGE",
    }
    assert {line.split("=", 1)[0] for line in env.splitlines() if line and not line.startswith("#")} >= keys
    assert all(f"${{{key}}}" in compose for key in keys)
    assert "clark" not in compose.lower()
