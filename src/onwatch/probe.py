from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any

from .catalog_version import resolve_catalog_version
from .config import ServerConfig, endpoint_url
from .hashing import sha256_bytes, sha256_json
from .http import fetch_bytes, probe_exists
from .io import utc_now
from .sources.master import collect_master_indexes, master_index_revisions


def fetch_probe(config: ServerConfig) -> dict[str, Any]:
    with ThreadPoolExecutor(max_workers=3) as pool:
        catalog_version_future = pool.submit(_fetch_catalog_version, config)
        version_future = pool.submit(_fetch_game_version, config)
        indexes_future = pool.submit(collect_master_indexes, config)
        catalog_version, catalog_version_source = catalog_version_future.result()
        game_version = version_future.result()
        indexes = indexes_future.result()
    catalog_hash = _fetch_catalog_hash(config, catalog_version)

    revisions = master_index_revisions(indexes)
    master_revision = sha256_json(revisions)
    resource_manifest_revision = catalog_hash
    if config.resource_manifest_url:
        resource_manifest_revision = sha256_bytes(
            fetch_bytes(
                config.resource_manifest_url.format(version=catalog_version),
                timeout=config.request_timeout_seconds,
            )
        )
    return {
        "schema": "our-notes-probe/1",
        "server": config.server,
        "checkedAt": utc_now(),
        "sources": {
            "gameVersion": game_version,
            "catalogVersion": catalog_version,
            "catalogVersionResolved": catalog_version,
            "catalogVersionConfiguredFloor": config.catalog_version,
            "catalogHash": catalog_hash,
            "masterRevision": master_revision,
            "masterIndexRevisions": revisions,
            "resourceManifestRevision": resource_manifest_revision,
        },
        "provenance": {
            "catalog": "official-cdn-hash",
            "catalogVersionSource": catalog_version_source,
            "master": "public-mirror-index",
            "masterAuthority": config.master_authority,
            "gameVersion": "configured-endpoint" if config.game_version_url else "unavailable",
        },
        "_masterIndexes": indexes,
    }


def _fetch_catalog_hash(config: ServerConfig, version: str) -> str:
    url = config.catalog_hash_url_for(version)
    raw = fetch_bytes(url, timeout=config.request_timeout_seconds)
    try:
        value = raw.decode("ascii").strip()
    except UnicodeDecodeError as error:
        raise ValueError(f"catalog hash response from {url} is not ASCII") from error
    if not value:
        raise ValueError(f"catalog hash response from {url} is empty")
    return value


def _fetch_catalog_version(config: ServerConfig) -> tuple[str, str]:
    if not config.catalog_version_url:
        resolution = resolve_catalog_version(
            config.catalog_version,
            lambda version: probe_exists(
                config.catalog_hash_url_for(version),
                timeout=config.request_timeout_seconds,
            ),
        )
        return resolution.resolved, resolution.source
    raw = fetch_bytes(
        config.catalog_version_url, timeout=config.request_timeout_seconds
    )
    try:
        document = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError):
        value = raw.decode("utf-8").strip()
    else:
        value = _pointer(document, config.catalog_version_pointer)
    if value is None or value == "":
        raise ValueError("configured catalog version endpoint returned no version")
    return str(value), "official-endpoint"


def _fetch_game_version(config: ServerConfig) -> str | None:
    if not config.game_version_url:
        return None
    raw = fetch_bytes(config.game_version_url, timeout=config.request_timeout_seconds)
    try:
        document = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError):
        value = raw.decode("utf-8").strip()
    else:
        value = _pointer(document, config.game_version_pointer)
    if value is None or value == "":
        raise ValueError("configured game version endpoint returned no version")
    return str(value)


def _pointer(value: Any, pointer: str | None) -> Any:
    if not pointer:
        return value
    if pointer.startswith("/"):
        current = value
        for segment in pointer.lstrip("/").split("/"):
            segment = segment.replace("~1", "/").replace("~0", "~")
            current = current[int(segment)] if isinstance(current, list) else current[segment]
        return current
    current = value
    for segment in pointer.split("."):
        current = current[segment]
    return current


def changed_sources(previous: dict[str, Any] | None, current: dict[str, Any]) -> list[str]:
    if not isinstance(previous, dict) or not isinstance(previous.get("sources"), dict):
        return ["baseline"]
    before = previous["sources"]
    after = current["sources"]
    return sorted(key for key, value in after.items() if before.get(key) != value)
