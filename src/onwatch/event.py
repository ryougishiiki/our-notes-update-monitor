from __future__ import annotations

from typing import Any

from .diff import charts_from_asset_diff
from .hashing import sha256_json
from .severity import classify_severity


def build_event(
    *,
    server: str,
    detected_at: str,
    before_snapshot: dict[str, Any],
    after_snapshot: dict[str, Any],
    asset_diff: dict[str, list[dict[str, Any]]],
    master_diff: list[dict[str, Any]],
) -> dict[str, Any]:
    old_meta = before_snapshot["meta"]
    new_meta = after_snapshot["meta"]
    charts = charts_from_asset_diff(asset_diff)
    song_changes = _song_changes(master_diff)
    counts = {
        "assetsAdded": len(asset_diff["added"]),
        "assetsChanged": len(asset_diff["changed"]),
        "assetsRemoved": len(asset_diff["removed"]),
        "masterAdded": sum(change["type"] == "added" for change in master_diff),
        "masterChanged": sum(change["type"] == "changed" for change in master_diff),
        "masterRemoved": sum(change["type"] == "removed" for change in master_diff),
        "chartsAdded": len(charts["added"]),
        "chartsChanged": len(charts["changed"]),
        "chartsRemoved": len(charts["removed"]),
        "songsAdded": len(song_changes["added"]),
        "songsChanged": len(song_changes["changed"]),
        "songsRemoved": len(song_changes["removed"]),
    }
    categories: dict[str, dict[str, int]] = {}
    for change_type in ("added", "changed", "removed"):
        for record in asset_diff[change_type]:
            asset = record.get("after") or record.get("before") or {}
            category = str(asset.get("category") or "unknown")
            categories.setdefault(category, {"added": 0, "changed": 0, "removed": 0})
            categories[category][change_type] += 1
    source = {
        "gameVersionBefore": _version(before_snapshot, "gameVersion"),
        "gameVersionAfter": _version(after_snapshot, "gameVersion"),
        "catalogVersionBefore": _version(before_snapshot, "catalogVersion"),
        "catalogVersionAfter": _version(after_snapshot, "catalogVersion"),
        "catalogVersionSourceBefore": _version(before_snapshot, "catalogVersionSource"),
        "catalogVersionSourceAfter": _version(after_snapshot, "catalogVersionSource"),
        "catalogBefore": old_meta.get("catalogHash"),
        "catalogAfter": new_meta.get("catalogHash"),
        "masterBefore": old_meta.get("masterRevision"),
        "masterAfter": new_meta.get("masterRevision"),
        "masterIndexBefore": old_meta.get("masterIndexRevision"),
        "masterIndexAfter": new_meta.get("masterIndexRevision"),
        "masterSource": new_meta.get("source", {}).get("master"),
        "masterAuthority": new_meta.get("source", {}).get("masterAuthority"),
        "masterVersionBefore": _version(before_snapshot, "masterVersion"),
        "masterVersionAfter": _version(after_snapshot, "masterVersion"),
        "masterResourceVersionBefore": _version(before_snapshot, "masterResourceVersion"),
        "masterResourceVersionAfter": _version(after_snapshot, "masterResourceVersion"),
        "masterManifestSha256Before": _version(before_snapshot, "masterManifestSha256"),
        "masterManifestSha256After": _version(after_snapshot, "masterManifestSha256"),
        "mirrorStatus": new_meta.get("source", {}).get("mirrorStatus"),
        "mirrorRevision": new_meta.get("source", {}).get("mirrorRevision"),
        "catalogMasterAlignment": after_snapshot.get("catalog", {}).get("masterAlignment"),
        "resourceManifestBefore": old_meta.get("resourceManifestRevision"),
        "resourceManifestAfter": new_meta.get("resourceManifestRevision"),
    }
    transition_id = sha256_json(
        {"server": server, "before": old_meta.get("revision"), "after": new_meta["revision"]}
    )[:16]
    event = {
        "schema": "our-notes-update-event/1",
        "id": f"{server}-{transition_id}",
        "server": server,
        "detectedAt": detected_at,
        "severity": "INFO",
        "source": source,
        "summary": counts,
        "categories": categories,
        "charts": charts,
        "songs": song_changes,
        "assets": asset_diff,
        "masterChanges": master_diff,
        "snapshot": {"revision": new_meta["revision"], "path": f"snapshots/{new_meta['revision']}"},
    }
    event["severity"] = classify_severity(event)
    return event


def event_fingerprint(event: dict[str, Any]) -> str:
    return sha256_json(event)


def _version(snapshot: dict[str, Any], key: str) -> Any:
    return snapshot.get("version", {}).get(key)


def _song_changes(master_diff: list[dict[str, Any]]) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {kind: [] for kind in ("added", "changed", "removed")}
    for change in master_diff:
        if change.get("table") == "MasterLiveMusic" and change.get("type") in result:
            result[change["type"]].append(str(change.get("key")))
    for keys in result.values():
        keys.sort(key=lambda value: (0, int(value)) if value.isdigit() else (1, value))
    return result
