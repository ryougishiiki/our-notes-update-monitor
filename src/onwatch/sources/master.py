from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import Any
from urllib.parse import quote

from ..config import ServerConfig, endpoint_url
from ..hashing import sha256_json
from ..http import fetch_json


SCORE_ID_FIELDS = {
    "easy": "_easyID",
    "normal": "_normalID",
    "hard": "_hardID",
    "expert": "_expertID",
    "special": "_specialID",
}


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
