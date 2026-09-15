"""One loopback-only request: forward to Runtime, then discard its HTTP response."""
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, HTTPServer
import socket
from threading import Thread
from urllib.parse import urlsplit

import httpx


@contextmanager
def drop_response(upstream):
    parsed = urlsplit(upstream)
    if parsed.scheme != "http" or parsed.hostname != "127.0.0.1" or parsed.username or parsed.password:
        raise ValueError("Fault proxy requires a literal loopback Runtime")
    observed = {}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_POST(self):
            if self.path != "/v1/workspace-scenes/events":
                self.send_error(404)
                return
            body = self.rfile.read(int(self.headers["Content-Length"]))
            with httpx.Client(trust_env=False, timeout=35) as client:
                response = client.post(upstream + self.path, content=body,
                    headers={"Authorization": self.headers["Authorization"], "Content-Type": "application/json"})
                observed["status"] = response.status_code
            # Upstream has returned after the database transaction committed.
            # No response status, headers or body reaches the downstream client.
            self.connection.shutdown(socket.SHUT_RDWR)
            self.connection.close()
            self.close_connection = True

    server = HTTPServer(("127.0.0.1", 0), Handler)
    server.timeout = 40
    thread = Thread(target=server.handle_request, daemon=True)
    thread.start()
    try:
        yield "http://127.0.0.1:" + str(server.server_port), observed
    finally:
        thread.join(timeout=45)
        server.server_close()
        if thread.is_alive():
            raise AssertionError("Fault proxy did not finish its single request")
