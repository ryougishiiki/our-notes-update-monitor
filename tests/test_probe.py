from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import onwatch.cli as cli


class ProbeStateTests(unittest.TestCase):
    def test_pending_fingerprint_change_survives_a_later_probe(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "state").mkdir()
            revision = "intl-baseline"
            (root / "snapshots" / revision).mkdir(parents=True)
            (root / "state" / "current.json").write_text(
                json.dumps({"current": revision, "previous": None}), encoding="utf-8"
            )
            scanned = {
                "schema": "our-notes-probe/1",
                "sources": {"catalogHash": "old", "masterRevision": "old"},
            }
            pending = {
                "schema": "our-notes-probe/1",
                "sources": {"catalogHash": "new", "masterRevision": "old"},
            }
            (root / "state" / "scanned.json").write_text(json.dumps(scanned), encoding="utf-8")
            (root / "state" / "probe.json").write_text(json.dumps(pending), encoding="utf-8")
            current = {
                "schema": "our-notes-probe/1",
                "server": "intl",
                "checkedAt": "2026-09-30T06:15:00Z",
                "sources": {"catalogHash": "new", "masterRevision": "old", "gameVersion": None},
                "provenance": {"masterAuthority": "derived"},
                "_masterIndexes": {},
            }
            args = SimpleNamespace(
                config="config/intl.json",
                server="intl",
                root=temp,
                settle_seconds=0,
                stable_checks=2,
                trigger_scan=False,
                github_output=None,
                json=True,
            )
            output = io.StringIO()
            with patch.object(cli, "load_config", return_value=object()), patch.object(
                cli, "fetch_probe", return_value=current
            ), contextlib.redirect_stdout(output):
                result = cli.cmd_probe(args)
            self.assertEqual(result, 0)
            self.assertTrue(json.loads(output.getvalue())["changed"])
            latest = json.loads((root / "state" / "probe.json").read_text("utf-8"))
            saved_baseline = json.loads((root / "state" / "scanned.json").read_text("utf-8"))
            self.assertEqual(latest["checkedAt"], current["checkedAt"])
            self.assertEqual(saved_baseline["sources"]["catalogHash"], "old")


if __name__ == "__main__":
    unittest.main()
