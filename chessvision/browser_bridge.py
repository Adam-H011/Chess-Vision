"""Small localhost-only, token-paired browser import endpoint."""
from __future__ import annotations

import json
import secrets
import threading
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from queue import Empty, Queue
from typing import Callable

from .position import Position

MAX_BODY = 16 * 1024
ALLOWED_ORIGIN_PREFIXES = ("moz-extension://", "chrome-extension://")

@dataclass(frozen=True)
class BridgeStatus:
    port: int
    token: str
    connected: bool = False

class BrowserBridge:
    """One-session bridge. It binds only to loopback and accepts explicit posts."""
    def __init__(self, on_position: Callable[[Position], None] | None = None) -> None:
        self.token = secrets.token_urlsafe(32)
        self._queue: Queue[Position] = Queue()
        self._on_position = on_position
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._connected = False

    @property
    def status(self) -> BridgeStatus:
        return BridgeStatus(self._server.server_port if self._server else 0, self.token, self._connected)

    def start(self) -> BridgeStatus:
        if self._server:
            return self.status
        bridge = self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format: str, *args: object) -> None: pass
            def _reply(self, status: int, body: dict[str, object]) -> None:
                raw = json.dumps(body).encode()
                self.send_response(status); self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw))); self.end_headers(); self.wfile.write(raw)
            def do_OPTIONS(self) -> None:
                origin = self.headers.get("Origin", "")
                if origin.startswith(ALLOWED_ORIGIN_PREFIXES):
                    self.send_response(204); self.send_header("Access-Control-Allow-Origin", origin)
                    self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type")
                    self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS"); self.end_headers()
                else: self._reply(403, {"error": "extension origin required"})
            def do_POST(self) -> None:
                origin = self.headers.get("Origin", "")
                if not origin.startswith(ALLOWED_ORIGIN_PREFIXES): return self._reply(403, {"error": "extension origin required"})
                if self.headers.get("Authorization") != f"Bearer {bridge.token}": return self._reply(401, {"error": "invalid session token"})
                length = self.headers.get("Content-Length")
                if not length or not length.isdigit() or int(length) > MAX_BODY: return self._reply(413, {"error": "payload too large"})
                try:
                    data = json.loads(self.rfile.read(int(length)).decode("utf-8"))
                    if not isinstance(data, dict) or set(data) - {"placement", "metadata", "orientation", "mode", "warnings", "confidence"}: raise ValueError("unexpected fields")
                    position = Position.from_browser_payload(data)
                except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc: return self._reply(400, {"error": str(exc)})
                bridge._connected = True; bridge._queue.put(position)
                if bridge._on_position: bridge._on_position(position)
                self.send_response(202); self.send_header("Access-Control-Allow-Origin", origin); self.end_headers()
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True, name="chessvision-browser-bridge"); self._thread.start()
        return self.status

    def get_position(self) -> Position | None:
        try: return self._queue.get_nowait()
        except Empty: return None

    def close(self) -> None:
        if self._server:
            self._server.shutdown(); self._server.server_close(); self._server = None
