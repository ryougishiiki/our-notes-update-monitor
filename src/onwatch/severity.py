from __future__ import annotations

from typing import Any


def classify_severity(event: dict[str, Any]) -> str:
    source = event["source"]
    summary = event["summary"]
    assets = summary["assetsAdded"] + summary["assetsChanged"] + summary["assetsRemoved"]
    master = summary["masterAdded"] + summary["masterChanged"] + summary["masterRemoved"]
    if source.get("gameVersionBefore") != source.get("gameVersionAfter"):
        return "CLIENT"
    if assets >= 100 and master >= 50:
        return "MAJOR"
    if any(summary[f"charts{suffix}"] for suffix in ("Added", "Changed", "Removed")) or master:
        return "CONTENT"
    if assets or source.get("catalogBefore") != source.get("catalogAfter"):
        return "MINOR"
    return "INFO"
