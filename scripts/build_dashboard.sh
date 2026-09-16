#!/usr/bin/env bash
# Reproducible dashboard build: locked npm install (npm ci only), typecheck,
# component tests, compile into src/memory_service_app/dashboard_dist, then
# record and verify the build manifest.  Python packaging (hatch/uv build) runs
# this check again through the Hatch build hook.
set -euo pipefail
cd "$(dirname "$0")/../workbench/dashboard"
if [ ! -f package-lock.json ]; then
  echo "package-lock.json is required: npm ci is the only supported install" >&2
  exit 1
fi
npm ci --no-audit --no-fund
npm run typecheck
npm test
npm run build
cd ../..
python3 scripts/verify_dashboard_assets.py --write-manifest
python3 scripts/verify_dashboard_assets.py
