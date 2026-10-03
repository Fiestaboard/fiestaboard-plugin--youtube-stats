"""Checks the manifest stays consistent with the plugin."""

from plugins.youtube_stats import YouTubeStatsPlugin
from src.text_to_board import take_tiles  # noqa: F401  (core import sanity)


def test_core_fields(manifest_data):
    for field in ("id", "name", "version", "description", "category", "settings_schema", "variables"):
        assert field in manifest_data
    assert manifest_data["id"] == "youtube_stats"
    assert "oauth" not in manifest_data


def test_refresh_default_is_30_minutes(manifest_data):
    refresh = manifest_data["settings_schema"]["properties"]["refresh_seconds"]
    assert refresh["default"] == 1800
    assert manifest_data["min_refresh_seconds"] == refresh["minimum"]


def test_api_key_is_masked(manifest_data):
    assert manifest_data["settings_schema"]["properties"]["api_key"]["ui:widget"] == "password"


def test_teaser_fits_a_note(manifest_data):
    assert len(manifest_data["teaser"].replace("{63}", "#")) <= 15


def test_every_variable_is_produced(manifest_data, youtube_api):
    p = YouTubeStatsPlugin(manifest_data)
    p.config = {"enabled": True, "api_key": "test_key", "channel": "@mapleroad"}
    data = p.fetch_data().data
    for name, spec in manifest_data["variables"]["simple"].items():
        assert name in data, name
        assert len(str(data[name]).replace("{63}", "#")) <= spec["max_length"], name


def test_preview_rows_fit(manifest_data):
    widths = {"flagship": 22, "note": 15}
    for preview in manifest_data["previews"]:
        width = widths.get(preview["device_type"], 15 * preview.get("notes_wide", 1))
        for row in preview["rows"]:
            assert len(row.replace("{63}", "#")) <= width, row
