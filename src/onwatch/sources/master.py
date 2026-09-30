# Official Master manifest, table-integrity, and decrypt contracts in this file
# are adapted from haneoka-gakuen/haneoka/scripts/extract/master.py (MPL-2.0).
# The bounded catalog resolver and gRPC protocol have their own file notices.

from __future__ import annotations

import gzip
import hashlib
import json
import re
from concurrent.futures import ThreadPoolExecutor
from typing import Any
from urllib.parse import quote

from ..config import ServerConfig, endpoint_url
from ..hashing import sha256_json
from ..http import fetch_bytes, fetch_json


SCORE_ID_FIELDS = {
    "easy": "_easyID",
    "normal": "_normalID",
    "hard": "_hardID",
    "expert": "_expertID",
    "special": "_specialID",
}
MASTER_TABLES = ("MasterLiveMusic", "MasterLiveMusicScore", "MasterEvent", "MasterCharacter")
MANIFEST_VERSION = re.compile(r"^[A-Za-z0-9._-]+(?:/[A-Za-z0-9._-]+)*$")
MANIFEST_FILE = re.compile(r"^Master[A-Za-z0-9_]+\.bin$")
SHA256 = re.compile(r"^[a-fA-F0-9]{64}$")
MAX_MANIFEST_FILES = 4096
MAX_MASTER_TABLE_BYTES = 1024 * 1024 * 1024


def validate_master_manifest(value: object, expected_version: str) -> dict[str, Any]:
    """Validate manifest structure and file metadata before downloading tables.

    The manifest contract follows haneoka-gakuen/haneoka's
    ``scripts/extract/master.py`` (MPL-2.0); see THIRD_PARTY_NOTICES.md.
    """
    if not isinstance(value, dict):
        raise ValueError("manifest root must be an object")
    version = value.get("version")
    if (
        not isinstance(version, str)
        or version != expected_version
        or not MANIFEST_VERSION.fullmatch(version)
        or any(part in {".", ".."} for part in version.split("/"))
    ):
        raise ValueError("manifest version is missing, unsafe, or does not match the official version")
    files = value.get("files")
    if not isinstance(files, list) or not files or len(files) > MAX_MANIFEST_FILES:
        raise ValueError("manifest files must be a non-empty bounded list")
    seen: set[str] = set()
    validated: list[dict[str, Any]] = []
    for index, entry in enumerate(files):
        if not isinstance(entry, dict):
            raise ValueError(f"manifest files[{index}] must be an object")
        name, digest, size = entry.get("name"), entry.get("hash"), entry.get("size")
        if not isinstance(name, str) or not MANIFEST_FILE.fullmatch(name):
            raise ValueError(f"manifest files[{index}] has an unsafe filename")
        if name in seen:
            raise ValueError(f"manifest contains duplicate filename {name}")
        if not isinstance(digest, str) or not SHA256.fullmatch(digest):
            raise ValueError(f"manifest has an invalid SHA-256 for {name}")
        if isinstance(size, bool) or not isinstance(size, int) or not 0 < size <= MAX_MASTER_TABLE_BYTES:
            raise ValueError(f"manifest has an invalid size for {name}")
        seen.add(name)
        validated.append({"name": name, "hash": digest.lower(), "size": size})
    result = dict(value)
    result["files"] = validated
    return result


def fetch_master_manifest(
    config: ServerConfig,
    expected_version: str,
    *,
    fetcher=fetch_bytes,
) -> tuple[dict[str, Any], bytes, str]:
    root = config.master_remote_root.rstrip("/")
    if not root.startswith("https://") or "?" in root or "#" in root:
        raise RuntimeError("OFFICIAL_MASTER_MANIFEST_INVALID: invalid official Master CDN root")
    try:
        raw = fetcher(f"{root}/{expected_version}/MasterManifest.json", timeout=config.request_timeout_seconds)
        value = json.loads(raw.decode("utf-8"))
        manifest = validate_master_manifest(value, expected_version)
    except Exception as error:
        raise RuntimeError(f"OFFICIAL_MASTER_MANIFEST_INVALID: {error}") from error
    return manifest, raw, hashlib.sha256(raw).hexdigest()


