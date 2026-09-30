from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any


MAX_BODY_BYTES = 1_048_576
REPLAY_WINDOW_SECONDS = 300


def build_handler(secret: str, processed: set[str] | None = None):
    seen = processed if processed is not None else set()

    class Receiver(BaseHTTPRequestHandler):
        processed = seen

        def do_POST(self) -> None:
            if self.path != "/events":
                self._reply(404, {"error": "not found"})
                return
            try:
                size = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                self._reply(400, {"error": "invalid content length"})
                return
            if size <= 0:
                self._reply(400, {"error": "empty body"})
                return
            if size > MAX_BODY_BYTES:
                self._reply(413, {"error": "body too large"})
                return
            body = self.rfile.read(size)
            timestamp = self.headers.get("X-OnWatch-Timestamp", "")
            supplied = self.headers.get("X-OnWatch-Signature", "")
            try:
                timestamp_value = int(timestamp)
            except ValueError:
                self._reply(401, {"error": "invalid timestamp"})
                return
            if abs(int(time.time()) - timestamp_value) > REPLAY_WINDOW_SECONDS:
                self._reply(401, {"error": "expired timestamp"})
                return
            expected = "sha256=" + hmac.new(
                secret.encode("utf-8"), timestamp.encode("ascii") + b"." + body, hashlib.sha256
            ).hexdigest()
            if not hmac.compare_digest(supplied, expected):
                self._reply(401, {"error": "invalid signature"})
                return
            try:
                payload: Any = json.loads(body.decode("utf-8"))
            except (UnicodeError, json.JSONDecodeError):
                self._reply(400, {"error": "invalid JSON"})
                return
            event_id = payload.get("eventId") if isinstance(payload, dict) else None
            if not isinstance(event_id, str) or not event_id:
                self._reply(400, {"error": "missing eventId"})
                return
            if self.headers.get("Idempotency-Key") != event_id:
                self._reply(400, {"error": "idempotency key mismatch"})
                return
            if event_id in self.processed:
                self._reply(200, {"accepted": True, "duplicate": True})
                return
            self.processed.add(event_id)
            self._reply(202, {"accepted": True, "duplicate": False})

        def _reply(self, status: int, value: dict[str, Any]) -> None:
            body = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, _format: str, *_args) -> None:
            return

    return Receiver


def main() -> int:
    parser = argparse.ArgumentParser(description="Local signed webhook contract receiver")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    secret = os.environ.get("ONWATCH_WEBHOOK_SECRET")
    if not secret:
        parser.error("set ONWATCH_WEBHOOK_SECRET in the environment")
    server = HTTPServer((args.host, args.port), build_handler(secret))
    print(f"Local receiver listening at http://{args.host}:{server.server_port}/events")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        return 0
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
