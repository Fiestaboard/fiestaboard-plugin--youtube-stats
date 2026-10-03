"""Tests for the YouTube Stats plugin. All HTTP is faked (see conftest.py)."""

import requests
import pytest
from plugins import youtube_stats as yt
from plugins.youtube_stats import (
    Plugin,
    YouTubeStatsPlugin,
    board_safe,
    compact_number,
    fit,
    parse_channel,
    with_commas,
)

from .conftest import TEST_CHANNEL_ID, TEST_KEY, FakeResponse, api_error, channel_item, channel_list


# ----------------------------------------------------------------------
# Pure helpers
# ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "value,expected",
    [
        (0, "0"),
        (7, "7"),
        (999, "999"),
        (1000, "1K"),
        (1234, "1.23K"),
        (12400, "12.4K"),
        (12499, "12.4K"),
        (100000, "100K"),
        (999999, "999K"),
        (1200000, "1.2M"),
        (1234567, "1.23M"),
        (1999999, "1.99M"),
        (45600000, "45.6M"),
        (2500000000, "2.5B"),
        (3400000000000, "3.4T"),
        ("12400", "12.4K"),
        (-5, "0"),
    ],
)
def test_compact_number(value, expected):
    assert compact_number(value) == expected


def test_compact_number_bad_input():
    assert compact_number(None) == ""
    assert compact_number("lots") == ""


def test_with_commas():
    assert with_commas("1234567") == "1,234,567"
    assert with_commas(12) == "12"
    assert with_commas(None) == ""


@pytest.mark.parametrize(
    "text,expected",
    [
        ("@MapleRoad", ("forHandle", "@MapleRoad")),
        ("MapleRoad", ("forHandle", "@MapleRoad")),
        ("  @maple.road-cooking_1 ", ("forHandle", "@maple.road-cooking_1")),
        (TEST_CHANNEL_ID, ("id", TEST_CHANNEL_ID)),
        (f"https://www.youtube.com/channel/{TEST_CHANNEL_ID}", ("id", TEST_CHANNEL_ID)),
        ("https://www.youtube.com/@MapleRoad", ("forHandle", "@MapleRoad")),
        ("youtube.com/@MapleRoad/videos", ("forHandle", "@MapleRoad")),
        ("https://m.youtube.com/@MapleRoad?si=x", ("forHandle", "@MapleRoad")),
        # Handles may use letters from many scripts, and middle dots.
        ("@café·tv", ("forHandle", "@café·tv")),
        ("https://www.youtube.com/@%E6%96%99%E7%90%86%E3%83%81%E3%83%A3%E3%83%B3%E3%83%8D%E3%83%AB", ("forHandle", "@料理チャンネル")),
        ("", None),
        ("@", None),
        ("has space", None),
        ("@" + "a" * 31, None),
        ("ab", None),
        ("https://www.youtube.com/watch?v=abc", None),
        (None, None),
    ],
)
def test_parse_channel(text, expected):
    assert parse_channel(text) == expected


def test_board_safe_drops_characters_the_board_cannot_show():
    assert board_safe("Café 🎉 {Live}") == "CAFE LIVE"
    assert board_safe(None) == ""


def test_fit_counts_colour_markers_as_one_tile():
    assert fit("ABCDEFGHIJ", 4) == "ABCD"
    assert fit("{63} ABC", 3) == "{63} A"


def test_export_hook():
    assert Plugin is YouTubeStatsPlugin


# ----------------------------------------------------------------------
# Config validation
# ----------------------------------------------------------------------


def test_plugin_id(plugin):
    assert plugin.plugin_id == "youtube_stats"


def test_validate_config_ok(plugin):
    assert plugin.validate_config({"api_key": TEST_KEY, "channel": "@mapleroad"}) == []


def test_validate_config_missing(plugin):
    errors = plugin.validate_config({})
    assert any("API key" in e for e in errors)
    assert any("channel" in e.lower() for e in errors)


def test_validate_config_bad_channel(plugin):
    errors = plugin.validate_config({"api_key": TEST_KEY, "channel": "not a channel"})
    assert errors and "handle" in errors[0]


def test_validate_config_bad_refresh(plugin):
    errors = plugin.validate_config({"api_key": TEST_KEY, "channel": "@a1b", "refresh_seconds": 5})
    assert errors


# ----------------------------------------------------------------------
# Fetching
# ----------------------------------------------------------------------


