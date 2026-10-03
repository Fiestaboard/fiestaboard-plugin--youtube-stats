"""Shared fixtures for the YouTube Stats plugin tests.

``youtube_api`` patches ``requests.get`` with a fake YouTube Data API v3
``channels.list`` endpoint that answers with payloads shaped like the real
ones. No test touches the network. Keys and channels are invented.
"""

import json
from collections import deque
from pathlib import Path
from unittest.mock import patch

import pytest
from plugins.youtube_stats import YouTubeStatsPlugin

MANIFEST_PATH = Path(__file__).parent.parent / "manifest.json"
CHANNELS_URL = "https://www.googleapis.com/youtube/v3/channels"
TEST_KEY = "test_api_key_example"
TEST_CHANNEL_ID = "UCabcdefghijklmnopqrstuv"  # fake, 24 characters


def channel_item(
    *,
    title="Maple Road Cooking",
    custom_url="@mapleroad",
    subscribers="12400",
    views="1234567",
    videos="245",
    hidden=False,
):
    stats = {"viewCount": views, "hiddenSubscriberCount": hidden, "videoCount": videos}
    if not hidden:
        stats["subscriberCount"] = subscribers
    return {
        "kind": "youtube#channel",
        "etag": "test_etag",
        "id": TEST_CHANNEL_ID,
        "snippet": {"title": title, "customUrl": custom_url},
        "statistics": stats,
    }


def channel_list(*items):
    return {
        "kind": "youtube#channelListResponse",
        "etag": "test_etag",
        "pageInfo": {"totalResults": len(items), "resultsPerPage": 5},
        "items": list(items),
    }


def api_error(status, reason, message="test"):
    return FakeResponse(
        {"error": {"code": status, "message": message, "errors": [{"reason": reason, "message": message}]}},
        status,
    )


class FakeResponse:
    def __init__(self, body=None, status_code=200):
        self._body = body
        self.status_code = status_code
        self.content = b"" if body is None else json.dumps(body).encode()

    def json(self):
        if self._body is None:
            raise ValueError("no JSON")
        return self._body


class FakeYouTube:
    def __init__(self):
        self.body = channel_list(channel_item())
        self.queued = deque()
        self.calls = []

    def respond(self, *responses):
        """Answer the next calls with *responses* (FakeResponse or exception) in order."""
        self.queued.extend(responses)

    def __call__(self, url, params=None, headers=None, timeout=None):
        assert url == CHANNELS_URL, url
        self.calls.append({"params": dict(params or {}), "headers": headers, "timeout": timeout})
        if self.queued:
            response = self.queued.popleft()
            if isinstance(response, Exception):
                raise response
            return response
        return FakeResponse(self.body)


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


@pytest.fixture
def manifest_data():
    with open(MANIFEST_PATH) as f:
        return json.load(f)


@pytest.fixture(autouse=True)
def clock():
    c = Clock()
    with patch("plugins.youtube_stats._monotonic", side_effect=c):
        yield c


@pytest.fixture
def youtube_api():
    fake = FakeYouTube()
    with patch("plugins.youtube_stats.requests.get", side_effect=fake):
        yield fake


@pytest.fixture
def make_plugin(manifest_data):
    def factory(**config):
        p = YouTubeStatsPlugin(manifest_data)
        p.config = {"enabled": True, "api_key": TEST_KEY, "channel": "@mapleroad", **config}
        return p

    return factory


@pytest.fixture
def plugin(make_plugin):
    return make_plugin()
