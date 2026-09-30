from __future__ import annotations

import unittest

from onwatch.snapshot import make_snapshot


class SnapshotTests(unittest.TestCase):
    def test_revision_is_stable_across_probe_timestamps(self) -> None:
        assets = [{"key": "a", "hash": "h", "size": 1, "category": "unknown"}]
        tables = {"MasterCharacter": {"table": "MasterCharacter", "rows": {"1": {"id": 1}}}}
        first = {
            "checkedAt": "2026-09-30T06:00:00Z",
            "sources": {"gameVersion": None, "catalogVersion": "1", "catalogHash": "c", "masterRevision": "m", "resourceManifestRevision": "c"},
            "provenance": {"masterAuthority": "derived"},
        }
        second = dict(first, checkedAt="2026-09-30T06:05:00Z")
        a = make_snapshot(server="intl", probe=first, catalog_sha256="a" * 64, assets=assets, tables=tables, parent=None)
        b = make_snapshot(server="intl", probe=second, catalog_sha256="a" * 64, assets=assets, tables=tables, parent=None)
        self.assertEqual(a["meta"]["revision"], b["meta"]["revision"])
        c = make_snapshot(server="intl", probe=second, catalog_sha256="a" * 64, assets=assets, tables=tables, parent="intl-other")
        self.assertEqual(a["meta"]["contentSha256"], c["meta"]["contentSha256"])
        self.assertNotEqual(a["meta"]["revision"], c["meta"]["revision"])


if __name__ == "__main__":
    unittest.main()