def test_fetch_by_handle(plugin, youtube_api):
    result = plugin.fetch_data()
    assert result.available, result.error
    call = youtube_api.calls[0]
    assert call["params"] == {"part": "snippet,statistics", "forHandle": "@mapleroad", "key": TEST_KEY}
    assert call["timeout"] == yt.REQUEST_TIMEOUT_SECONDS
    assert "FiestaBoard" in call["headers"]["User-Agent"]
    d = result.data
    assert d["channel_name"] == "MAPLE ROAD COOKING"
    assert d["handle"] == "@MAPLEROAD"
    assert d["logo_tile"] == "{63}"
    assert d["subscribers"] == "12.4K"
    assert d["subscribers_exact"] == "12,400"
    assert d["subscribers_hidden"] == "NO"
    assert d["subscriber_count"] == 12400
    assert d["views"] == "1.23M"
    assert d["views_exact"] == "1,234,567"
    assert d["view_count"] == 1234567
    assert d["videos"] == "245"
    assert d["videos_exact"] == "245"
    assert d["video_count"] == 245
    assert d["subscribers_line"] == "12.4K SUBSCRIBERS"
    assert d["views_line"] == "1.23M VIEWS"
    assert d["videos_line"] == "245 VIDEOS"
    assert result.formatted_lines == [
        "{63} MAPLE ROAD COOKING",
        "",
        "SUBSCRIBERS      12.4K",
        "VIEWS            1.23M",
        "VIDEOS             245",
        "",
    ]


def test_fetch_by_id(make_plugin, youtube_api):
    plugin = make_plugin(channel=TEST_CHANNEL_ID)
    assert plugin.fetch_data().available
    assert youtube_api.calls[0]["params"]["id"] == TEST_CHANNEL_ID
    assert "forHandle" not in youtube_api.calls[0]["params"]


def test_singular_labels(plugin, youtube_api):
    youtube_api.body = channel_list(channel_item(subscribers="1", views="1", videos="1"))
    d = plugin.fetch_data().data
    assert d["subscribers_line"] == "1 SUBSCRIBER"
    assert d["views_line"] == "1 VIEW"
    assert d["videos_line"] == "1 VIDEO"


def test_hidden_subscribers(plugin, youtube_api):
    youtube_api.body = channel_list(channel_item(hidden=True))
    result = plugin.fetch_data()
    d = result.data
    assert d["subscribers"] == "HIDDEN"
    assert d["subscribers_exact"] == "HIDDEN"
    assert d["subscribers_hidden"] == "YES"
    assert d["subscriber_count"] is None
    assert d["subscribers_line"] == "SUBSCRIBERS HIDDEN"
    assert result.formatted_lines[2] == "SUBSCRIBERS     HIDDEN"


def test_missing_fields_are_tolerated(plugin, youtube_api):
    youtube_api.body = channel_list({"id": TEST_CHANNEL_ID, "statistics": {}})
    d = plugin.fetch_data().data
    assert d["channel_name"] == ""
    assert d["handle"] == ""
    assert d["views"] == "0"
    assert d["subscribers"] == "0"


def test_small_board_layout(plugin, youtube_api):
    lines = plugin._format_display(plugin.fetch_data().data, 15, 3)
    assert lines == ["{63} MAPLE ROAD", "12.4K SUBS", "1.23M VIEWS"]


def test_get_formatted_display(plugin, youtube_api):
    assert plugin.get_formatted_display()[0] == "{63} MAPLE ROAD COOKING"


def test_get_formatted_display_unavailable(make_plugin, youtube_api):
    assert make_plugin(api_key="").get_formatted_display() is None


def test_board_size_follows_board(plugin):
    class Board:
        cols, rows = 15, 3

    plugin._bound = Board()
    type(plugin).board = property(lambda self: getattr(self, "_bound", None))
    try:
        assert plugin._board_size() == (15, 3)
    finally:
        del type(plugin).board


# ----------------------------------------------------------------------
# Caching
# ----------------------------------------------------------------------


def test_snapshot_shared_between_boards(plugin, youtube_api, clock):
    plugin.fetch_data()
    plugin.fetch_data()
    assert len(youtube_api.calls) == 1
    clock.advance(yt.SNAPSHOT_TTL_SECONDS + 1)
    plugin.fetch_data()
    assert len(youtube_api.calls) == 2


def test_config_change_clears_cache(plugin, youtube_api):
    plugin.fetch_data()
    old = dict(plugin.config)
    plugin.config = {**old, "channel": TEST_CHANNEL_ID}
    plugin.on_config_change(old, plugin.config)
    plugin.fetch_data()
    assert len(youtube_api.calls) == 2
    assert youtube_api.calls[1]["params"]["id"] == TEST_CHANNEL_ID


