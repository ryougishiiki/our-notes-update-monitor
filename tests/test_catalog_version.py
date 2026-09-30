from __future__ import annotations

import json
import urllib.error
import unittest
from pathlib import Path
from unittest.mock import patch

from onwatch import catalog_version
from onwatch.http import HttpError, probe_exists


FIXTURE = Path(__file__).parent / "fixtures" / "catalog_version_probe_contract.json"


class CatalogVersionResolverTests(unittest.TestCase):
    def test_shared_probe_contract_cases(self) -> None:
        contract = json.loads(FIXTURE.read_text("utf-8"))
        self.assertEqual(catalog_version.MAX_CONSECUTIVE_MISSES, contract["maxConsecutiveMisses"])
        self.assertEqual(catalog_version.MAX_BUILD_ADVANCE, contract["maxBuildAdvance"])
        self.assertEqual(catalog_version.MAX_LINE_ADVANCE, contract["maxLineAdvance"])
        self.assertEqual(catalog_version.MAX_PROBE_REQUESTS, contract["maxProbeRequests"])
        for case in contract["cases"]:
            with self.subTest(case=case["name"]):
                hits = set(case["existing"])
                result = catalog_version.resolve_catalog_version(case["floor"], hits.__contains__)
                self.assertEqual(result.resolved, case["expected"])
                self.assertEqual(result.source, "probe")

    def test_nonstandard_version_is_returned_without_network_probes(self) -> None:
        called: list[str] = []
        result = catalog_version.resolve_catalog_version("1.0.0-beta", called.append)
        self.assertEqual(result.resolved, "1.0.0-beta")
        self.assertEqual(result.source, "config")
        self.assertEqual(result.probes, 0)
        self.assertEqual(called, [])

    def test_http_missing_statuses_are_misses(self) -> None:
        for status in (400, 403, 404):
            with self.subTest(status=status):
                error = urllib.error.HTTPError(
                    "https://cdn.invalid/catalog_1.0.0.101.hash", status, "missing", {}, None
                )
                with patch("onwatch.http.urllib.request.urlopen", side_effect=error):
                    self.assertFalse(probe_exists("https://cdn.invalid/catalog_1.0.0.101.hash"))

    def test_http_server_error_retries_then_fails_instead_of_becoming_a_miss(self) -> None:
        error = urllib.error.HTTPError(
            "https://cdn.invalid/catalog_1.0.0.101.hash", 503, "unavailable", {}, None
        )
        with patch("onwatch.http.urllib.request.urlopen", side_effect=error) as request, patch(
            "onwatch.http.time.sleep"
        ):
            with self.assertRaisesRegex(HttpError, "failed to probe"):
                probe_exists("https://cdn.invalid/catalog_1.0.0.101.hash", retries=3)
        self.assertEqual(request.call_count, 3)

    def test_probe_request_budget_fails_closed(self) -> None:
        with patch.object(catalog_version, "MAX_PROBE_REQUESTS", 4):
            checked: list[str] = []
            with self.assertRaises(catalog_version.CatalogProbeLimitError):
                catalog_version.resolve_catalog_version(
                    "1.0.0.100", lambda version: checked.append(version) or True
                )
        self.assertEqual(len(checked), 4)

    def test_line_search_limit_fails_closed(self) -> None:
        with patch.object(catalog_version, "MAX_BUILD_ADVANCE", 0), patch.object(
            catalog_version, "MAX_LINE_ADVANCE", 1
        ):
            with self.assertRaisesRegex(catalog_version.CatalogProbeLimitError, "higher lines"):
                catalog_version.resolve_catalog_version("1.0.0.100", lambda _version: True)


if __name__ == "__main__":
    unittest.main()
