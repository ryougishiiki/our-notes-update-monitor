from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .config import ServerConfig
from .diff import diff_assets, diff_master
from .event import build_event
from .hashing import sha256_bytes
from .http import fetch_bytes
from .io import read_json, utc_now, write_json
from .lock import FileLock
from .normalize.catalog import normalize_catalog
from .probe import changed_sources, fetch_probe
from .publish import rebuild_feed
from .report import render_report
from .snapshot import load_current_snapshot, make_snapshot, write_snapshot
from .sources.addressables import parse_catalog
from .sources.master import compare_master_mirror, fetch_official_master_tables


class IncompleteSnapshotError(RuntimeError):
    pass


def run_scan(root: Path, config: ServerConfig, *, force: bool = True) -> dict[str, Any]:
    root = root.resolve()
    with FileLock(root / "state" / "scan.lock"):
        current_pointer = read_json(root / "state" / "current.json", {})
        current_snapshot = load_current_snapshot(root, current_pointer)
        last_probe = read_json(root / "state" / "scanned.json")
        initial_probe = fetch_probe(config)
        changed = changed_sources(last_probe, initial_probe)
        if current_snapshot is not None and not force and not changed:
            return {
                "changed": False,
                "reason": "source fingerprints unchanged",
                "snapshotRevision": current_snapshot["meta"]["revision"],
                "eventId": None,
            }

        catalog_hash = initial_probe["sources"]["catalogHash"]
        if (
            current_snapshot is not None
            and current_snapshot["catalog"].get("catalogHash") == catalog_hash
            and current_snapshot["catalog"].get("catalogSha256")
        ):
            assets = current_snapshot["assets"]
            catalog_sha256 = current_snapshot["catalog"]["catalogSha256"]
        else:
            catalog_version = str(initial_probe["sources"]["catalogVersion"])
            catalog_bytes = fetch_bytes(
                config.catalog_url_for(catalog_version), timeout=config.request_timeout_seconds
            )
            reader = parse_catalog(catalog_bytes, config.remote_root, config.remote_root)
            assets = normalize_catalog(reader)
            catalog_sha256 = sha256_bytes(catalog_bytes)

        if (
            current_snapshot is not None
            and current_snapshot["meta"].get("masterIndexRevision")
            == initial_probe["sources"].get("masterRevision")
            and all(name in current_snapshot["master"] for name in (
                "MasterLiveMusic", "MasterLiveMusicScore", "MasterEvent", "MasterCharacter"
            ))
        ):
            tables = {
                name: current_snapshot["master"][name]
                for name in ("MasterLiveMusic", "MasterLiveMusicScore", "MasterEvent", "MasterCharacter")
            }
        else:
            tables = fetch_official_master_tables(
                config,
                str(initial_probe["sources"]["masterVersion"]),
                str(initial_probe["sources"]["masterManifestSha256"]),
            )

        try:
            mirror_comparison, _mirror_music_catalog = compare_master_mirror(config, tables)
        except Exception as error:
            mirror_comparison = {
                "status": "MIRROR_UNAVAILABLE",
                "error": f"{type(error).__name__}: {error}",
            }

        # A source changing during download means the collected pieces may not
        # describe one coherent revision. Leave the pointer untouched and retry
        # on the next probe.
        final_probe = fetch_probe(config)
        if initial_probe["sources"] != final_probe["sources"]:
            raise IncompleteSnapshotError(
                "upstream fingerprints changed during deep scan; snapshot was discarded"
            )

        _validate_complete(assets, tables, current_snapshot, config)
        parent = current_snapshot["meta"]["revision"] if current_snapshot else None
        catalog_master_alignment = _catalog_master_alignment(assets, tables)
        snapshot = make_snapshot(
            server=config.server,
            probe=_public_probe(final_probe),
            catalog_sha256=catalog_sha256,
            assets=assets,
            tables=tables,
            parent=parent,
            mirror_comparison=mirror_comparison,
            catalog_master_alignment=catalog_master_alignment,
        )
        revision = snapshot["meta"]["revision"]
        if (
            current_snapshot
            and current_snapshot["meta"].get("contentSha256")
            == snapshot["meta"].get("contentSha256")
            and not changed
        ):
            revision = current_snapshot["meta"]["revision"]
            return {
                "changed": False,
                "reason": "normalized snapshot unchanged",
                "snapshotRevision": revision,
                "previousSnapshot": revision,
                "eventId": None,
            }

        write_snapshot(root, snapshot)
        if current_snapshot is None:
            _advance_state(root, revision, None, final_probe)
            status = _status(final_probe, None, revision)
            rebuilt = rebuild_feed(root, status)
            return {
                "changed": True,
                "baseline": True,
                "snapshotRevision": revision,
                "previousSnapshot": None,
                "eventId": None,
                "feed": rebuilt["latest"],
            }

        assets_diff = diff_assets(current_snapshot["assets"], snapshot["assets"])
        master_changes = diff_master(current_snapshot["master"], snapshot["master"])
        event = build_event(
            server=config.server,
            detected_at=final_probe["checkedAt"],
            before_snapshot=current_snapshot,
            after_snapshot=snapshot,
            asset_diff=assets_diff,
            master_diff=master_changes,
        )
        event_path = root / "events" / f"{event['id']}.json"
        report_path = root / "reports" / f"{event['id']}.md"
        existing_event = read_json(event_path)
        if existing_event is not None:
            if _event_identity(existing_event) != _event_identity(event):
                raise ValueError(f"event ID collision at {event_path}")
            event = existing_event
        else:
            write_json(event_path, event)
        if not report_path.exists():
            report_path.parent.mkdir(parents=True, exist_ok=True)
            report_path.write_text(render_report(event, config.display_timezone), encoding="utf-8")

        _advance_state(root, revision, current_snapshot["meta"]["revision"], final_probe)
        status = _status(final_probe, event["id"], revision)
        rebuilt = rebuild_feed(root, status)
        return {
            "changed": True,
            "baseline": False,
            "snapshotRevision": revision,
            "previousSnapshot": current_snapshot["meta"]["revision"],
            "eventId": event["id"],
            "severity": event["severity"],
            "summary": event["summary"],
            "feed": rebuilt["latest"],
        }


