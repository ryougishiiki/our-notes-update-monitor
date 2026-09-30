from __future__ import annotations

import json
import random
import time
import urllib.error
import urllib.request
from typing import Any


class HttpError(RuntimeError):
    pass


USER_AGENT = "our-notes-update-monitor/0.1 (+automated data and resource update tracker)"


def fetch_bytes(url: str, *, timeout: float = 30.0, retries: int = 3) -> bytes:
    last_error: Exception | None = None
    for attempt in range(retries):
        try:
            request = urllib.request.Request(
                url,
                headers={
                    "User-Agent": USER_AGENT,
                    "Accept": "application/json, application/octet-stream, */*",
                    "Cache-Control": "no-cache",
                },
            )
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.read()
        except urllib.error.HTTPError as error:
            last_error = error
            if error.code not in (408, 425, 429) and error.code < 500:
                raise HttpError(f"HTTP {error.code} while fetching {url}") from error
        except (urllib.error.URLError, TimeoutError, ConnectionError) as error:
            last_error = error
        if attempt + 1 < retries:
            time.sleep(min(4.0, 0.75 * (attempt + 1)) + random.uniform(0, 0.25))
    raise HttpError(f"failed to fetch {url}: {last_error}")


def fetch_json(url: str, *, timeout: float = 30.0, retries: int = 3) -> Any:
    raw = fetch_bytes(url, timeout=timeout, retries=retries)
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as error:
        raise HttpError(f"invalid UTF-8 JSON response from {url}: {error}") from error
