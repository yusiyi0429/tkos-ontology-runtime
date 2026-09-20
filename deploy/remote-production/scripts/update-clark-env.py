#!/usr/bin/env python3
"""Install Clark's production NarrativeClient settings without exposing the token."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import tempfile


CLARK_ENV = Path("/etc/tokenhub/clark-main.env")
BACKUPS = Path("/etc/tokenhub/runtime-cutover-backups")


def runtime_env(path: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if len(value) < 2 or value[0] != "'" or value[-1] != "'":
            raise RuntimeError("RUNTIME_ENV_FORMAT_INVALID")
        result[key] = value[1:-1]
    return result


def safe(value: str) -> str:
    if not value or "\n" in value or "\r" in value:
        raise RuntimeError("CLARK_ENV_VALUE_INVALID")
    return value


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release-id", required=True)
    parser.add_argument("--runtime-root", default="/srv/tokenhub/apps/tkos-runtime-prod/current")
    args = parser.parse_args()
    if os.geteuid() != 0:
        raise RuntimeError("ROOT_REQUIRED")
    root = Path(args.runtime_root)
    values = runtime_env(root / ".env")
    token = safe((root / "private/runtime-service-token").read_text(encoding="utf-8").strip())
    updates = {
        "TKOS_MEMORY_API_BASE_URL": "https://memory-api.tokenkingos.com",
        "TKOS_MEMORY_API_KEY": token,
        "TKOS_MEMORY_API_TENANT": safe(values["MEMORY_TENANT"]),
        "TKOS_MEMORY_API_ORG": safe(values["MEMORY_ORG"]),
        "TKOS_ONTOLOGY_SOURCE": "narrative",
        "TKOS_ONTOLOGY_CONTEXT": "1",
        "TKOS_ONTOLOGY_TIMEOUT_MS": "45000",
    }
    original = CLARK_ENV.read_text(encoding="utf-8")
    BACKUPS.mkdir(mode=0o700, parents=True, exist_ok=True)
    backup = BACKUPS / f"clark-main.env.{args.release_id}.before"
    if backup.exists():
        raise RuntimeError("CLARK_ENV_BACKUP_ALREADY_EXISTS")
    shutil.copy2(CLARK_ENV, backup)
    os.chmod(backup, 0o600)
    output: list[str] = []
    seen: set[str] = set()
    for line in original.splitlines():
        if "=" not in line or line.lstrip().startswith("#"):
            output.append(line)
            continue
        key = line.split("=", 1)[0].strip()
        if key == "TKOS_ONTOLOGY_SNAPSHOT":
            continue
        if key in updates:
            if key not in seen:
                output.append(key + "=" + updates[key])
                seen.add(key)
        else:
            output.append(line)
    for key, value in updates.items():
        if key not in seen:
            output.append(key + "=" + value)
    fd, temporary = tempfile.mkstemp(prefix="clark-main.env.", dir=str(CLARK_ENV.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write("\n".join(output) + "\n")
        os.chmod(temporary, 0o600)
        os.chown(temporary, 0, 0)
        os.replace(temporary, CLARK_ENV)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    print(json.dumps({"ok": True, "release_id": args.release_id,
                      "updated_keys": sorted(updates), "removed_snapshot": True,
                      "backup": str(backup), "secret_values_printed": False},
                     separators=(",", ":")))


if __name__ == "__main__":
    main()