def _advance_state(root: Path, revision: str, parent: str | None, probe: dict[str, Any]) -> None:
    write_json(root / "state" / "current.json", {"current": revision, "previous": parent})
    _write_probe_state(root, probe)


def _write_probe_state(root: Path, probe: dict[str, Any]) -> None:
    public_probe = _public_probe(probe)
    write_json(root / "state" / "probe.json", public_probe)
    write_json(root / "state" / "scanned.json", public_probe)


def _public_probe(probe: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in probe.items() if not key.startswith("_")}


def _status(
    probe: dict[str, Any], latest_event: str | None, current_snapshot: str
) -> dict[str, Any]:
    provenance = probe.get("provenance", {})
    sources = probe.get("sources", {})
    return {
        "healthy": True,
        "lastProbe": probe.get("checkedAt"),
        "lastSuccessfulScan": probe.get("checkedAt"),
        "currentSnapshot": current_snapshot,
        "latestEvent": latest_event,
        "catalogVersionSource": provenance.get("catalogVersionSource", "config"),
        "catalogVersionConfiguredFloor": sources.get("catalogVersionConfiguredFloor"),
        "catalogVersionResolved": sources.get("catalogVersionResolved", sources.get("catalogVersion")),
        "gameVersionAvailable": sources.get("gameVersion") is not None,
        "masterAuthority": provenance.get("masterAuthority", "unavailable"),
        "masterVersion": sources.get("masterVersion"),
        "masterResourceVersion": sources.get("masterResourceVersion"),
        "masterManifestSha256": sources.get("masterManifestSha256"),
        "source": {
            "catalog": "ok",
            "master": "ok",
            "gameVersion": "ok" if sources.get("gameVersion") is not None else "unavailable",
        },
    }


def _validate_complete(
    assets: list[dict[str, Any]],
    tables: dict[str, dict[str, Any]],
    previous: dict[str, Any] | None,
    config: ServerConfig,
) -> None:
    if not assets:
        raise IncompleteSnapshotError("catalog is empty; refusing to interpret it as deletions")
    required_tables = ("MasterLiveMusic", "MasterLiveMusicScore", "MasterCharacter")
    for name in required_tables:
        table = tables.get(name)
        if not isinstance(table, dict) or not table.get("rows"):
            raise IncompleteSnapshotError(f"required Master table {name} is empty or missing")
    if "MasterEvent" not in tables:
        raise IncompleteSnapshotError("MasterEvent source was not returned")

    if previous is None:
        return
    old_count = len(previous.get("assets", []))
    if old_count and len(assets) * 2 < old_count:
        raise IncompleteSnapshotError(
            f"catalog asset count fell from {old_count} to {len(assets)}; refusing a likely partial snapshot"
        )
    old_tables = previous.get("master", {})
    for name, old_table in old_tables.items():
        if name == "MusicCatalog":
            # Retired mirror-derived display index; it is not official Master data.
            continue
        old_rows = old_table.get("rows", {})
        new_rows = tables.get(name, {}).get("rows", {})
        if old_rows and not new_rows and name not in config.allow_empty_tables:
            raise IncompleteSnapshotError(
                f"Master table {name} became empty; refusing to interpret it as row removals"
            )


def _event_identity(event: dict[str, Any]) -> tuple[Any, ...]:
    return (
        event.get("id"),
        event.get("server"),
        event.get("source"),
        event.get("summary"),
        event.get("categories"),
        event.get("charts"),
        event.get("songs"),
        event.get("assets"),
        event.get("masterChanges"),
        event.get("snapshot"),
    )


def _catalog_master_alignment(
    assets: list[dict[str, Any]], tables: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    scores = tables["MasterLiveMusicScore"]["rows"]
    songs = tables["MasterLiveMusic"]["rows"]
    referenced: set[str] = set()
    for song in songs.values():
        for field in ("_easyID", "_normalID", "_hardID", "_expertID", "_specialID"):
            score = scores.get(str(song.get(field) or ""))
            name = str(score.get("_musicScoreTextFileName") or "") if score else ""
            if name:
                referenced.add(name)
    chart_assets = [asset for asset in assets if asset.get("category") == "music_score"]

    def matches(chart_file: str, asset: dict[str, Any]) -> bool:
        token = chart_file.replace("/", "_").casefold()
        return any(
            token in str(asset.get(field) or "").replace("/", "_").casefold()
            for field in ("key", "bundle", "internalId", "url")
        )

    missing = sorted(
        name for name in referenced if not any(matches(name, asset) for asset in chart_assets)
    )
    preloaded = sorted(
        str(asset.get("key") or "")
        for asset in chart_assets
        if not any(matches(name, asset) for name in referenced)
    )
    status = (
        "MASTER_AHEAD_OF_CATALOG"
        if missing
        else "CATALOG_PRELOADED_CHARTS"
        if preloaded
        else "CATALOG_MASTER_ALIGNED"
    )
    return {
        "status": status,
        "masterReferencedChartCount": len(referenced),
        "catalogNamedChartCount": len(chart_assets),
        "masterReferencesWithoutCatalog": missing,
        "catalogNamedWithoutMaster": preloaded,
    }
