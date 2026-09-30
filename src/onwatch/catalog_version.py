"""Resolve versioned Addressables catalogs by probing official ``.hash`` files.

The probe order and three-miss behavior are adapted from
``haneoka-gakuen/haneoka/scripts/ingest/apks.py::_resolve_catalog_version``
(MPL-2.0): https://github.com/haneoka-gakuen/haneoka/blob/main/scripts/ingest/apks.py
This file keeps that strategy while adding a hard request budget so a pathological
sequence of hits cannot make CI probe indefinitely.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

MAX_CONSECUTIVE_MISSES = 3
MAX_BUILD_ADVANCE = 128
MAX_LINE_ADVANCE = 128
MAX_PROBE_REQUESTS = 4096


@dataclass(frozen=True)
class CatalogVersionResolution:
    configured_floor: str
    resolved: str
    source: str
    probes: int


class CatalogProbeLimitError(RuntimeError):
    """Raised instead of returning a potentially stale answer at the probe cap."""


def resolve_catalog_version(
    floor: str, exists: Callable[[str], bool]
) -> CatalogVersionResolution:
    """Resolve the newest published four-part numeric version at or above floor.

    ``exists`` must check only the official ``catalog_<version>.hash`` URL. HTTP
    errors other than 400/403/404 must be raised by that callback so outages are
    never interpreted as missing versions.
    """
    parts = floor.split(".")
    if len(parts) != 4 or not all(part.isascii() and part.isdecimal() for part in parts):
        return CatalogVersionResolution(floor, floor, "config", 0)

    base = tuple(int(part) for part in parts)
    probes = 0

    def published(version: tuple[int, int, int, int]) -> bool:
        nonlocal probes
        if probes >= MAX_PROBE_REQUESTS:
            raise CatalogProbeLimitError(
                f"catalog version probing exceeded {MAX_PROBE_REQUESTS} hash requests"
            )
        probes += 1
        return exists(".".join(str(component) for component in version))

    def line_max(major: int, minor: int, patch: int, start_build: int) -> int | None:
        if not published((major, minor, patch, start_build)):
            return None
        latest = start_build
        consecutive_misses = 0
        build = start_build + 1
        while build <= start_build + MAX_BUILD_ADVANCE and consecutive_misses < MAX_CONSECUTIVE_MISSES:
            if published((major, minor, patch, build)):
                latest = build
                consecutive_misses = 0
            else:
                consecutive_misses += 1
            build += 1
        return latest

    best = base
    current_build = line_max(base[0], base[1], base[2], base[3])
    if current_build is not None:
        best = (base[0], base[1], base[2], current_build)

    for dimension in (2, 1, 0):
        consecutive_misses = 0
        component = base[dimension] + 1
        last_component = base[dimension] + MAX_LINE_ADVANCE
        while component <= last_component and consecutive_misses < MAX_CONSECUTIVE_MISSES:
            line = list(base)
            for index in range(dimension + 1, 4):
                line[index] = 0
            line[dimension] = component
            latest_build = line_max(line[0], line[1], line[2], 0)
            if latest_build is None:
                consecutive_misses += 1
            else:
                candidate = (line[0], line[1], line[2], latest_build)
                if candidate > best:
                    best = candidate
                consecutive_misses = 0
            component += 1
        if component > last_component and consecutive_misses < MAX_CONSECUTIVE_MISSES:
            raise CatalogProbeLimitError(
                f"catalog version line search exceeded {MAX_LINE_ADVANCE} higher lines"
            )

    resolved = ".".join(str(component) for component in best)
    return CatalogVersionResolution(floor, resolved, "probe", probes)
