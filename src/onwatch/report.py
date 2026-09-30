from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def render_report(event: dict[str, Any], timezone_name: str = "Asia/Shanghai") -> str:
    local_time = _local_time(event["detectedAt"], timezone_name)
    source = event["source"]
    summary = event["summary"]
    lines = [
        "# Our Notes 数据更新",
        "",
        f"检测时间：{local_time}",
        f"服务器：{event['server']}　严重度：{event['severity']}",
        "",
        "## 版本",
        "",
        f"Game: {_shown(source.get('gameVersionBefore'))} → {_shown(source.get('gameVersionAfter'))}",
        f"Catalog: {_shown(source.get('catalogVersionBefore'))} → {_shown(source.get('catalogVersionAfter'))}",
        f"Catalog hash: {_shown(source.get('catalogBefore'))} → {_shown(source.get('catalogAfter'))}",
        f"Master revision: {_shown(source.get('masterBefore'))} → {_shown(source.get('masterAfter'))}",
        f"Master source: {_shown(source.get('masterSource'))} ({_shown(source.get('masterAuthority'))})",
        "",
        "## 资源",
        "",
        f"新增 {summary['assetsAdded']}　修改 {summary['assetsChanged']}　删除 {summary['assetsRemoved']}",
        "",
    ]
    for category, counts in sorted(event["categories"].items()):
        changes = _asset_records(event, category)
        lines.extend([f"### {category}", ""])
        if not changes:
            lines.append(f"新增 {counts['added']}　修改 {counts['changed']}　删除 {counts['removed']}")
        else:
            lines.extend(_lines_limited([_format_asset(item) for item in changes]))
        lines.append("")

    lines.extend(
        [
            "## Master",
            "",
            f"新增 {summary['masterAdded']}　修改 {summary['masterChanged']}　删除 {summary['masterRemoved']}",
            "",
        ]
    )
    if event["masterChanges"]:
        lines.extend(_lines_limited([_format_master(item) for item in event["masterChanges"]]))
    else:
        lines.append("无逐行变化。")
    lines.extend(["", "## 谱面", "", f"新增 {summary['chartsAdded']}　修改 {summary['chartsChanged']}　删除 {summary['chartsRemoved']}", ""])
    for label, values in (("新增", event["charts"]["added"]), ("修改", event["charts"]["changed"]), ("删除", event["charts"]["removed"])):
        if values:
            lines.append(f"{label}：" + ", ".join(values[:30]))
            if len(values) > 30:
                lines.append(f"（另有 {len(values) - 30} 项）")
    lines.extend(["", f"快照：`{event['snapshot']['revision']}`", ""])
    return "\n".join(lines)


def _asset_records(event: dict[str, Any], category: str) -> list[dict[str, Any]]:
    result = []
    for kind in ("added", "changed", "removed"):
        for record in event["assets"][kind]:
            asset = record.get("after") or record.get("before") or {}
            if asset.get("category") == category:
                result.append({"type": kind, **record})
    return result


def _format_asset(record: dict[str, Any]) -> str:
    marker = {"added": "+", "changed": "~", "removed": "-"}[record["type"]]
    return f"{marker} {record['key']}"


def _format_master(change: dict[str, Any]) -> str:
    label = {"added": "+", "changed": "~", "removed": "-"}[change["type"]]
    head = f"{label} {change['table']}/{change['key']}"
    if change["type"] == "changed":
        fields = ", ".join(
            f"{name}: {_compact(value['before'])} → {_compact(value['after'])}"
            for name, value in list(change["fields"].items())[:8]
        )
        more = max(0, len(change["fields"]) - 8)
        return f"{head}: {fields}" + (f"（另有 {more} 个字段）" if more else "")
    row = change.get("after") or change.get("before") or {}
    fields = ", ".join(f"{key}={_compact(value)}" for key, value in list(row.items())[:4])
    return f"{head}: {fields}" if fields else head


def _lines_limited(items: list[str], limit: int = 100) -> list[str]:
    shown = [f"- {item}" for item in items[:limit]]
    if len(items) > limit:
        shown.append(f"- 另有 {len(items) - limit} 项")
    return shown


def _compact(value: Any) -> str:
    text = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return text if len(text) <= 100 else text[:97] + "..."


def _shown(value: Any) -> str:
    return "未知" if value is None else str(value)


def _local_time(value: str, timezone_name: str) -> str:
    instant = datetime.fromisoformat(value.replace("Z", "+00:00"))
    try:
        zone = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError:
        # Python on Windows may not have an IANA database before the package is
        # installed. The configured default is a current fixed-offset zone.
        if timezone_name == "Asia/Shanghai":
            zone = timezone(timedelta(hours=8), name="CST")
        else:
            raise ValueError(f"time zone database has no entry for {timezone_name!r}")
    return instant.astimezone(zone).strftime("%Y-%m-%d %H:%M %Z")
