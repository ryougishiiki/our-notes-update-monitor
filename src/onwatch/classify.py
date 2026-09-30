from __future__ import annotations


CATEGORIES = (
    "music_score",
    "music_audio",
    "jacket",
    "character",
    "live2d",
    "voice",
    "scenario",
    "movie",
    "ui",
    "master",
    "event",
    "system",
    "unknown",
)


def classify_asset(
    primary_key: str,
    internal_id: str,
    bundle_name: str,
    resource_type: str,
) -> str:
    text = " ".join((primary_key, internal_id, bundle_name, resource_type)).casefold()
    rules = (
        ("music_score", ("musicscore", "music_score", "score_")),
        ("music_audio", ("music_audio", "musicsound", "music_sound", "sound/music", "cri_assets_cri_sound")),
        ("jacket", ("jacket", "albumart")),
        ("live2d", ("live2d", "live_2d", "motion3", "model3")),
        ("character", ("character", "chara_")),
        ("voice", ("voice", "scenario_voice")),
        ("scenario", ("scenario", "story", "textasset", "adv_assets_adv_")),
        ("movie", ("movie", "video", ".webm", ".mp4")),
        ("ui", ("/ui/", "_ui_", "uiprefab", "spriteatlas")),
        ("master", ("master", "masterdata")),
        ("event", ("event", "gacha")),
        ("system", ("system", "common", "boot")),
    )
    for category, needles in rules:
        if any(needle in text for needle in needles):
            return category
    return "unknown"
