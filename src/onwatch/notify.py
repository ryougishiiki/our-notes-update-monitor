from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from .hashing import canonical_json
from .http import HttpError
from .io import read_json, utc_now, write_json


EVENT_TYPE = "our-notes-update"
CHARTDB_EVENT_TYPE = "our-notes-chart-update"
FAILURE_EVENT_TYPE = "our-notes-update-failure"


def latest_event(root: Path) -> dict[str, Any] | None:
    latest = read_json(root / "feed" / "latest.json", {}) or {}
    event_id = latest.get("latest")
    if not event_id:
        return None
    path = root / "events" / f"{event_id}.json"
    event = read_json(path)
    if not isinstance(event, dict) or event.get("schema") != "our-notes-update-event/1":
        raise ValueError(f"latest feed points to an invalid event: {path}")
    return event


def repository_dispatch_payload(event: dict[str, Any]) -> dict[str, Any]:
    return {
        "event_type": CHARTDB_EVENT_TYPE,
        "client_payload": {
            "eventId": event["id"],
            "snapshot": event["snapshot"]["revision"],
            "catalogVersion": event["source"].get("catalogVersionAfter"),
            "catalogHash": event["source"].get("catalogAfter"),
            "added": event["charts"]["added"],
            "changed": event["charts"]["changed"],
            "removed": event["charts"]["removed"],
        },
    }


def dispatch_repository(
    event: dict[str, Any], *, repository: str | None, token: str | None, state_dir: Path
) -> dict[str, Any]:
    payload = repository_dispatch_payload(event)
    has_chart_changes = any(
        payload["client_payload"][kind] for kind in ("added", "changed", "removed")
    )
    has_version_change = (
        event["source"].get("catalogVersionBefore")
        != event["source"].get("catalogVersionAfter")
    )
    if not has_chart_changes and not has_version_change:
        return {"status": "skipped", "reason": "no chart changes"}
    if not repository:
        return {"status": "skipped", "reason": "repository is not configured"}
    if not token:
        raise ValueError("ONWATCH_GITHUB_TOKEN is required when ONWATCH_CHARTDB_REPOSITORY is set")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
        raise ValueError("repository must be formatted as owner/repository")

    receipt = state_dir / "notifications" / "repository-dispatch" / f"{event['id']}.json"
    if receipt.is_file():
        return {"status": "already-sent", "eventId": event["id"]}

    body = canonical_json(payload)
    request = urllib.request.Request(
        f"https://api.github.com/repos/{repository}/dispatches",
        data=body,
        method="POST",
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "our-notes-update-monitor/0.1",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            if response.status not in (200, 204):
                raise HttpError(f"repository_dispatch returned HTTP {response.status}")
    except urllib.error.HTTPError as error:
        raise HttpError(f"repository_dispatch returned HTTP {error.code}") from error
    write_json(receipt, {"eventId": event["id"], "sentAt": utc_now(), "status": "acknowledged"})
    return {"status": "sent", "eventId": event["id"]}


def send_webhook(
    event: dict[str, Any],
    *,
    url: str | None,
    secret: str | None,
    public_base_url: str | None,
    state_dir: Path,
) -> dict[str, Any]:
    if not url:
        return {"status": "skipped", "reason": "webhook URL is not configured"}
    if not secret:
        raise ValueError("ONWATCH_WEBHOOK_SECRET is required when ONWATCH_WEBHOOK_URL is set")

    receipt = state_dir / "notifications" / "webhook" / f"{event['id']}.json"
    if receipt.is_file():
        return {"status": "already-sent", "eventId": event["id"]}

    report_path = f"reports/{event['id']}.md"
    report_url = f"{public_base_url.rstrip('/')}/{report_path}" if public_base_url else report_path
    payload = {
        "event": EVENT_TYPE,
        "eventId": event["id"],
        "reportUrl": report_url,
        "severity": event["severity"],
        "summary": event["summary"],
    }
    _post_signed(url, secret, payload, event["id"], receipt)
    return {"status": "sent", "eventId": event["id"]}


def send_failure_webhook(
    *,
    status: str,
    url: str | None,
    secret: str | None,
    state_dir: Path,
    run_id: str,
    run_attempt: str,
    job: str,
    run_url: str | None,
) -> dict[str, Any]:
    allowed = {"SCAN_FAILED", "SOURCE_UNAVAILABLE", "SCHEMA_INVALID", "PUBLISH_FAILED"}
    if status not in allowed:
        raise ValueError(f"failure status must be one of: {', '.join(sorted(allowed))}")
    if not url:
        return {"status": "skipped", "reason": "webhook URL is not configured"}
    if not secret:
        raise ValueError("ONWATCH_WEBHOOK_SECRET is required when ONWATCH_WEBHOOK_URL is set")

    event_id = f"{run_id}:{run_attempt}:{job}:{status}"
    receipt = state_dir / "notifications" / "failures" / f"{event_id.replace(':', '-')}.json"
    if receipt.is_file():
        return {"status": "already-sent", "eventId": event_id}
    payload = {
        "event": FAILURE_EVENT_TYPE,
        "eventId": event_id,
        "status": status,
        "runUrl": run_url,
    }
    _post_signed(url, secret, payload, event_id, receipt)
    return {"status": "sent", "eventId": event_id}


def _post_signed(
    url: str, secret: str, payload: dict[str, Any], idempotency_key: str, receipt: Path
) -> None:
    body = canonical_json(payload)
    timestamp = str(int(time.time()))
    signature = hmac.new(
        secret.encode("utf-8"), timestamp.encode("ascii") + b"." + body, hashlib.sha256
    ).hexdigest()
    request = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json; charset=utf-8",
            "User-Agent": "our-notes-update-monitor/0.1",
            "Idempotency-Key": idempotency_key,
            "X-OnWatch-Timestamp": timestamp,
            "X-OnWatch-Signature": f"sha256={signature}",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            if response.status < 200 or response.status >= 300:
                raise HttpError(f"webhook returned HTTP {response.status}")
    except urllib.error.HTTPError as error:
        raise HttpError(f"webhook returned HTTP {error.code}") from error
    write_json(receipt, {"eventId": idempotency_key, "sentAt": utc_now(), "status": "acknowledged"})


def notify_latest(root: Path) -> dict[str, Any]:
    event = latest_event(root)
    if event is None:
        return {"eventId": None, "repositoryDispatch": "skipped", "webhook": "skipped"}
    state_dir = root / "state"
    dispatch = dispatch_repository(
        event,
        repository=os.environ.get("ONWATCH_CHARTDB_REPOSITORY"),
        token=os.environ.get("ONWATCH_GITHUB_TOKEN"),
        state_dir=state_dir,
    )
    webhook = send_webhook(
        event,
        url=os.environ.get("ONWATCH_WEBHOOK_URL"),
        secret=os.environ.get("ONWATCH_WEBHOOK_SECRET"),
        public_base_url=os.environ.get("ONWATCH_PUBLIC_BASE_URL"),
        state_dir=state_dir,
    )
    return {"eventId": event["id"], "repositoryDispatch": dispatch, "webhook": webhook}
