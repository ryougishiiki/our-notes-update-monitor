from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

from .config import DEFAULT_CONFIG, load_config
from .io import read_json, write_json
from .notify import notify_latest, send_failure_webhook
from .probe import changed_sources, fetch_probe
from .publish import rebuild_feed
from .http import HttpError, InvalidResponseError
from .scan import IncompleteSnapshotError, run_scan


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="onwatch", description="Our Notes data and resource update tracker")
    parser.add_argument("--version", action="version", version="onwatch 0.1.0")
    subparsers = parser.add_subparsers(dest="command", required=True)

    probe = subparsers.add_parser("probe", help="check lightweight upstream fingerprints")
    _common(probe)
    probe.add_argument("--trigger-scan", action="store_true", help="run a deep scan after a fingerprint change")
    probe.add_argument("--settle-seconds", type=float, default=0, help="wait between stability checks after a change")
    probe.add_argument("--stable-checks", type=int, default=2, help="matching checks required when settling")
    probe.add_argument("--github-output", default=None, help="write changed/reason outputs to a GitHub Actions output file")
    probe.add_argument("--json", action="store_true", help="print machine-readable output")
    probe.set_defaults(func=cmd_probe)

    scan = subparsers.add_parser("scan", help="build, diff, and publish a complete snapshot")
    _common(scan)
    scan.add_argument("--github-output", default=None, help="write scan outputs to a GitHub Actions output file")
    scan.add_argument("--json", action="store_true", help="print machine-readable output")
    scan.set_defaults(func=cmd_scan)

    feed = subparsers.add_parser("rebuild-feed", help="rebuild static feed files from stored events")
    feed.add_argument("--root", default=".")
    feed.add_argument("--json", action="store_true")
    feed.set_defaults(func=cmd_feed)

    notify = subparsers.add_parser("notify", help="send the latest event to configured integrations")
    notify.add_argument("--root", default=".")
    notify.add_argument("--json", action="store_true")
    notify.set_defaults(func=cmd_notify)

    failure = subparsers.add_parser("notify-failure", help="send a signed workflow failure alert")
    failure.add_argument("--root", default=".")
    failure.add_argument(
        "--status",
        required=True,
        choices=("SCAN_FAILED", "SOURCE_UNAVAILABLE", "SCHEMA_INVALID", "PUBLISH_FAILED"),
    )
    failure.add_argument("--json", action="store_true")
    failure.set_defaults(func=cmd_notify_failure)
    return parser


def _common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--server", default=None)
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    parser.add_argument("--root", default=".")


def cmd_probe(args: argparse.Namespace) -> int:
    config = load_config(Path(args.config), args.server)
    root = Path(args.root).resolve()
    previous = read_json(root / "state" / "scanned.json")
    current_pointer = read_json(root / "state" / "current.json", {}) or {}
    current = fetch_probe(config)
    reasons = changed_sources(previous, current)
    current_revision = current_pointer.get("current")
    if not current_revision or not (root / "snapshots" / str(current_revision)).is_dir():
        reasons = sorted(set(reasons + ["snapshot-baseline-missing"]))
    changed = bool(reasons)
    public_probe = _public_probe(current)
    write_json(root / "state" / "probe.json", public_probe)
    status = read_json(root / "state" / "status.json", {}) or {}
    provenance = current.get("provenance", {})
    status.update(
        {
            "healthy": bool(status.get("healthy", False)),
            "lastProbe": current["checkedAt"],
            "lastSuccessfulScan": status.get("lastSuccessfulScan"),
            "currentSnapshot": current_pointer.get("current"),
            "latestEvent": status.get("latestEvent"),
            "catalogVersionSource": provenance.get("catalogVersionSource", "config"),
            "gameVersionAvailable": current["sources"].get("gameVersion") is not None,
            "masterAuthority": provenance.get("masterAuthority", "unavailable"),
            "source": {
                "catalog": "ok",
                "master": "ok",
                "gameVersion": "ok" if current["sources"].get("gameVersion") is not None else "unavailable",
            },
        }
    )
    write_json(root / "state" / "status.json", status)
    write_json(root / "site" / "api" / "status.json", status)

    if changed and args.settle_seconds > 0:
        if args.stable_checks < 1:
            raise ValueError("--stable-checks must be at least 1")
        stable = 0
        prior_sources = current["sources"]
        while stable < args.stable_checks:
            time.sleep(args.settle_seconds)
            candidate = fetch_probe(config)
            if candidate["sources"] == prior_sources:
                stable += 1
            else:
                stable = 0
            prior_sources = candidate["sources"]
            current = candidate

    output: dict[str, Any] = {
        "changed": changed,
        "changedSources": reasons,
        "probe": public_probe,
    }
    if changed and args.trigger_scan:
        output["scan"] = run_scan(root, config, force=True)
    _write_github_output(args.github_output, changed, reasons)
    _print(output, args.json)
    return 0


