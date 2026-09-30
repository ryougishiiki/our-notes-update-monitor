from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .hashing import sha256_json
from .io import utc_now, write_json


SNAPSHOT_SCHEMA = "our-notes-snapshot/1"
TABLE_FILES = {
    "MasterLiveMusic": "songs.json",
    "MasterLiveMusicScore": "scores.json",
    "MasterEvent": "events.json",
    "MasterCharacter": "characters.json",
    "MusicCatalog": "music-catalog.json",
}


def make_snapshot(
    *,
    server: str,
    probe: dict[str, Any],
    catalog_sha256: str,
    assets: list[dict[str, Any]],
    tables: dict[str, dict[str, Any]],
    parent: str | None,
) -> dict[str, Any]:
    sources = probe["sources"]
    table_hashes = {
        name: sha256_json(table["rows"]) for name, table in sorted(tables.items())
    }
    master_revision = sha256_json(table_hashes)
    version = {
        "gameVersion": sources.get("gameVersion"),
        "catalogVersion": sources.get("catalogVersion"),
    }
    catalog = {
        "catalogHash": sources.get("catalogHash"),
        "catalogSha256": catalog_sha256,
        "resourceManifestRevision": sources.get("resourceManifestRevision"),
        "downloadableAssetCount": len(assets),
        "source": "official-cdn",
    }
    content = {
        "schema": SNAPSHOT_SCHEMA,
        "server": server,
        "version": version,
        "catalog": catalog,
        "master": tables,
        "assets": assets,
        "masterRevision": master_revision,
    }
    digest = sha256_json(content)
    revision_identity = sha256_json(
        {"server": server, "contentSha256": digest, "parent": parent}
    )[:16]
    revision = f"{server}-{revision_identity}"
    return {
        "meta": {
            "schema": SNAPSHOT_SCHEMA,
            "server": server,
            "revision": revision,
            "contentSha256": digest,
            "createdAt": probe.get("checkedAt") or utc_now(),
            "parent": parent,
            "catalogHash": catalog["catalogHash"],
            "catalogSha256": catalog_sha256,
            "masterRevision": master_revision,
            "masterIndexRevision": sources.get("masterRevision"),
            "resourceManifestRevision": catalog["resourceManifestRevision"],
            "source": {
                "catalog": "official-cdn",
                "master": "haneoka-public-mirror",
                "masterAuthority": probe.get("provenance", {}).get("masterAuthority", "derived"),
            },
        },
        "version": version,
        "catalog": catalog,
        "assets": assets,
        "charts": [asset for asset in assets if asset.get("category") == "music_score"],
        "master": tables,
    }


def write_snapshot(root: Path, snapshot: dict[str, Any]) -> Path:
    meta = snapshot["meta"]
    directory = root / "snapshots" / meta["revision"]
    if directory.exists():
        existing = load_snapshot_dir(directory)
        if existing["meta"].get("contentSha256") != meta["contentSha256"]:
            raise ValueError(f"snapshot revision collision at {directory}")
        return directory
    write_json(directory / "meta.json", meta)
    write_json(directory / "version.json", snapshot["version"])
    write_json(directory / "catalog.json", snapshot["catalog"])
    write_json(directory / "assets.json", snapshot["assets"])
    write_json(directory / "charts.json", snapshot["charts"])
    for table_name, table in sorted(snapshot["master"].items()):
        file_name = TABLE_FILES.get(table_name, f"{_slug(table_name)}.json")
        write_json(directory / "master" / file_name, table)
    return directory


def load_snapshot_dir(directory: Path) -> dict[str, Any]:
    def read(name: str) -> Any:
        return json.loads((directory / name).read_text("utf-8"))

    master: dict[str, dict[str, Any]] = {}
    master_dir = directory / "master"
    if master_dir.exists():
        for path in sorted(master_dir.glob("*.json")):
            table = json.loads(path.read_text("utf-8"))
            name = table.get("table")
            if not isinstance(name, str) or name in master:
                raise ValueError(f"invalid or duplicate table in {path}")
            master[name] = table
    return {
        "meta": read("meta.json"),
        "version": read("version.json"),
        "catalog": read("catalog.json"),
        "assets": read("assets.json"),
        "charts": read("charts.json"),
        "master": master,
    }


def load_current_snapshot(root: Path, current: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(current, dict) or not current.get("current"):
        return None
    directory = root / "snapshots" / str(current["current"])
    if not directory.is_dir():
        raise ValueError(f"current snapshot pointer references missing directory {directory}")
    snapshot = load_snapshot_dir(directory)
    if snapshot["meta"].get("revision") != current["current"]:
        raise ValueError("current snapshot pointer and snapshot metadata disagree")
    return snapshot


def _slug(name: str) -> str:
    return "".join(character.lower() if character.isalnum() else "-" for character in name).strip("-")
