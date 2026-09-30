from __future__ import annotations

import unittest

from onwatch.classify import classify_asset
from onwatch.diff import diff_assets, diff_master


class DiffTests(unittest.TestCase):
    def test_asset_add_change_remove(self) -> None:
        before = [
            {"key": "same", "hash": "a", "size": 1},
            {"key": "removed", "hash": "b", "size": 2},
        ]
        after = [
            {"key": "same", "hash": "c", "size": 1},
            {"key": "added", "hash": "d", "size": 3},
        ]
        result = diff_assets(before, after)
        self.assertEqual([item["key"] for item in result["added"]], ["added"])
        self.assertEqual([item["key"] for item in result["changed"]], ["same"])
        self.assertEqual([item["key"] for item in result["removed"]], ["removed"])

    def test_master_diff_is_row_semantic_and_flattens_nested_fields(self) -> None:
        before = {"MasterEvent": {"rows": {"7": {"start": {"at": 10}, "name": "a"}}}}
        after = {"MasterEvent": {"rows": {"7": {"start": {"at": 20}, "name": "a"}, "8": {"name": "b"}}}}
        result = diff_master(before, after)
        self.assertEqual([item["type"] for item in result], ["added", "changed"])
        self.assertEqual(result[0]["key"], 8)
        self.assertEqual(result[1]["fields"]["start.at"], {"before": 10, "after": 20})

    def test_classifier_keeps_unknown_without_guessing(self) -> None:
        self.assertEqual(classify_asset("new_asset", "", "", "Custom.Type"), "unknown")
        self.assertEqual(classify_asset("live_assets_live_musicscore_a", "", "", ""), "music_score")
        self.assertEqual(classify_asset("cri_assets_cri_sound_track.bundle", "", "", ""), "music_audio")
        self.assertEqual(classify_asset("adv_assets_adv_episode_event.bundle", "", "", ""), "scenario")


if __name__ == "__main__":
    unittest.main()
