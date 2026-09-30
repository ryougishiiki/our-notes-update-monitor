from __future__ import annotations

import hashlib
import hmac
import json
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import HTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from onwatch.notify import (
    CHARTDB_EVENT_TYPE,
    dispatch_repository,
    repository_dispatch_payload,
    send_failure_webhook,
    send_webhook,
)
from tools.webhook_receiver import build_handler


SECRET = "test-shared-secret"


class FakeResponse:
    status = 204

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class NotifyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.processed: set[str] = set()
        self.server = HTTPServer(("127.0.0.1", 0), build_handler(SECRET, self.processed))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_port}/events"

    def tearDown(self) -> None:
        self.server.shutdown()
        self.thread.join(timeout=2)
        self.server.server_close()

    def test_signed_webhook_receiver_rejects_bad_signature_and_expired_timestamp(self) -> None:
        body = json.dumps({"eventId": "event-invalid"}, separators=(",", ":")).encode()
        with self.assertRaises(urllib.error.HTTPError) as bad_signature:
            urllib.request.urlopen(self._request(body, str(int(time.time())), "sha256=bad"))
        self.assertEqual(bad_signature.exception.code, 401)

        expired = str(int(time.time()) - 301)
        signature = self._signature(expired, body)
        with self.assertRaises(urllib.error.HTTPError) as stale:
            urllib.request.urlopen(self._request(body, expired, signature))
        self.assertEqual(stale.exception.code, 401)
        self.assertEqual(self.processed, set())

    def test_signed_webhook_is_accepted_once_and_duplicate_is_acked(self) -> None:
        event = _event()
        with TemporaryDirectory() as temp:
            state = Path(temp) / "state"
            first = send_webhook(
                event,
                url=self.url,
                secret=SECRET,
                public_base_url="https://example.invalid",
                state_dir=state,
            )
            second = send_webhook(
                event,
                url=self.url,
                secret=SECRET,
                public_base_url="https://example.invalid",
                state_dir=state,
            )
        self.assertEqual(first["status"], "sent")
        self.assertEqual(second["status"], "already-sent")
        self.assertEqual(self.processed, {"intl-event-2"})

        body = json.dumps(
            {"event": "our-notes-update", "eventId": "intl-event-2"},
            separators=(",", ":"),
        ).encode()
        timestamp = str(int(time.time()))
        request = self._request(body, timestamp, self._signature(timestamp, body), "intl-event-2")
        with urllib.request.urlopen(request) as response:
            duplicate = json.loads(response.read().decode("utf-8"))
            self.assertEqual(response.status, 200)
            self.assertIn("charset=utf-8", response.headers["Content-Type"])
        self.assertTrue(duplicate["duplicate"])
        self.assertEqual(len(self.processed), 1)

    def test_failure_alert_is_a_separate_signed_event(self) -> None:
        with TemporaryDirectory() as temp:
            state_dir = Path(temp) / "state"
            first = send_failure_webhook(
                status="SOURCE_UNAVAILABLE",
                url=self.url,
                secret=SECRET,
                state_dir=state_dir,
                run_id="run-12",
                run_attempt="1",
                job="probe",
                run_url="https://github.com/owner/repo/actions/runs/12",
            )
            duplicate = send_failure_webhook(
                status="SOURCE_UNAVAILABLE",
                url=self.url,
                secret=SECRET,
                state_dir=state_dir,
                run_id="run-12",
                run_attempt="1",
                job="probe",
                run_url="https://github.com/owner/repo/actions/runs/12",
            )
        self.assertEqual(first["status"], "sent")
        self.assertEqual(duplicate["status"], "already-sent")
        self.assertEqual(self.processed, {"run-12:1:probe:SOURCE_UNAVAILABLE"})
        with self.assertRaises(ValueError):
            send_failure_webhook(
                status="NO_CHANGE",
                url=self.url,
                secret=SECRET,
                state_dir=Path("unused"),
                run_id="run-12",
                run_attempt="1",
                job="probe",
                run_url=None,
            )

    def test_repository_dispatch_payload_is_minimal_and_version_aware(self) -> None:
        payload = repository_dispatch_payload(_event())
        self.assertEqual(payload["event_type"], CHARTDB_EVENT_TYPE)
        self.assertEqual(
            set(payload["client_payload"]),
            {"eventId", "snapshot", "catalogVersion", "catalogHash", "added", "changed", "removed"},
        )
        self.assertEqual(payload["client_payload"]["eventId"], "intl-event-2")
        self.assertEqual(payload["client_payload"]["catalogVersion"], "1.0.0.101")
        self.assertTrue(payload["client_payload"]["added"])

        no_chart_change = _event()
        no_chart_change["charts"] = {"added": [], "changed": [], "removed": []}
        no_chart_change["source"]["catalogVersionAfter"] = "1.0.0.100"
        with TemporaryDirectory() as temp:
            result = dispatch_repository(
                no_chart_change, repository="owner/chartdb", token="test-token", state_dir=Path(temp)
            )
        self.assertEqual(result, {"status": "skipped", "reason": "no chart changes"})

    def test_repository_dispatch_posts_one_deterministic_event(self) -> None:
        with TemporaryDirectory() as temp:
            state_dir = Path(temp)
            with patch("onwatch.notify.urllib.request.urlopen", return_value=FakeResponse()) as send:
                first = dispatch_repository(
                    _event(),
                    repository="ryougishiiki/our-notes-chartdb",
                    token="test-token",
                    state_dir=state_dir,
                )
                second = dispatch_repository(
                    _event(),
                    repository="ryougishiiki/our-notes-chartdb",
                    token="test-token",
                    state_dir=state_dir,
                )
        self.assertEqual(first["status"], "sent")
        self.assertEqual(second["status"], "already-sent")
        self.assertEqual(send.call_count, 1)
        request = send.call_args.args[0]
        self.assertEqual(request.full_url, "https://api.github.com/repos/ryougishiiki/our-notes-chartdb/dispatches")
        posted = json.loads(request.data.decode("utf-8"))
        self.assertEqual(posted, repository_dispatch_payload(_event()))

    def _request(
        self, body: bytes, timestamp: str, signature: str, event_id: str = "event-invalid"
    ) -> urllib.request.Request:
        return urllib.request.Request(
            self.url,
            data=body,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Idempotency-Key": event_id,
                "X-OnWatch-Timestamp": timestamp,
                "X-OnWatch-Signature": signature,
            },
        )

    def _signature(self, timestamp: str, body: bytes) -> str:
        digest = hmac.new(SECRET.encode(), timestamp.encode() + b"." + body, hashlib.sha256).hexdigest()
        return f"sha256={digest}"


def _event() -> dict:
    return {
        "id": "intl-event-2",
        "severity": "CONTENT",
        "source": {
            "catalogBefore": "a",
            "catalogAfter": "b",
            "catalogVersionBefore": "1.0.0.100",
            "catalogVersionAfter": "1.0.0.101",
        },
        "summary": {"assetsAdded": 1, "masterAdded": 3, "chartsAdded": 1},
        "charts": {"added": ["100084/easy"], "changed": ["100084/hard"], "removed": []},
        "snapshot": {"revision": "intl-revision-2"},
    }


if __name__ == "__main__":
    unittest.main()
