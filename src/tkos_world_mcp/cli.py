"""tkos-world-mcp 的命令入口：SDK 在可选依赖组 mcp 里，没装时给出明确提示。"""
from __future__ import annotations

import sys


def main() -> None:
    try:
        from .server import main as serve
    except ModuleNotFoundError as exc:
        # server 先后导入 jsonschema 与 mcp，两者都只来自 [mcp] extra。
        if (exc.name or "").split(".")[0] not in {"mcp", "jsonschema"}:
            raise
        print("tkos-world-mcp needs the optional mcp dependencies: install tkos-memory-service[mcp] "
              "(uv sync --extra mcp).", file=sys.stderr)
        raise SystemExit(2) from exc
    serve()


if __name__ == "__main__":
    main()
