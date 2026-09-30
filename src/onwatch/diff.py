from __future__ import annotations

from typing import Any


def diff_assets(before: list[dict[str, Any]], after: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    old = _asset_map(before)
    new = _asset_map(after)
    added = [{"key": key, "after": new[key]} for key in sorted(new.keys() - old.keys())]
    removed = [{"key": key, "before": old[key]} for key in sorted(old.keys() - new.keys())]
    changed = [
        {"key": key, "before": old[key], "after": new[key]}
        for key in sorted(old.keys() & new.keys())
        if old[key] != new[key]
    ]
    return {"added": added, "changed": changed, "removed": removed}


def diff_master(
    before: dict[str, dict[str, Any]], after: dict[str, dict[str, Any]]
) -> list[dict[str, Any]]:
    changes: list[dict[str, Any]] = []
    for table_name in sorted(before.keys() | after.keys()):
        old_rows = _rows(before.get(table_name))
        new_rows = _rows(after.get(table_name))
        for key in sorted(new_rows.keys() - old_rows.keys(), key=_key_sort):
            changes.append(
                {"table": table_name, "key": _display_key(key), "type": "added", "after": new_rows[key]}
            )
        for key in sorted(old_rows.keys() - new_rows.keys(), key=_key_sort):
            changes.append(
                {"table": table_name, "key": _display_key(key), "type": "removed", "before": old_rows[key]}
            )
        for key in sorted(old_rows.keys() & new_rows.keys(), key=_key_sort):
            fields = _field_diff(old_rows[key], new_rows[key])
            if fields:
                changes.append(
                    {
                        "table": table_name,
                        "key": _display_key(key),
                        "type": "changed",
                        "fields": fields,
                    }
                )
    return changes


def charts_from_asset_diff(asset_changes: dict[str, list[dict[str, Any]]]) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {"added": [], "changed": [], "removed": []}
    for change_type in result:
        records = asset_changes[change_type]
        keys = []
        for record in records:
            asset = record.get("after") or record.get("before")
            if isinstance(asset, dict) and asset.get("category") == "music_score":
                keys.append(str(record["key"]))
        result[change_type] = sorted(keys)
    return result


def _asset_map(assets: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    result = {}
    for asset in assets:
        key = str(asset.get("key") or "")
        if not key or key in result:
            raise ValueError(f"asset snapshot has a missing or duplicate key {key!r}")
        result[key] = asset
    return result


def _rows(table: dict[str, Any] | None) -> dict[str, Any]:
    if table is None:
        return {}
    rows = table.get("rows")
    if not isinstance(rows, dict):
        raise ValueError(f"Master table {table.get('table')} has no keyed rows object")
    return {str(key): value for key, value in rows.items()}


def _field_diff(before: Any, after: Any, prefix: str = "") -> dict[str, dict[str, Any]]:
    if isinstance(before, dict) and isinstance(after, dict):
        result: dict[str, dict[str, Any]] = {}
        for key in sorted(before.keys() | after.keys()):
            field = f"{prefix}.{key}" if prefix else str(key)
            if key not in before:
                result[field] = {"before": None, "after": after[key]}
            elif key not in after:
                result[field] = {"before": before[key], "after": None}
            else:
                result.update(_field_diff(before[key], after[key], field))
        return result
    if before != after:
        return {prefix or "value": {"before": before, "after": after}}
    return {}


def _key_sort(key: str) -> tuple[int, int | str, str]:
    return (0, int(key), key) if key.isdigit() else (1, key, key)


def _display_key(key: str) -> int | str:
    return int(key) if key.isdigit() and str(int(key)) == key else key
