from __future__ import annotations

import gzip
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

from py3rijndael import Pkcs7Padding, RijndaelCbc

from onwatch.sources.master import (
    compare_master_mirror,
    fetch_official_master_tables,
    validate_master_manifest,
)


CONTRACT = Path(__file__).parent / "fixtures" / "official_master_manifest_contract.json"
VERSION = "74639bc3f98486a22b1232f232213def"
ROOT = "https://master.example.test/master"
CRYPTO = {
    "scheme": "haneoka-rijndael-cbc-v2",
    "salt": "b50b23a5fd628c3dc386f7488f81d6b0450b8c89671574f55a3ad815f10b8e30",
    "key": "0532791c510a08eb7ede6b46c6ba71ea9aa2a3cfb678a595f89d67c8a5e493b6",
    "iv": "9b94c91e242a562e97fcd505b75a5129b2b80632a269034cf942ec7293d489a1",
}


def _encrypted_table(rows: list[dict]) -> bytes:
    salt, key, iv = (bytes.fromhex(CRYPTO[name]) for name in ("salt", "key", "iv"))
    cipher = RijndaelCbc(key, iv, Pkcs7Padding(32), block_size=32)
    payload = gzip.compress(json.dumps({"_allData": rows}, separators=(",", ":")).encode())
    return salt + iv + cipher.encrypt(payload)


def _source_inputs():
    table_rows = {
        "MasterLiveMusic.bin": [
            {"_id": 100107, "_easyID": 10010700, "_normalID": 10010701, "_hardID": 10010702, "_expertID": 10010703}
        ],
        "MasterLiveMusicScore.bin": [
            {"_id": 10010700, "_musicScoreTextFileName": "0107/0107_00", "_fullComboCount": 320, "_musicScoreLevel": 6},
            {"_id": 10010701, "_musicScoreTextFileName": "0107/0107_01", "_fullComboCount": 499, "_musicScoreLevel": 12},
            {"_id": 10010702, "_musicScoreTextFileName": "0107/0107_02", "_fullComboCount": 646, "_musicScoreLevel": 18},
            {"_id": 10010703, "_musicScoreTextFileName": "0107/0107_03", "_fullComboCount": 960, "_musicScoreLevel": 25},
        ],
        "MasterEvent.bin": [{"_id": 42, "_startAt": "2026/09/24 0:00:00"}],
        "MasterCharacter.bin": [{"_id": 6, "_characterNameTextID": "Character_Name_6"}],
    }
    binaries = {name: _encrypted_table(rows) for name, rows in table_rows.items()}
    manifest = {
        "version": VERSION,
        "files": [
            {"name": name, "size": len(raw), "hash": hashlib.sha256(raw).hexdigest()}
            for name, raw in binaries.items()
        ],
    }
    manifest_raw = json.dumps(manifest, separators=(",", ":")).encode()
    responses = {
        f"{ROOT}/{VERSION}/MasterManifest.json": manifest_raw,
        **{f"{ROOT}/{VERSION}/{name}": raw for name, raw in binaries.items()},
    }
    return manifest, manifest_raw, responses


def _config():
    return SimpleNamespace(
        master_remote_root=ROOT,
        master_crypto=CRYPTO,
        request_timeout_seconds=2,
        master_base_url="https://haneoka.org/api/v1/servers/intl",
        master_endpoints={"songs": "songs", "events": "events", "characters": "characters"},
    )


class OfficialMasterTests(TestCase):
    def test_shared_manifest_fixture_contract(self):
        contract = json.loads(CONTRACT.read_text("utf-8"))
        self.assertEqual(
            validate_master_manifest(contract["valid"], contract["expectedVersion"]),
            contract["valid"],
        )
        for case in contract["invalid"]:
            with self.subTest(case=case["name"]), self.assertRaises(ValueError):
                validate_master_manifest(case["value"], contract["expectedVersion"])

    def test_all_required_official_tables_load_after_integrity_and_decryption(self):
        _manifest, raw_manifest, responses = _source_inputs()
        fetcher = lambda url, **_kwargs: responses[url]
        tables = fetch_official_master_tables(
            _config(), VERSION, hashlib.sha256(raw_manifest).hexdigest(), fetcher=fetcher
        )
        self.assertEqual(set(tables), {"MasterLiveMusic", "MasterLiveMusicScore", "MasterEvent", "MasterCharacter"})
        self.assertTrue(all(table["authority"] == "official" for table in tables.values()))
        self.assertEqual(len(tables["MasterLiveMusic"]["rows"]), 1)
        self.assertEqual(len(tables["MasterLiveMusicScore"]["rows"]), 4)
        self.assertEqual(tables["MasterLiveMusic"]["rows"]["100107"]["_id"], 100107)

    def test_hash_and_size_mismatches_fail_closed(self):
        for key in ("hash", "size"):
            with self.subTest(mismatch=key):
                manifest, raw_manifest, responses = _source_inputs()
                entry = manifest["files"][0]
                if key == "hash":
                    entry[key] = "0" * 64
                else:
                    entry[key] += 1
                changed_manifest = json.dumps(manifest).encode()
                responses[f"{ROOT}/{VERSION}/MasterManifest.json"] = changed_manifest
                with self.assertRaisesRegex(RuntimeError, "OFFICIAL_MASTER_TABLE_INTEGRITY_FAILED"):
                    fetch_official_master_tables(
                        _config(), VERSION, hashlib.sha256(changed_manifest).hexdigest(),
                        fetcher=lambda url, **_kwargs: responses[url],
                    )

    def test_missing_required_file_and_partial_response_fail(self):
        manifest, raw_manifest, responses = _source_inputs()
        manifest["files"] = [entry for entry in manifest["files"] if entry["name"] != "MasterCharacter.bin"]
        changed_manifest = json.dumps(manifest).encode()
        responses[f"{ROOT}/{VERSION}/MasterManifest.json"] = changed_manifest
        with self.assertRaisesRegex(RuntimeError, "OFFICIAL_MASTER_MANIFEST_INVALID"):
            fetch_official_master_tables(
                _config(), VERSION, hashlib.sha256(changed_manifest).hexdigest(),
                fetcher=lambda url, **_kwargs: responses[url],
            )

        _manifest, raw_manifest, responses = _source_inputs()
        responses.pop(f"{ROOT}/{VERSION}/MasterCharacter.bin")
        with self.assertRaises(KeyError):
            fetch_official_master_tables(
                _config(), VERSION, hashlib.sha256(raw_manifest).hexdigest(),
                fetcher=lambda url, **_kwargs: responses[url],
            )

    def test_mirror_lag_is_reported_but_does_not_change_official_rows(self):
        _manifest, raw_manifest, responses = _source_inputs()
        tables = fetch_official_master_tables(
            _config(), VERSION, hashlib.sha256(raw_manifest).hexdigest(),
            fetcher=lambda url, **_kwargs: responses[url],
        )
        mirror = {
            "100106": {"musicId": 100106, "difficulty": []},
        }
        with patch("onwatch.sources.master.fetch_json", return_value=mirror):
            comparison, _catalog = compare_master_mirror(_config(), tables)
        self.assertEqual(comparison["status"], "MIRROR_LAG_CONFIRMED")
        self.assertEqual(comparison["officialOnlyMusicIds"], [100107])
        self.assertEqual(comparison["mirrorOnlyMusicIds"], [100106])
