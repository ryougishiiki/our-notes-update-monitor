"""Resolve the exact catalog generation reported by Master when available.

The probe order and three-miss behavior are adapted from
``haneoka-gakuen/haneoka/scripts/ingest/apks.py::_resolve_catalog_version``
(MPL-2.0): https://github.com/haneoka-gakuen/haneoka/blob/main/scripts/ingest/apks.py
Numeric probing remains as a fallback for sources without a Master anchor, with
a hard request budget so a pathological sequence of hits cannot stall CI.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from .master_protocol import is_safe_resource_version

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
    master_anchor: str | None = None
    master_anchor_available: bool = False


class CatalogProbeLimitError(RuntimeError):
    """Raised instead of returning a potentially stale answer at the probe cap."""


class CatalogAnchorUnavailableError(RuntimeError):
    """The official Master version points at a catalog that is not published."""


def resolve_catalog_version(
    floor: str,
    exists: Callable[[str], bool],
    master_anchor: str | None = None,
) -> CatalogVersionResolution:
    """Use an official Master catalog version verbatim, or probe from ``floor``.

    ``exists`` must return true only when both official ``.hash`` and ``.bin``
    objects exist. An unavailable Master anchor is an error; falling back to an
    older configured floor could publish stale data. HTTP errors other than
    400/403/404 must be raised by the callback so outages are never misses.
    """
    if master_anchor is not None:
        if not is_safe_resource_version(master_anchor):
            raise ValueError("official Master resourceVersion is not a safe catalog path segment")
        if not exists(master_anchor):
            raise CatalogAnchorUnavailableError(
                f"official Master catalog version is not published: {master_anchor}"
            )
        return CatalogVersionResolution(floor, master_anchor, "master-anchor", 1, master_anchor, True)

    parts = floor.split(".")
    if len(parts) != 4 or not all(part.isascii() and part.isdecimal() for part in parts):
        return CatalogVersionResolution(floor, floor, "config", 0, None, False)

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

    def line_max(
        major: int,
        minor: int,
        patch: int,
        start_build: int,
        *,
        start_known_published: bool = False,
    ) -> int | None:
        if not start_known_published and not published((major, minor, patch, start_build)):
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

    # Haneoka checks higher patch, minor, then major lines, each at build 0,
    # and searches that line's build counter after a hit.
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
    return CatalogVersionResolution(
        floor,
        resolved,
        "probe",
        probes,
        None,
        False,
    )
