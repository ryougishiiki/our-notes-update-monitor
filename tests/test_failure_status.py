from __future__ import annotations

import unittest

from onwatch.cli import _failure_status
from onwatch.http import HttpError, InvalidResponseError
from onwatch.scan import IncompleteSnapshotError


class FailureStatusTests(unittest.TestCase):
    def test_source_and_schema_errors_have_separate_alert_codes(self) -> None:
        self.assertEqual(_failure_status(HttpError("HTTP 503")), "SOURCE_UNAVAILABLE")
        self.assertEqual(_failure_status(InvalidResponseError("invalid JSON")), "SCHEMA_INVALID")
        self.assertEqual(_failure_status(IncompleteSnapshotError("empty source")), "SCHEMA_INVALID")
        self.assertEqual(_failure_status(RuntimeError("unexpected")), "SCAN_FAILED")


if __name__ == "__main__":
    unittest.main()
