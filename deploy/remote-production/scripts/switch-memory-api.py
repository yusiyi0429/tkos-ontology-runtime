#!/usr/bin/env python3
"""Atomically switch the public Memory Nginx upstream after candidate acceptance."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile


CONFIG = Path("/etc/nginx/sites-available/memory-api.conf")
BACKUPS = Path("/etc/tokenhub/runtime-cutover-backups")


def command(argv: list[str]) -> None:
    result = subprocess.run(argv, text=True, capture_output=True, check=False)
    if result.returncode:
        raise RuntimeError("NGINX_COMMAND_FAILED")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release-id", required=True)
    parser.add_argument("--from-port", type=int, default=8020)
    parser.add_argument("--to-port", type=int, default=8030)
    args = parser.parse_args()
    if os.geteuid() != 0:
        raise RuntimeError("ROOT_REQUIRED")
    original = CONFIG.read_text(encoding="utf-8")
    old = f"proxy_pass http://127.0.0.1:{args.from_port};"
    new = f"proxy_pass http://127.0.0.1:{args.to_port};"
    if original.count(old) != 1 or new in original:
        raise RuntimeError("NGINX_UPSTREAM_BASELINE_DIVERGED")
    server_name = "    server_name memory-api.tokenkingos.com;\n"
    if server_name not in original:
        raise RuntimeError("NGINX_SERVER_BASELINE_DIVERGED")
    BACKUPS.mkdir(mode=0o700, parents=True, exist_ok=True)
    backup = BACKUPS / f"memory-api.conf.{args.release_id}.before"
    if backup.exists():
        raise RuntimeError("NGINX_BACKUP_ALREADY_EXISTS")
    shutil.copy2(CONFIG, backup)
    os.chmod(backup, 0o600)
    candidate = original.replace(old, new).replace(
        server_name, server_name + "\n    client_max_body_size 4m;\n", 1
    )
    fd, temporary = tempfile.mkstemp(prefix="memory-api.conf.", dir=str(CONFIG.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(candidate)
        os.chmod(temporary, 0o644)
        os.replace(temporary, CONFIG)
        try:
            command(["nginx", "-t"])
            command(["systemctl", "reload", "nginx"])
        except Exception:
            shutil.copyfile(backup, CONFIG)
            os.chmod(CONFIG, 0o644)
            command(["nginx", "-t"])
            command(["systemctl", "reload", "nginx"])
            raise
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    print(json.dumps({"ok": True, "release_id": args.release_id,
                      "from_port": args.from_port, "to_port": args.to_port,
                      "backup": str(backup)}, separators=(",", ":")))


if __name__ == "__main__":
    main()
