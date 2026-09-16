#!/usr/bin/env python3
"""Write the tkos.dashboard/0.1 read-contract subset of the app OpenAPI."""
from __future__ import annotations

import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from memory_service_app.main import app  # noqa: E402


def main() -> None:
    spec = app.openapi()
    paths = {path: value for path, value in spec["paths"].items()
             if path.startswith("/v1/dashboard")}
    if not paths:
        raise SystemExit("the app exposes no /v1/dashboard routes")
    subset = {
        "openapi": spec["openapi"],
        "info": {
            "title": "TKOS Runtime dashboard read contract",
            "version": "tkos.dashboard/0.1",
            "description": (
                "GET-only business reads over the governed runtime.  Requires the current "
                "Bearer identity on /v1/dashboard; the local /dashboard/api facade calls the "
                "same readers with the server-configured viewer identity."
            ),
        },
        "paths": paths,
        "components": {"schemas": spec.get("components", {}).get("schemas", {})},
    }
    target = ROOT / "docs" / "runtime-dashboard-openapi.json"
    target.write_text(json.dumps(subset, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
                      encoding="utf-8")
    print(f"wrote {target.relative_to(ROOT)} with {len(paths)} paths")


if __name__ == "__main__":
    main()