# ----------------------------------------------------------------------
# Not configured and errors
# ----------------------------------------------------------------------


def test_no_key_makes_no_request(make_plugin, youtube_api):
    result = make_plugin(api_key="  ").fetch_data()
    assert not result.available
    assert "API key" in result.error
    assert youtube_api.calls == []


def test_bad_channel_makes_no_request(make_plugin, youtube_api):
    result = make_plugin(channel="not a channel").fetch_data()
    assert not result.available
    assert "handle" in result.error
    assert youtube_api.calls == []


def test_channel_not_found(plugin, youtube_api):
    youtube_api.body = {"kind": "youtube#channelListResponse", "pageInfo": {"totalResults": 0}}
    result = plugin.fetch_data()
    assert not result.available
    assert "@mapleroad" in result.error and "found" in result.error


@pytest.mark.parametrize(
    "response,needle",
    [
        (api_error(400, "badRequest", "API key not valid. Please pass a valid API key."), "rejected the API key"),
        (api_error(400, "keyInvalid"), "rejected the API key"),
        (api_error(400, "keyExpired"), "rejected the API key"),
        (api_error(403, "quotaExceeded"), "quota"),
        (api_error(403, "dailyLimitExceeded"), "quota"),
        (api_error(403, "accessNotConfigured"), "enable"),
        (api_error(403, "SERVICE_DISABLED"), "enable"),
        (api_error(403, "ipRefererBlocked"), "restrictions"),
        (api_error(403, "forbidden"), "restrictions"),
        (api_error(404, "channelNotFound"), "found"),
        (api_error(400, "invalidCriteria", "weird"), "400"),
    ],
)
def test_api_errors(plugin, youtube_api, response, needle):
    youtube_api.respond(response)
    result = plugin.fetch_data()
    assert not result.available
    assert needle in result.error


def test_unparseable_error_body(plugin, youtube_api):
    youtube_api.respond(FakeResponse(None, 502))
    result = plugin.fetch_data()
    assert not result.available
    assert "502" in result.error


def test_bad_json_on_success(plugin, youtube_api):
    youtube_api.respond(FakeResponse(None, 200))
    result = plugin.fetch_data()
    assert not result.available
    assert "unexpected" in result.error.lower()


def test_timeout(plugin, youtube_api):
    youtube_api.respond(requests.exceptions.Timeout("slow"))
    result = plugin.fetch_data()
    assert not result.available
    assert "reach YouTube" in result.error


def test_error_cooldown_avoids_hammering(plugin, youtube_api, clock):
    youtube_api.respond(api_error(400, "keyInvalid"))
    first = plugin.fetch_data()
    second = plugin.fetch_data()
    assert len(youtube_api.calls) == 1
    assert second.error == first.error
    clock.advance(yt.AUTH_COOLDOWN_SECONDS + 1)
    assert plugin.fetch_data().available
    assert len(youtube_api.calls) == 2


def test_transient_error_keeps_last_good_data(plugin, youtube_api, clock):
    assert plugin.fetch_data().available
    clock.advance(yt.SNAPSHOT_TTL_SECONDS + 1)
    youtube_api.respond(FakeResponse(None, 503))
    result = plugin.fetch_data()
    assert result.available
    assert result.data["subscribers"] == "12.4K"
    clock.advance(yt.STALE_DATA_LIMIT_SECONDS + 1)
    youtube_api.respond(FakeResponse(None, 503))
    assert not plugin.fetch_data().available


def test_quota_error_keeps_last_good_data(plugin, youtube_api, clock):
    plugin.fetch_data()
    clock.advance(yt.SNAPSHOT_TTL_SECONDS + 1)
    youtube_api.respond(api_error(403, "quotaExceeded"))
    assert plugin.fetch_data().available


def test_bad_key_does_not_show_stale_data(plugin, youtube_api, clock):
    plugin.fetch_data()
    clock.advance(yt.SNAPSHOT_TTL_SECONDS + 1)
    youtube_api.respond(api_error(400, "keyInvalid"))
    assert not plugin.fetch_data().available


def test_fetch_never_raises(plugin, youtube_api, monkeypatch):
    monkeypatch.setattr(plugin, "_build_data", lambda *_: 1 / 0)
    result = plugin.fetch_data()
    assert not result.available
    assert "division" in result.error


def test_key_not_logged_in_error(plugin, youtube_api):
    youtube_api.respond(api_error(400, "keyInvalid", f"bad key {TEST_KEY}"))
    assert TEST_KEY not in plugin.fetch_data().error