def _decrypt_master(raw: bytes, crypto: dict[str, str], name: str) -> dict[str, Any]:
    try:
        from py3rijndael import Pkcs7Padding, RijndaelCbc
    except ImportError as error:  # pragma: no cover
        raise RuntimeError("py3rijndael is required to decrypt official Master tables") from error
    if set(crypto) != {"scheme", "salt", "key", "iv"} or crypto.get("scheme") != "haneoka-rijndael-cbc-v2":
        raise ValueError("official Master crypto contract is not configured")
    salt, key, iv = (bytes.fromhex(crypto[name]) for name in ("salt", "key", "iv"))
    if raw[:64] != salt + iv:
        raise ValueError(f"master crypto constants do not match {name}")
    cipher = RijndaelCbc(key, iv, Pkcs7Padding(32), block_size=32)
    try:
        value = json.loads(gzip.decompress(cipher.decrypt(raw[64:])).decode("utf-8"))
    except (OSError, ValueError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError(f"invalid encrypted Master table {name}") from error
    if not isinstance(value, dict) or not isinstance(value.get("_allData"), list):
        raise ValueError(f"invalid Master table {name}: _allData is missing")
    return value


def _official_rows(table: dict[str, Any], name: str, candidates: tuple[str, ...]) -> dict[str, Any]:
    rows = table["_allData"]
    result: dict[str, Any] = {}
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError(f"OFFICIAL_MASTER_TABLE_INVALID: {name} has a non-object row")
        key = next((row.get(field) for field in candidates if row.get(field) is not None), None)
        if key is None or str(key) in result:
            raise ValueError(f"OFFICIAL_MASTER_TABLE_INVALID: {name} has a missing or duplicate primary key")
        result[str(key)] = row
    return result


def fetch_official_master_tables(
    config: ServerConfig,
    master_version: str,
    expected_manifest_sha256: str,
    *,
    fetcher=fetch_bytes,
) -> dict[str, dict[str, Any]]:
    """Fetch all Monitor-required official files, verifying each before parsing."""
    manifest, _manifest_raw, manifest_sha256 = fetch_master_manifest(
        config, master_version, fetcher=fetcher
    )
    if manifest_sha256 != expected_manifest_sha256:
        raise RuntimeError("OFFICIAL_MASTER_MANIFEST_INVALID: manifest changed during deep scan")
    entries = {entry["name"]: entry for entry in manifest["files"]}
    root = f"{config.master_remote_root.rstrip('/')}/{master_version}"
    decoded: dict[str, dict[str, Any]] = {}
    for table_name in MASTER_TABLES:
        filename = f"{table_name}.bin"
        entry = entries.get(filename)
        if entry is None:
            raise RuntimeError(f"OFFICIAL_MASTER_MANIFEST_INVALID: required {filename} is missing")
        raw = fetcher(f"{root}/{filename}", timeout=config.request_timeout_seconds)
        if len(raw) != entry["size"] or hashlib.sha256(raw).hexdigest() != entry["hash"]:
            raise RuntimeError(f"OFFICIAL_MASTER_TABLE_INTEGRITY_FAILED: {filename} size or SHA-256 mismatch")
        decoded[table_name] = _decrypt_master(raw, config.master_crypto, filename)

    music_rows = _official_rows(decoded["MasterLiveMusic"], "MasterLiveMusic", ("_id",))
    score_rows = _official_rows(decoded["MasterLiveMusicScore"], "MasterLiveMusicScore", ("_id",))
    event_rows = _official_rows(decoded["MasterEvent"], "MasterEvent", ("_id", "id", "eventId"))
    character_rows = _official_rows(
        decoded["MasterCharacter"], "MasterCharacter", ("_id", "_characterId", "characterId", "id")
    )
    if not music_rows or not score_rows or not character_rows:
        raise RuntimeError("OFFICIAL_MASTER_TABLE_INVALID: a required song, score, or character table is empty")
    return {
        "MasterLiveMusic": _table("MasterLiveMusic", "_id", music_rows, "official"),
        "MasterLiveMusicScore": _table("MasterLiveMusicScore", "_id", score_rows, "official"),
        "MasterEvent": _table("MasterEvent", "_id", event_rows, "official"),
        "MasterCharacter": _table("MasterCharacter", "_id", character_rows, "official"),
    }


def compare_master_mirror(
    config: ServerConfig,
    tables: dict[str, dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    """Compare the official row IDs with the mirror's single summary-index request."""
    songs = fetch_json(endpoint_url(config, "songs"), timeout=config.request_timeout_seconds)
    if not isinstance(songs, dict) or not songs:
        raise ValueError("mirror songs endpoint returned no songs")
    mirror_ids: set[int] = set()
    mirror_chart_files: set[str] = set()
    mirror_chart_count = 0
    music_catalog_rows: dict[str, dict[str, Any]] = {}
    for key, summary in songs.items():
        if not isinstance(summary, dict):
            continue
        try:
            music_id = int(summary.get("musicId") or key)
        except (TypeError, ValueError):
            continue
        mirror_ids.add(music_id)
        music_catalog_rows[str(music_id)] = summary
        for entry in summary.get("difficulty") or []:
            if not isinstance(entry, dict):
                continue
            mirror_chart_count += 1
            path = str(entry.get("file") or "").split("/Live/MusicScore/")[-1]
            if path.endswith(".bytes"):
                mirror_chart_files.add(path[:-6])

    music_table = tables["MasterLiveMusic"]["rows"]
    score_table = tables["MasterLiveMusicScore"]["rows"]
    official_ids = {int(key) for key in music_table if str(key).isdigit()}
    score_by_id = {str(key): row for key, row in score_table.items()}
    official_chart_files: set[str] = set()
    for music in music_table.values():
        for field in SCORE_ID_FIELDS.values():
            score = score_by_id.get(str(music.get(field) or ""))
            filename = str(score.get("_musicScoreTextFileName") or "") if score else ""
            if filename:
                official_chart_files.add(filename)

    official_only_ids = sorted(official_ids - mirror_ids)
    mirror_only_ids = sorted(mirror_ids - official_ids)
    official_only_charts = sorted(official_chart_files - mirror_chart_files)
    mirror_only_charts = sorted(mirror_chart_files - official_chart_files)
    if official_only_ids or official_only_charts or len(official_ids) > len(mirror_ids):
        status = "MIRROR_LAG_CONFIRMED"
    elif not mirror_only_ids and not mirror_only_charts:
        status = "MIRROR_CURRENT"
    else:
        status = "MIRROR_DIVERGED"
    comparison = {
        "officialSongCount": len(official_ids),
        "mirrorSongCount": len(mirror_ids),
        "officialChartCount": len(official_chart_files),
        "mirrorChartCount": mirror_chart_count,
        "officialOnlyMusicIds": official_only_ids,
        "mirrorOnlyMusicIds": mirror_only_ids,
        "officialOnlyChartFiles": official_only_charts,
        "mirrorOnlyChartFiles": mirror_only_charts,
        "mirrorRevision": sha256_json(songs),
        "status": status,
    }
    music_catalog = _table("MusicCatalog", "musicId", music_catalog_rows, "derived")
    return comparison, music_catalog


def collect_master_indexes(config: ServerConfig) -> dict[str, Any]:
    names = tuple(config.master_endpoints)
    with ThreadPoolExecutor(max_workers=max(1, len(names))) as pool:
        values = list(
            pool.map(
                lambda name: fetch_json(
                    endpoint_url(config, name), timeout=config.request_timeout_seconds
                ),
                names,
            )
        )
    indexes = dict(zip(names, values))
    _validate_index("songs", indexes.get("songs"), nonempty=True)
    _validate_index("characters", indexes.get("characters"), nonempty=True)
    _validate_index("events", indexes.get("events"), nonempty=False)
    return indexes


def master_index_revisions(indexes: dict[str, Any]) -> dict[str, str]:
    return {name: sha256_json(value) for name, value in sorted(indexes.items())}


def make_master_tables(config: ServerConfig, indexes: dict[str, Any]) -> dict[str, dict[str, Any]]:
    songs = indexes["songs"]
    if not isinstance(songs, dict):
        raise ValueError("songs mirror response must be an object keyed by music ID")
    song_items = sorted(songs.items(), key=lambda item: str(item[0]))
    urls = [f"{endpoint_url(config, 'songs').rstrip('/')}/{quote(str(key), safe='')}" for key, _ in song_items]
    with ThreadPoolExecutor(max_workers=min(8, max(1, len(urls)))) as pool:
        detail_documents = list(
            pool.map(
                lambda url: fetch_json(url, timeout=config.request_timeout_seconds), urls
            )
        )

    music_rows: dict[str, dict[str, Any]] = {}
    for (key, _summary), document in zip(song_items, detail_documents):
        raw = document.get("raw") if isinstance(document, dict) else None
        if not isinstance(raw, dict):
            raise ValueError(f"song detail {key} has no raw MasterLiveMusic row")
        raw_id = str(raw.get("_id") or "")
        if not raw_id or raw_id != str(key):
            raise ValueError(f"song detail {key} has an unexpected Master row ID {raw_id!r}")
        music_rows[raw_id] = raw

    score_rows: dict[str, dict[str, Any]] = {}
    for music_key, summary in song_items:
        if not isinstance(summary, dict):
            raise ValueError(f"song index row {music_key} is not an object")
        music = music_rows[str(music_key)]
        difficulties = summary.get("difficulty")
        if not isinstance(difficulties, list):
            raise ValueError(f"song index row {music_key} has no difficulty list")
        for entry in difficulties:
            if not isinstance(entry, dict):
                raise ValueError(f"song index row {music_key} has an invalid difficulty entry")
            name = str(entry.get("difficultyName") or "").casefold()
            field = SCORE_ID_FIELDS.get(name)
            score_id = music.get(field) if field else None
            if not score_id:
                continue
            score_id = str(score_id)
            chart_path = str(entry.get("file") or "")
            marker = "/MusicScore/"
            if marker not in chart_path or not chart_path.endswith(".bytes"):
                raise ValueError(f"song index row {music_key} has an invalid chart path")
            chart_file = chart_path.split(marker, 1)[1][:-len(".bytes")]
            if score_id in score_rows:
                raise ValueError(f"duplicate MasterLiveMusicScore ID {score_id}")
            score_rows[score_id] = {
                "_id": int(score_id),
                "_musicScoreTextFileName": chart_file,
                "_fullComboCount": entry.get("noteCount"),
                "_musicScoreLevel": entry.get("playLevel"),
                "_difficultyName": name,
                "_difficultyIndex": entry.get("difficulty"),
            }

    event_payload = indexes["events"]
    events = event_payload.get("entries") if isinstance(event_payload, dict) else event_payload
    if isinstance(events, list):
        event_rows = _keyed_rows(events, ("_id", "id", "eventId"), "events")
    elif isinstance(events, dict):
        event_rows = {str(key): value for key, value in events.items()}
    else:
        raise ValueError("events mirror response must contain an entries object or array")

    character_rows = _keyed_rows(
        indexes["characters"], ("characterId", "_id", "id"), "characters"
    )
    mirror_song_rows = {str(key): value for key, value in songs.items()}

    return {
        "MasterLiveMusic": _table("MasterLiveMusic", "_id", music_rows, "derived"),
        "MasterLiveMusicScore": _table(
            "MasterLiveMusicScore", "_id", score_rows, "mirror-derived"
        ),
        "MasterEvent": _table("MasterEvent", "mirrorKey", event_rows, "derived"),
        "MasterCharacter": _table(
            "MasterCharacter", "characterId", character_rows, "derived"
        ),
        "MusicCatalog": _table("MusicCatalog", "musicId", mirror_song_rows, "derived"),
    }


def _table(name: str, key_field: str, rows: dict[str, Any], authority: str) -> dict[str, Any]:
    ordered = {key: rows[key] for key in sorted(rows, key=_sort_key)}
    return {
        "schema": "our-notes-master-table/1",
        "table": name,
        "primaryKey": key_field,
        "authority": authority,
        "rows": ordered,
    }


def _sort_key(value: str) -> tuple[int, int | str, str]:
    return (0, int(value), value) if value.isdigit() else (1, value, value)


def _keyed_rows(value: Any, candidates: tuple[str, ...], label: str) -> dict[str, Any]:
    if isinstance(value, dict):
        result = {}
        for key, row in value.items():
            if not isinstance(row, dict):
                raise ValueError(f"{label} row {key} is not an object")
            resolved = next((row.get(field) for field in candidates if row.get(field) is not None), key)
            resolved = str(resolved)
            if resolved in result:
                raise ValueError(f"duplicate {label} primary key {resolved}")
            result[resolved] = row
        return result
    if isinstance(value, list):
        result = {}
        for row in value:
            if not isinstance(row, dict):
                raise ValueError(f"{label} row is not an object")
            key = next((row.get(field) for field in candidates if row.get(field) is not None), None)
            if key is None:
                raise ValueError(f"{label} row has no configured primary key")
            if str(key) in result:
                raise ValueError(f"duplicate {label} primary key {key}")
            result[str(key)] = row
        return result
    raise ValueError(f"{label} mirror response must be an object or array")


def _validate_index(name: str, value: Any, *, nonempty: bool) -> None:
    if name == "events" and isinstance(value, dict):
        value = value.get("entries", value)
    if not isinstance(value, (dict, list)):
        raise ValueError(f"{name} mirror response is not a collection")
    if nonempty and not value:
        raise ValueError(f"{name} mirror response is empty")
    if isinstance(value, dict) and any(not isinstance(row, dict) for row in value.values()):
        raise ValueError(f"{name} mirror response contains an invalid row")
    if isinstance(value, list) and any(not isinstance(row, dict) for row in value):
        raise ValueError(f"{name} mirror response contains an invalid row")
