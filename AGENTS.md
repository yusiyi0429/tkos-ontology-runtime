# Repository Guidelines

## Project Structure & Module Organization

This repository owns the TKOS governance Runtime; Clark is maintained separately.

- `src/memory_service_runtime/governed/`: ontology models, actions, authorization, evidence, and receipts.
- `src/memory_service_runtime/`: persistent task queue and Worker.
- `src/memory_service_app/`: FastAPI entry point, configuration, and append-only SQL migrations in `migrations/`.
- `src/memory_service/` and `src/adapter/`: memory core and Clark compatibility interfaces.
- `workbench/dashboard/`: the dashboard, a Vite + React + TypeScript app whose build output is committed under `src/memory_service_app/dashboard_dist/`.
- `tests/`: Python regressions; `acceptance/`: independent HTTP/database/storage scenarios.
- `docs/`, `contracts/`, and `deploy/`: specifications, API snapshots, and environment-specific deployment tools.

## Build, Test, and Development Commands

Use Python 3.12+, uv, and Node.js for the dashboard. Configure an isolated PostgreSQL/MinIO environment first; see `acceptance/runtime/README.md` for bootstrap and role-aware command execution. `tests/conftest.py` requires `DATABASE_URL` only for tests marked `db` (they fail without it rather than fall back to a local default); `uv run pytest tests -q -m "not db"` runs everything else without a database.

- `uv sync --frozen --extra s3`: install locked dependencies and development tools.
- `uv run uvicorn memory_service_app.main:app --host 127.0.0.1 --port 8010`: start the configured API locally.
- `uv run tkos-memory-worker`: start the configured Worker.
- `uv run pytest tests -q -m "not db"`: run all database-free regressions; `uv run pytest tests/test_method_m1b_models.py -q` runs one file.
- `npm test`, `npm run typecheck`, `npm run build` in `workbench/dashboard/`: test, type-check, and rebuild the dashboard. Rebuild after any dashboard source change; `tests/dashboard/test_dashboard_assets.py` fails when the committed build is stale.
- `uv build`: build the source distribution and wheel.

## Coding Style & Naming Conventions

Use four-space Python indentation, type hints, `snake_case` functions/modules, and `PascalCase` models. JavaScript uses ES modules and two-space indentation. Match surrounding code; no repository-wide formatter or lint command is configured. Name new migrations `NNNN_description.sql`; never rewrite applied migrations. Preserve existing package and CLI names for compatibility.

## Testing Guidelines

Use pytest (`test_*.py`, `test_*` functions) and Node's built-in runner (`*.test.mjs`). There is no configured coverage-percentage gate. Cover relevant permission, version-conflict, idempotency, and recovery cases. Use randomized tenant/organization scopes, never `local/local-org`; seed business records through governance paths.

For Method changes, run the independent acceptance for the affected version in its version-named directory, such as `acceptance/method_v04/` or `acceptance/method_v05/`, on a fresh isolated database built with `acceptance/method_v05/database.py` (it only accepts the isolated acceptance stack; never the Clark-linked stack on 54350/54351). `acceptance/method_independent/` records the 2026-09-11 M1A/M1B run and cannot be rerun as-is at HEAD. Unit tests do not replace independent acceptance. Report executed checks, skips, and unverified deployment boundaries separately.

## Commit & Pull Request Guidelines

Follow recent conventions: `feat(method): ...`, `feat(runtime): ...`, `test(clark): ...`, or `docs: ...`. Keep changes focused and preserve unrelated worktree edits. PRs should describe business behavior, contract/migration changes, validation results, and limitations. Link relevant issues; include screenshots for workbench changes.

## Security & Governance

Never print or commit credentials, `.runtime-acceptance/`, or raw acceptance artifacts. API/Worker database roles must remain distinct from migration owners. Preserve protocol-specific semantics, immutable versions, current authorization, and atomic business/audit/receipt writes.
