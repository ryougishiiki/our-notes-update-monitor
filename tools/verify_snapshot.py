from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from onwatch.hashing import sha256_json
from onwatch.io import read_json
from onwatch.snapshot import SNAPSHOT_SCHEMA, load_snapshot_dir


def verify(root: Path) -> dict[str, int | str | None]:
    snapshots = root / "snapshots"
    count = 0
    for directory in sorted(snapshots.iterdir()) if snapshots.exists() else []:
        if not directory.is_dir():
            continue
        snapshot = load_snapshot_dir(directory)
        meta = snapshot["meta"]
        if meta.get("schema") != SNAPSHOT_SCHEMA:
            raise ValueError(f"unsupported snapshot schema in {directory}")
        if meta.get("revision") != directory.name:
            raise ValueError(f"revision path mismatch in {directory}")
        keys = [str(asset.get("key") or "") for asset in snapshot["assets"]]
        if any(not key for key in keys) or len(keys) != len(set(keys)):
            raise ValueError(f"asset keys are missing or duplicated in {directory}")
        expected_charts = [asset for asset in snapshot["assets"] if asset.get("category") == "music_score"]
        if snapshot["charts"] != expected_charts:
            raise ValueError(f"chart index disagrees with assets in {directory}")
        hashes = {
            name: sha256_json(table["rows"])
            for name, table in sorted(snapshot["master"].items())
        }
        master_revision = sha256_json(hashes)
        if master_revision != meta.get("masterRevision"):
            raise ValueError(f"Master revision mismatch in {directory}")
        content = {
            "schema": SNAPSHOT_SCHEMA,
            "server": meta["server"],
            "version": snapshot["version"],
            "catalog": snapshot["catalog"],
            "master": snapshot["master"],
            "assets": snapshot["assets"],
            "masterRevision": master_revision,
        }
        if sha256_json(content) != meta.get("contentSha256"):
            raise ValueError(f"content hash mismatch in {directory}")
        revision_identity = sha256_json(
            {
                "server": meta["server"],
                "contentSha256": meta["contentSha256"],
                "parent": meta.get("parent"),
            }
        )[:16]
        if meta["revision"] != f"{meta['server']}-{revision_identity}":
            raise ValueError(f"revision identity mismatch in {directory}")
        count += 1
    event_ids = []
    events_dir = root / "events"
    if events_dir.exists():
        events = []
        for path in events_dir.glob("*.json"):
            event = json.loads(path.read_text("utf-8"))
            if event.get("schema") != "our-notes-update-event/1" or event.get("id") != path.stem:
                raise ValueError(f"invalid event metadata in {path}")
            report = root / "reports" / f"{path.stem}.md"
            if not report.is_file():
                raise ValueError(f"event report is missing for {path.stem}")
            events.append(event)
        events.sort(key=lambda item: (item.get("detectedAt", ""), item.get("id", "")), reverse=True)
        event_ids = [item["id"] for item in events]
        for relative in (Path("feed") / "index.json", Path("site") / "api" / "events" / "index.json"):
            index_path = root / relative
            if index_path.is_file():
                index = json.loads(index_path.read_text("utf-8"))
                if index.get("count") != len(events):
                    raise ValueError(f"event index count mismatch in {index_path}")
                indexed_ids = [item.get("id") for item in index.get("events", [])]
                if indexed_ids != event_ids:
                    raise ValueError(f"event index contents mismatch in {index_path}")
    current = read_json(root / "state" / "current.json", {})
    if current and current.get("current"):
        directory = snapshots / current["current"]
        if not directory.is_dir():
            raise ValueError("current pointer does not reference a snapshot")
        current_snapshot = load_snapshot_dir(directory)
        if current_snapshot["meta"].get("revision") != current["current"]:
            raise ValueError("current pointer and snapshot metadata disagree")
    return {
        "snapshotsVerified": count,
        "eventsVerified": len(event_ids),
        "current": current.get("current") if current else None,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=str(ROOT))
    args = parser.parse_args()
    result = verify(Path(args.root).resolve())
    print(
        f"Verified {result['snapshotsVerified']} snapshot(s) and {result['eventsVerified']} event(s); current={result['current']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
