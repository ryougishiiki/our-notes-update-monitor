from __future__ import annotations

from typing import Any

from ..classify import classify_asset


def normalize_catalog(reader: Any) -> list[dict[str, Any]]:
    locations = reader.downloadable()
    assets: list[dict[str, Any]] = []
    seen: set[str] = set()
    for location in locations:
        data = location.get("data")
        if not isinstance(data, dict):
            raise ValueError("downloadable catalog location has no bundle data")
        key = str(location.get("primaryKey") or "").strip()
        url = str(location.get("remoteUrl") or "").strip()
        if not key or not url.startswith("https://"):
            raise ValueError("downloadable catalog location has an invalid key or URL")
        if key in seen:
            raise ValueError(f"catalog has duplicate downloadable primary key {key!r}")
        seen.add(key)
        internal_id = str(location.get("internalId") or "")
        bundle_name = str(data.get("bundleName") or "")
        resource_type = str(location.get("resourceType") or "")
        assets.append(
            {
                "key": key,
                "bundle": bundle_name or key.rsplit("/", 1)[-1],
                "url": url,
                "hash": str(data.get("hash") or ""),
                "crc": int(data.get("crc") or 0),
                "size": int(data.get("bundleSize") or 0),
                "resourceType": resource_type,
                "internalId": internal_id,
                "category": classify_asset(key, internal_id, bundle_name, resource_type),
            }
        )
    if not assets:
        raise ValueError("catalog produced no downloadable resources; refusing an empty snapshot")
    return sorted(assets, key=lambda item: item["key"])
