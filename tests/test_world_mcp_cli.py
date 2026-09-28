"""tkos-world-mcp 在缺少可选依赖组 mcp 时给出安装提示，而不是原始 traceback。

server 模块依次 import jsonschema 与 mcp，二者都只来自 [mcp] extra；不论先缺哪一个，
入口都应落到同一个提示。子进程里用 meta_path 屏蔽导入，不需要真的卸载依赖。
"""
from __future__ import annotations

from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
BLOCK_AND_RUN = """
import importlib.abc, sys
blocked = set(sys.argv[1].split(","))
class Block(importlib.abc.MetaPathFinder):
    def find_spec(self, name, path=None, target=None):
        if name.split(".")[0] in blocked:
            raise ModuleNotFoundError(f"No module named {name!r}", name=name)
        return None
sys.meta_path.insert(0, Block())
for name in list(sys.modules):
    if name.split(".")[0] in blocked:
        del sys.modules[name]
from tkos_world_mcp.cli import main
main()
"""


@pytest.mark.parametrize("blocked", ["mcp,jsonschema", "jsonschema", "mcp"])
def test_missing_optional_dependencies_print_the_install_hint(blocked: str) -> None:
    run = subprocess.run([sys.executable, "-c", BLOCK_AND_RUN, blocked], cwd=ROOT, capture_output=True,
                         text=True, timeout=60, env={"PYTHONPATH": str(ROOT / "src"), "PATH": "/usr/bin:/bin"})
    assert run.returncode == 2, run.stderr[-500:]
    assert "tkos-memory-service[mcp]" in run.stderr
    assert "Traceback" not in run.stderr
