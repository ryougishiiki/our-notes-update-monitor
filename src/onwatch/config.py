from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = ROOT / "config" / "intl.json"


@dataclass(frozen=True)
class ServerConfig:
    server: str
    display_timezone: str
    catalog_version: str
    catalog_url_template: str
    catalog_hash_url_template: str
    catalog_version_url: str | None
    catalog_version_pointer: str | None
    remote_root: str
    game_version_url: str | None
    game_version_pointer: str | None
    resource_manifest_url: str | None
    master_version_endpoint: str
    master_remote_root: str
    master_crypto: dict[str, str]
    master_base_url: str
    master_endpoints: dict[str, str]
    master_authority: str
    request_timeout_seconds: float
    allow_empty_tables: frozenset[str]

    def catalog_url_for(self, version: str) -> str:
        return self.catalog_url_template.format(version=version)

    def catalog_hash_url_for(self, version: str) -> str:
        return self.catalog_hash_url_template.format(version=version)


def load_config(path: Path | None = None, server: str | None = None) -> ServerConfig:
    config_path = path or DEFAULT_CONFIG
    document = json.loads(config_path.read_text("utf-8"))
    chosen = server or document.get("default") or document.get("server")
    servers = document.get("servers")
    if isinstance(servers, dict):
        raw = servers.get(chosen)
    else:
        raw = document if chosen == document.get("server") else None
    if not isinstance(raw, dict):
        raise ValueError(f"unknown server {chosen!r} in {config_path}")

    catalog = raw["catalog"]
    master = raw.get("master", {})
    mirror = master.get("mirror", raw.get("masterMirror", {}))
    version = raw.get("gameVersion", {})
    if not isinstance(version, dict):
        version = {}
    remote_root = str(catalog["remoteRoot"]).rstrip("/")
    catalog_version = str(catalog["version"])
    catalog_url_template = str(
        catalog.get("urlTemplate") or f"{remote_root}/catalog_{{version}}.bin"
    )
    catalog_hash_url_template = str(
        catalog.get("hashUrlTemplate") or f"{remote_root}/catalog_{{version}}.hash"
    )
    base = str(mirror["baseUrl"]).rstrip("/") + str(mirror.get("apiPrefix", ""))
    server_id = str(raw.get("id") or chosen)
    if not re.fullmatch(r"[A-Za-z0-9._-]+", server_id):
        raise ValueError(f"invalid server id {server_id!r}")
    return ServerConfig(
        server=server_id,
        display_timezone=str(raw.get("displayTimezone", "Asia/Shanghai")),
        catalog_version=catalog_version,
        catalog_url_template=catalog_url_template,
        catalog_hash_url_template=catalog_hash_url_template,
        catalog_version_url=catalog.get("versionUrl"),
        catalog_version_pointer=catalog.get("versionJsonPointer"),
        remote_root=remote_root,
        game_version_url=version.get("url"),
        game_version_pointer=version.get("jsonPointer"),
        resource_manifest_url=catalog.get("resourceManifestUrl"),
        master_version_endpoint=str(master.get("versionEndpoint") or ""),
        master_remote_root=str(master.get("remoteRoot") or "").rstrip("/"),
        master_crypto={str(k): str(v) for k, v in raw.get("masterCrypto", {}).items()},
        master_base_url=base.rstrip("/"),
        master_endpoints={str(k): str(v) for k, v in mirror["endpoints"].items()},
        master_authority=str(master.get("authority", "official")),
        request_timeout_seconds=float(raw.get("requestTimeoutSeconds", 30)),
        allow_empty_tables=frozenset(str(value) for value in raw.get("allowEmptyTables", [])),
    )


def endpoint_url(config: ServerConfig, name: str) -> str:
    try:
        endpoint = config.master_endpoints[name]
    except KeyError as error:
        raise ValueError(f"Master endpoint {name!r} is not configured") from error
    if endpoint.startswith(("https://", "http://")):
        return endpoint
    return f"{config.master_base_url}/{endpoint.lstrip('/')}"
