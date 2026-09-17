"""Export the focused tkos.workspace/0.2 partner API contract.

Only the paths a partner needs for standalone source collaboration are
included, with transitively referenced schemas. No database or secret is
touched. ``--check`` fails when the checked-in snapshot is stale.
"""
import argparse
import json
from pathlib import Path
import sys

PATHS = [
    "/v1/identity",
    "/v1/workspace-scenes/events",
    "/v1/workspace-sources",
    "/v1/workspace-sources/{scene_id}",
    "/v1/workspace-sources/contexts",
    "/v1/workspace-sources/contexts/{context_id}",
    "/v1/action-receipts/{receipt_id}",
    "/v1/objects/{object_id}",
    "/v1/objects/{object_id}/revisions/{revision_id}",
    "/v1/evidence-assets",
    "/v1/evidence-assets/{object_id}/revisions/{revision_id}",
    "/v1/context-packs",
    "/v1/context-packs/{snapshot_id}",
]


def _collect_refs(node, schemas, components, seen):
    if isinstance(node, dict):
        reference = node.get("$ref")
        if isinstance(reference, str) and reference.startswith("#/components/schemas/"):
            name = reference.rsplit("/", 1)[-1]
            if name not in seen:
                seen.add(name)
                schema = components["schemas"][name]
                schemas[name] = schema
                _collect_refs(schema, schemas, components, seen)
        for value in node.values():
            _collect_refs(value, schemas, components, seen)
    elif isinstance(node, list):
        for value in node:
            _collect_refs(value, schemas, components, seen)


def build(source_root: Path) -> dict:
    sys.path.insert(0, str(source_root))
    from memory_service_app.main import app
    full = app.openapi()
    missing = [path for path in PATHS if path not in full["paths"]]
    if missing:
        raise SystemExit("workspace/0.2 paths missing from the application: " + ", ".join(missing))
    schemas: dict = {}
    for path in PATHS:
        _collect_refs(full["paths"][path], schemas, full["components"], set())
    return {
        "openapi": full["openapi"],
        "info": {**full["info"], "title": full["info"]["title"] + " — tkos.workspace/0.2 partner surface"},
        "paths": {path: full["paths"][path] for path in PATHS},
        "components": {"schemas": dict(sorted(schemas.items()))},
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    value = build(root / "src")
    target = root / "docs/runtime-workspace-v02-openapi.json"
    text = json.dumps(value, ensure_ascii=False, indent=2) + "\n"
    if args.check:
        if not target.exists() or target.read_text() != text:
            raise SystemExit("workspace/0.2 OpenAPI snapshot differs from the source")
    else:
        target.write_text(text)
    print("workspace/0.2 OpenAPI snapshot verified" if args.check else "workspace/0.2 OpenAPI snapshot exported")


if __name__ == "__main__":
    main()
