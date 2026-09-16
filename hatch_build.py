"""Hatch build hook: refuse to package stale or missing dashboard assets."""
from __future__ import annotations

from pathlib import Path
import subprocess
import sys

from hatchling.builders.hooks.plugin.interface import BuildHookInterface


class CustomBuildHook(BuildHookInterface):
    PLUGIN_NAME = "custom"

    def initialize(self, version: str, build_data: dict) -> None:
        root = Path(self.root)
        verifier = root / "scripts" / "verify_dashboard_assets.py"
        if not verifier.is_file():
            raise RuntimeError("dashboard asset verifier is missing from the build context")
        result = subprocess.run([sys.executable, str(verifier)], cwd=root,
                                capture_output=True, text=True)
        if result.returncode:
            raise RuntimeError(
                "dashboard assets are stale or missing; run scripts/build_dashboard.sh "
                f"before packaging:\n{result.stdout.strip()}\n{result.stderr.strip()}")
        self.app.display_info(result.stdout.strip())
