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
from .master_protocol import discover_master_version
from .sources.master import fetch_master_manifest


def fetch_probe(config: ServerConfig) -> dict[str, Any]:
    with ThreadPoolExecutor(max_workers=2) as pool:
        master_future = pool.submit(
            discover_master_version,
            config.master_version_endpoint,
            timeout=config.request_timeout_seconds,
        )
        version_future = pool.submit(_fetch_game_version, config)
        master_version = master_future.result()
        game_version = version_future.result()
    _manifest, _manifest_raw, master_manifest_sha256 = fetch_master_manifest(
        config, master_version.version
    )
    catalog_version, catalog_version_source = _fetch_catalog_version(
        config, master_version.resource_version
    )
    catalog_hash = _fetch_catalog_hash(config, catalog_version)

    master_revision = sha256_json(
        {
            "masterVersion": master_version.version,
            "masterResourceVersion": master_version.resource_version,
            "masterManifestSha256": master_manifest_sha256,
        }
    )
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
            "masterVersion": master_version.version,
            "masterResourceVersion": master_version.resource_version,
            "masterManifestSha256": master_manifest_sha256,
            "masterRevision": master_revision,
            "resourceManifestRevision": resource_manifest_revision,
        },
        "provenance": {
            "catalog": "official-cdn-hash",
            "catalogVersionSource": catalog_version_source,
            "master": "official-master-cdn",
            "masterAuthority": "official",
            "mirror": "comparison-only",
            "gameVersion": "configured-endpoint" if config.game_version_url else "unavailable",
        },
        "_masterVersion": master_version,
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


def _catalog_version_published(config: ServerConfig, version: str) -> bool:
    if not probe_exists(
        config.catalog_hash_url_for(version), timeout=config.request_timeout_seconds
    ):
        return False
    return probe_exists(
        config.catalog_url_for(version), timeout=config.request_timeout_seconds
    )


def _fetch_catalog_version(
    config: ServerConfig, master_resource_version: str | None = None
) -> tuple[str, str]:
    if not config.catalog_version_url:
        resolution = resolve_catalog_version(
            config.catalog_version,
            lambda version: _catalog_version_published(config, version),
            master_resource_version,
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
