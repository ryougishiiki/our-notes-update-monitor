from __future__ import annotations

import copy
import json
import os
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import onwatch.scan as scan_module
from onwatch.notify import CHARTDB_EVENT_TYPE, notify_latest


FIXTURES = Path(__file__).parent / "fixtures"


class FakeResponse:
    status = 204

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class OfflinePipelineAcceptanceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.config = SimpleNamespace(
            server="intl",
            remote_root="https://cdn.invalid",
            catalog_url_for=lambda version: f"https://cdn.invalid/catalog_{version}.bin",
            request_timeout_seconds=1,
            display_timezone="Asia/Shanghai",
            allow_empty_tables=frozenset(),
        )
        self.active = self._fixture("snapshot-a.json")
        self.patchers = [
            patch.object(scan_module, "fetch_probe", side_effect=self._probe),
            patch.object(scan_module, "fetch_bytes", side_effect=self._catalog_bytes),
            patch.object(scan_module, "parse_catalog", side_effect=lambda data, *args: data),
            patch.object(scan_module, "normalize_catalog", side_effect=self._assets),
            patch.object(scan_module, "fetch_official_master_tables", side_effect=self._tables),
            patch.object(scan_module, "compare_master_mirror", side_effect=self._mirror),
        ]
        for patcher in self.patchers:
            patcher.start()

    def tearDown(self) -> None:
        for patcher in reversed(self.patchers):
            patcher.stop()
        self.temp.cleanup()

    def test_baseline_noop_then_two_idempotent_update_events(self) -> None:
        baseline = scan_module.run_scan(self.root, self.config)
        self.assertTrue(baseline["baseline"])
        self.assertEqual(self._event_count(), 0)
        status = json.loads((self.root / "state" / "status.json").read_text("utf-8"))
        self.assertTrue(status["healthy"])
        self.assertEqual(status["currentSnapshot"], baseline["snapshotRevision"])
        self.assertIsNone(status["latestEvent"])
        self.assertEqual(status["catalogVersionSource"], "probe")
        self.assertEqual(status["catalogVersionConfiguredFloor"], "1.0.0.100")
        self.assertEqual(status["catalogVersionResolved"], "1.0.0.100")
        self.assertEqual(
            status["gameVersionAvailable"], self.active["sources"]["gameVersion"] is not None
        )
        self.assertEqual(status["masterAuthority"], "official")

        no_op_files = [
            "state/current.json",
            "state/probe.json",
            "state/scanned.json",
            "state/status.json",
            "feed/latest.json",
            "site/api/status.json",
        ]
        no_op_before = {name: (self.root / name).read_bytes() for name in no_op_files}
        no_change = scan_module.run_scan(self.root, self.config)
        self.assertFalse(no_change["changed"])
        self.assertEqual(self._event_count(), 0)
        self.assertEqual(
            no_op_before,
            {name: (self.root / name).read_bytes() for name in no_op_files},
        )

        self.active = self._fixture("snapshot-b.json")
        event_one = scan_module.run_scan(self.root, self.config)
        self.assertEqual(event_one["summary"]["assetsChanged"], 1)
        self.assertEqual(self._event_count(), 1)

        rerun = scan_module.run_scan(self.root, self.config)
        self.assertFalse(rerun["changed"])
        self.assertEqual(self._event_count(), 1)

        self.active = self._fixture("snapshot-c.json")
        event_two = scan_module.run_scan(self.root, self.config)
        self.assertEqual(event_two["summary"]["masterAdded"], 2)
        self.assertEqual(event_two["summary"]["songsAdded"], 1)
        self.assertEqual(event_two["summary"]["chartsAdded"], 1)
        self.assertEqual(event_two["summary"]["chartsChanged"], 1)
        self.assertEqual(event_two["severity"], "CONTENT")
        self.assertEqual(self._event_count(), 2)

        duplicate = scan_module.run_scan(self.root, self.config)
        self.assertFalse(duplicate["changed"])
        self.assertEqual(self._event_count(), 2)

        feed_index = json.loads((self.root / "feed" / "index.json").read_text("utf-8"))
        latest = json.loads((self.root / "feed" / "latest.json").read_text("utf-8"))
        self.assertEqual(feed_index["count"], 2)
        self.assertEqual(latest["latest"], event_two["eventId"])
        site_index = json.loads((self.root / "site" / "api" / "events" / "index.json").read_text("utf-8"))
        self.assertEqual(site_index["events"][0]["url"], f"{event_two['eventId']}.json")
        event_doc = json.loads((self.root / "events" / f"{event_two['eventId']}.json").read_text("utf-8"))
        self.assertEqual(event_doc["schema"], "our-notes-update-event/1")
        self.assertEqual(event_doc["charts"]["added"], ["live_assets_live_musicscore_0001_0002_03_b.bundle"])
        self.assertTrue((self.root / "site" / "reports" / f"{event_two['eventId']}.md").is_file())
        feed_xml = ET.parse(self.root / "feed.xml").getroot()
        self.assertEqual(len([child for child in feed_xml if child.tag.endswith("entry")]), 2)

        with patch.dict(
            os.environ,
            {
                "ONWATCH_CHARTDB_REPOSITORY": "ryougishiiki/our-notes-chartdb",
                "ONWATCH_GITHUB_TOKEN": "test-token",
                "ONWATCH_WEBHOOK_URL": "",
                "ONWATCH_WEBHOOK_SECRET": "",
            },
        ):
            with patch("onwatch.notify.urllib.request.urlopen", return_value=FakeResponse()) as dispatch:
                first_notification = notify_latest(self.root)
                second_notification = notify_latest(self.root)
        self.assertEqual(first_notification["repositoryDispatch"]["status"], "sent")
        self.assertEqual(second_notification["repositoryDispatch"]["status"], "already-sent")
        self.assertEqual(dispatch.call_count, 1)
        payload = json.loads(dispatch.call_args.args[0].data.decode("utf-8"))
        self.assertEqual(payload["event_type"], CHARTDB_EVENT_TYPE)
        self.assertEqual(payload["client_payload"]["eventId"], event_two["eventId"])
        self.assertEqual(
            set(payload["client_payload"]),
            {
                "eventId", "catalogVersion", "catalogHash", "masterVersion",
                "masterResourceVersion", "masterManifestSha256", "songs", "added", "changed", "removed"
            },
        )

    def test_catalog_version_only_change_emits_event_and_dispatch(self) -> None:
        baseline = scan_module.run_scan(self.root, self.config)
        self.assertTrue(baseline["baseline"])

        self.active["sources"]["catalogVersion"] = "1.0.0.101"
        self.active["sources"]["catalogVersionResolved"] = "1.0.0.101"
        event_result = scan_module.run_scan(self.root, self.config)
        self.assertTrue(event_result["changed"])
        self.assertEqual(event_result["summary"]["chartsAdded"], 0)
        event = json.loads(
            (self.root / "events" / f"{event_result['eventId']}.json").read_text("utf-8")
        )
        self.assertEqual(event["source"]["catalogVersionBefore"], "1.0.0.100")
        self.assertEqual(event["source"]["catalogVersionAfter"], "1.0.0.101")

        version_file = self.root / "snapshots" / event_result["snapshotRevision"] / "version.json"
        version = json.loads(version_file.read_text("utf-8"))
        self.assertEqual(version["catalogVersionResolved"], "1.0.0.101")
        self.assertEqual(version["catalogVersionSource"], "probe")
        for state_name in ("probe.json", "scanned.json"):
            persisted = json.loads((self.root / "state" / state_name).read_text("utf-8"))
            self.assertEqual(persisted["sources"]["catalogVersionResolved"], "1.0.0.101")
            self.assertEqual(persisted["provenance"]["catalogVersionSource"], "probe")
        status = json.loads((self.root / "state" / "status.json").read_text("utf-8"))
        self.assertEqual(status["catalogVersionResolved"], "1.0.0.101")

        with patch.dict(
            os.environ,
            {
                "ONWATCH_CHARTDB_REPOSITORY": "ryougishiiki/our-notes-chartdb",
                "ONWATCH_GITHUB_TOKEN": "test-token",
                "ONWATCH_WEBHOOK_URL": "",
                "ONWATCH_WEBHOOK_SECRET": "",
            },
        ):
            with patch("onwatch.notify.urllib.request.urlopen", return_value=FakeResponse()) as dispatch:
                notification = notify_latest(self.root)
        self.assertEqual(notification["repositoryDispatch"]["status"], "sent")
        payload = json.loads(dispatch.call_args.args[0].data.decode("utf-8"))
        self.assertEqual(payload["client_payload"]["catalogVersion"], "1.0.0.101")
        self.assertEqual(payload["client_payload"]["added"], [])

    def test_master_version_only_change_emits_event_and_dispatch(self) -> None:
        scan_module.run_scan(self.root, self.config)
        self.active["sources"]["masterVersion"] = "master-version-b"
        event_result = scan_module.run_scan(self.root, self.config)
        self.assertTrue(event_result["changed"])
        self.assertEqual(event_result["summary"]["chartsAdded"], 0)
        self.assertEqual(event_result["summary"]["songsAdded"], 0)
        event = json.loads(
            (self.root / "events" / f"{event_result['eventId']}.json").read_text("utf-8")
        )
        self.assertEqual(event["source"]["masterVersionBefore"], "master-version-a")
        self.assertEqual(event["source"]["masterVersionAfter"], "master-version-b")

        with patch.dict(
            os.environ,
            {
                "ONWATCH_CHARTDB_REPOSITORY": "ryougishiiki/our-notes-chartdb",
                "ONWATCH_GITHUB_TOKEN": "test-token",
                "ONWATCH_WEBHOOK_URL": "",
                "ONWATCH_WEBHOOK_SECRET": "",
            },
        ):
            with patch("onwatch.notify.urllib.request.urlopen", return_value=FakeResponse()) as dispatch:
                notification = notify_latest(self.root)
        self.assertEqual(notification["repositoryDispatch"]["status"], "sent")
        payload = json.loads(dispatch.call_args.args[0].data.decode("utf-8"))
        self.assertEqual(payload["client_payload"]["masterVersion"], "master-version-b")

    def test_empty_catalog_rejection_does_not_advance_current(self) -> None:
        scan_module.run_scan(self.root, self.config)
        current_before = json.loads((self.root / "state" / "current.json").read_text("utf-8"))
        self.active = self._fixture("snapshot-b.json")
        self.active["assets"] = []
        with self.assertRaises(scan_module.IncompleteSnapshotError):
            scan_module.run_scan(self.root, self.config)
        current_after = json.loads((self.root / "state" / "current.json").read_text("utf-8"))
        self.assertEqual(current_after, current_before)
        self.assertEqual(self._event_count(), 0)

    def _fixture(self, name: str) -> dict:
        return json.loads((FIXTURES / name).read_text("utf-8"))

    def _probe(self, _config):
        sources = copy.deepcopy(self.active["sources"])
        sources.setdefault("catalogVersionResolved", sources.get("catalogVersion"))
        sources.setdefault("catalogVersionConfiguredFloor", "1.0.0.100")
        sources.setdefault("masterVersion", "master-version-a")
        sources.setdefault("masterResourceVersion", "1.0.0.201")
        sources.setdefault("masterManifestSha256", "a" * 64)
        return {
            "schema": "our-notes-probe/1",
            "server": "intl",
            "checkedAt": self.active["checkedAt"],
            "sources": sources,
            "provenance": {"masterAuthority": "official", "catalogVersionSource": "probe"},
            "_masterVersion": None,
        }

    def _catalog_bytes(self, _url, **_kwargs):
        return self.active["catalogBytes"].encode("utf-8")

    def _assets(self, _reader):
        return copy.deepcopy(self.active["assets"])

    def _tables(self, _config, _version, _manifest_sha256):
        return {
            name: copy.deepcopy(table)
            for name, table in self.active["tables"].items()
            if name != "MusicCatalog"
        }

    def _mirror(self, _config, _tables):
        return ({"status": "MIRROR_CURRENT", "mirrorRevision": "mirror-a"}, {})

    def test_new_master_song_with_preloaded_catalog_chart_is_not_a_new_chart(self) -> None:
        scan_module.run_scan(self.root, self.config)
        self.active = self._fixture("snapshot-c.json")
        self.active["assets"] = self._fixture("snapshot-a.json")["assets"]
        result = scan_module.run_scan(self.root, self.config)
        self.assertEqual(result["summary"]["songsAdded"], 1)
        self.assertEqual(result["summary"]["chartsAdded"], 0)
        event = json.loads(
            (self.root / "events" / f"{result['eventId']}.json").read_text("utf-8")
        )
        self.assertEqual(event["charts"]["added"], [])

    def _event_count(self) -> int:
        directory = self.root / "events"
        return len(list(directory.glob("*.json"))) if directory.exists() else 0


if __name__ == "__main__":
    unittest.main()