def cmd_scan(args: argparse.Namespace) -> int:
    config = load_config(Path(args.config), args.server)
    result = run_scan(Path(args.root), config, force=True)
    _write_scan_github_output(args.github_output, result, config.server)
    _print(result, args.json)
    return 0


def cmd_feed(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve()
    status = read_json(root / "state" / "status.json")
    result = rebuild_feed(root, status)
    _print(result["latest"], args.json)
    return 0


def cmd_notify(args: argparse.Namespace) -> int:
    _print(notify_latest(Path(args.root).resolve()), args.json)
    return 0


def cmd_notify_failure(args: argparse.Namespace) -> int:
    run_id = os.environ.get("GITHUB_RUN_ID", "local")
    run_attempt = os.environ.get("GITHUB_RUN_ATTEMPT", "1")
    job = os.environ.get("GITHUB_JOB", "manual")
    server_url = os.environ.get("GITHUB_SERVER_URL", "https://github.com").rstrip("/")
    repository = os.environ.get("GITHUB_REPOSITORY", "")
    run_url = f"{server_url}/{repository}/actions/runs/{run_id}" if repository else None
    result = send_failure_webhook(
        status=args.status,
        url=os.environ.get("ONWATCH_WEBHOOK_URL"),
        secret=os.environ.get("ONWATCH_WEBHOOK_SECRET"),
        state_dir=Path(args.root).resolve() / "state",
        run_id=run_id,
        run_attempt=run_attempt,
        job=job,
        run_url=run_url,
    )
    _print(result, args.json)
    return 0


def _public_probe(probe: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in probe.items() if not key.startswith("_")}


def _write_github_output(path: str | None, changed: bool, reasons: list[str]) -> None:
    if not path:
        return
    with Path(path).open("a", encoding="utf-8", newline="\n") as stream:
        stream.write(f"changed={'true' if changed else 'false'}\n")
        stream.write(f"changed_sources={','.join(reasons)}\n")


def _write_scan_github_output(path: str | None, result: dict[str, Any], server: str) -> None:
    if not path:
        return
    before = result.get("previousSnapshot")
    after = result.get("snapshotRevision")
    short = lambda value: str(value).rsplit("-", 1)[-1][:8] if value else "none"
    with Path(path).open("a", encoding="utf-8", newline="\n") as stream:
        stream.write(f"changed={'true' if result.get('changed') else 'false'}\n")
        stream.write(f"event_id={result.get('eventId') or ''}\n")
        stream.write(f"before={short(before)}\n")
        stream.write(f"after={short(after)}\n")
        stream.write(f"server={server}\n")


def _failure_status(error: Exception) -> str:
    if isinstance(error, InvalidResponseError):
        return "SCHEMA_INVALID"
    if isinstance(error, HttpError):
        return "SOURCE_UNAVAILABLE"
    if isinstance(error, (IncompleteSnapshotError, UnicodeError, json.JSONDecodeError, ValueError)):
        return "SCHEMA_INVALID"
    return "SCAN_FAILED"


def _print(value: Any, machine: bool) -> None:
    if machine:
        print(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2))
    else:
        if "probe" in value:
            state = "change detected" if value["changed"] else "no changes"
            print(f"Probe: {state}")
            if value["changedSources"]:
                print("Changed sources: " + ", ".join(value["changedSources"]))
            if value.get("scan"):
                print("Scan: " + json.dumps(value["scan"], ensure_ascii=False, sort_keys=True))
        else:
            print(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2))


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except Exception as error:
        output_path = os.environ.get("GITHUB_OUTPUT")
        if output_path and getattr(args, "command", None) in {"probe", "scan"}:
            with Path(output_path).open("a", encoding="utf-8", newline="\n") as stream:
                stream.write(f"failure_status={_failure_status(error)}\n")
        print(f"onwatch: {type(error).__name__}: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
