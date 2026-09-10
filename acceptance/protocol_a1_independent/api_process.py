"""Start one real API from an explicitly selected source tree on loopback.

The old-source mode imports no new production module or patched resolver.
Optional current-source checkpoints may pause/fail only, never approve actions.
"""
from __future__ import annotations

import argparse
import asyncio
import importlib
import json
import os
from pathlib import Path
import socket
import sys


def observe_database_identity(dsn: str, *, require_no_capability: bool) -> dict:
    """Observe this process's actual connection; never install a capability."""
    import psycopg
    from psycopg.rows import dict_row

    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        conn.execute("SET TRANSACTION READ ONLY")
        row = conn.execute("""SELECT current_user::text AS current_user,
            session_user::text AS session_user, r.rolsuper, r.rolbypassrls,
            r.oid=d.datdba AS is_db_owner,
            current_setting('app.runtime_write_capability',true) AS runtime_write_capability
            FROM pg_roles r JOIN pg_database d ON d.datname=current_database()
            WHERE r.rolname=current_user""").fetchone()
    if (row is None or not row["current_user"] or row["current_user"] != row["session_user"]
            or any(row[key] is not False for key in ("rolsuper", "rolbypassrls", "is_db_owner"))):
        raise ValueError("API startup requires an observed ordinary application database role")
    if require_no_capability and row["runtime_write_capability"] not in (None, ""):
        raise ValueError("unwrapped old API connection contains a new runtime capability")
    return {key: row[key] for key in ("current_user", "session_user", "rolsuper", "rolbypassrls",
                                     "is_db_owner", "runtime_write_capability")}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--ready-file", type=Path, required=True)
    parser.add_argument("--control-dir", type=Path)
    parser.add_argument("--key-file", type=Path)
    args = parser.parse_args()
    source = args.source.resolve()
    if not (source / "memory_service_app/main.py").is_file():
        parser.error("selected source has no real application")
    # Harness old=True never passes control-dir. Also keep an unwrapped
    # current API conservative: no probe may silently confer this capability.
    db_identity = observe_database_identity(os.environ["DATABASE_URL"],
                                            require_no_capability=not bool(args.control_dir))
    sys.path.insert(0, str(source))
    wrapper = None
    if args.control_dir:
        if not args.key_file:
            parser.error("checkpoint control requires a private key file")
        root = Path(__file__).resolve().parents[2]
        sys.path.append(str(root))
        # Import test-only controls before production; server module prepends
        # current source, so restore the explicitly selected source afterwards.
        from acceptance.runtime.server import CheckpointController, InjectionMiddleware
        sys.path = [str(source)] + [entry for entry in sys.path if entry != str(source)]
        controller = CheckpointController(args.control_dir, 45)
        checkpoints = importlib.import_module("memory_service_runtime.governed.checkpoints")
        checkpoints.checkpoint = controller.checkpoint
        wrapper = (InjectionMiddleware, args.key_file.read_text().strip(), controller)
    module = importlib.import_module("memory_service_app.main")
    path = Path(module.__file__).resolve()
    if not path.is_relative_to(source):
        raise AssertionError("application imported from the wrong source")
    app = wrapper[0](module.app, wrapper[1], wrapper[2]) if wrapper else module.app
    import uvicorn
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    config = uvicorn.Config(app, host="127.0.0.1", port=port, access_log=False, log_level="warning")

    class Server(uvicorn.Server):
        async def startup(self, sockets=None):
            await super().startup(sockets=sockets)
            if self.started:
                info = {"pid": os.getpid(), "url": f"http://127.0.0.1:{port}",
                        "application_source": str(path), "source_root": str(source),
                        "checkpoint_wrapper": bool(wrapper), "db_identity": db_identity}
                args.ready_file.write_text(json.dumps(info))
                args.ready_file.chmod(0o600)
                print(json.dumps(info), flush=True)
    try:
        asyncio.run(Server(config).serve(sockets=[sock]))
    finally:
        sock.close()


if __name__ == "__main__":
    main()
