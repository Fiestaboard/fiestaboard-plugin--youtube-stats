"""YouTube Stats plugin for FiestaBoard.

Shows a YouTube channel's subscriber count, total views and number of public
videos. There is no sign-in: the user pastes their own YouTube Data API v3 key
(from their own Google Cloud project) and a channel handle or channel ID. Any
public channel works.

Endpoint (YouTube Data API v3, checked against developers.google.com):

* ``GET https://www.googleapis.com/youtube/v3/channels``
  ``part=snippet,statistics`` and ``forHandle=@name`` or ``id=UC...``.
  One quota unit per call whatever the parts; keys get 10,000 units a day.

``statistics.subscriberCount`` is already rounded down by YouTube to three
significant figures, and is left out when ``hiddenSubscriberCount`` is true.
The counts arrive as strings.
"""

import logging
import re
import threading
import time
import unicodedata
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

import requests
from src.board_chars import BoardChars
from src.plugins.base import PluginBase, PluginResult
from src.text_to_board import take_tiles

logger = logging.getLogger(__name__)

CHANNELS_URL = "https://www.googleapis.com/youtube/v3/channels"
USER_AGENT = "FiestaBoard YouTube Stats Plugin (https://github.com/Fiestaboard/fiestaboard-plugin--youtube-stats)"
REQUEST_TIMEOUT_SECONDS = 10

# Each board shape asks for its own data; one answer from YouTube is shared
# between them for this long so a Flagship and a Note cost one quota unit.
SNAPSHOT_TTL_SECONDS = 60
# After a failure YouTube is left alone for a while instead of being asked
# again on every render (and spending quota on a request that will fail).
TRANSIENT_COOLDOWN_SECONDS = 60
AUTH_COOLDOWN_SECONDS = 600
QUOTA_COOLDOWN_SECONDS = 3600
# During an outage or after the daily quota runs out, keep showing the last
# good numbers while they are this fresh. Subscriber counts move slowly.
STALE_DATA_LIMIT_SECONDS = 6 * 3600

DEFAULT_COLS = 22
DEFAULT_ROWS = 6
TILE_LOGO = "{63}"  # red
HIDDEN = "HIDDEN"

CHANNEL_ID_RE = re.compile(r"^UC[A-Za-z0-9_-]{22}$")
# YouTube handles are 3-30 letters, digits, dots, dashes and underscores.
HANDLE_RE = re.compile(r"^@?([A-Za-z0-9._-]{3,30})$")

NO_KEY_ERROR = "Add your YouTube Data API key in this plugin's settings."
BAD_CHANNEL_ERROR = (
    "Set the channel to its handle, such as @GoogleDevelopers, or its channel ID, which starts with UC."
)
BAD_KEY_ERROR = (
    "YouTube rejected the API key. Check it in this plugin's settings, or create a new one in Google Cloud Console."
)
QUOTA_ERROR = "This API key has used its daily YouTube quota. It resets at midnight Pacific time."
NOT_ENABLED_ERROR = (
    "Your Google Cloud project does not allow the YouTube Data API. In Google Cloud Console, "
    "enable YouTube Data API v3 for the project the key belongs to."
)
BLOCKED_ERROR = (
    "YouTube refused this request (403). Check the key's restrictions in Google Cloud Console: "
    "it must allow the YouTube Data API v3 and calls from your FiestaBoard."
)
UNREACHABLE_ERROR = "Couldn't reach YouTube. Will try again shortly."

_KEY_REASONS = {"keyInvalid", "keyExpired", "API_KEY_INVALID", "API_KEY_EXPIRED"}
_QUOTA_REASONS = {"quotaExceeded", "dailyLimitExceeded", "rateLimitExceeded", "RATE_LIMIT_EXCEEDED"}
_NOT_ENABLED_REASONS = {"accessNotConfigured", "SERVICE_DISABLED"}
_BLOCKED_REASONS = {
    "forbidden",
    "ipRefererBlocked",
    "API_KEY_HTTP_REFERRER_BLOCKED",
    "API_KEY_IP_ADDRESS_BLOCKED",
    "API_KEY_SERVICE_BLOCKED",
    "API_KEY_ANDROID_APP_BLOCKED",
    "API_KEY_IOS_APP_BLOCKED",
}

_SUFFIXES = ((10**12, "T"), (10**9, "B"), (10**6, "M"), (10**3, "K"))


class YouTubeError(Exception):
    """A YouTube request failed; the message is shown to the user."""

    def __init__(self, message: str, *, transient: bool, cooldown: float):
        super().__init__(message)
        self.transient = transient
        self.cooldown = cooldown


# ----------------------------------------------------------------------
# Pure helpers
# ----------------------------------------------------------------------


def _monotonic() -> float:
    return time.monotonic()


def _to_int(value: Any) -> Optional[int]:
    try:
        return max(int(value), 0)
    except (TypeError, ValueError):
        return None


def compact_number(value: Any) -> str:
    """``12400`` -> ``12.4K``; at most three significant figures, rounded down.

    Rounding down matches YouTube's own subscriber rounding, so the board never
    shows more than the channel has (999,999 is ``999K``, not ``1000K``).
    """
    n = _to_int(value)
    if n is None:
        return ""
    for size, suffix in _SUFFIXES:
        if n >= size:
            scaled = n / size
            digits = len(str(int(scaled)))
            decimals = max(3 - digits, 0)
            factor = 10**decimals
            truncated = int(n * factor // size) / factor
            text = f"{truncated:.{decimals}f}"
            if decimals:
                text = text.rstrip("0").rstrip(".")
            return f"{text}{suffix}"
    return str(n)


def with_commas(value: Any) -> str:
    n = _to_int(value)
    return "" if n is None else f"{n:,}"


def parse_channel(text: Any) -> Optional[Tuple[str, str]]:
    """Turn what the user typed into a ``channels.list`` filter.

    Returns ``("id", "UC...")`` or ``("forHandle", "@name")``, or ``None``
    when it is neither. Channel and handle URLs are accepted too.
    """
    value = str(text or "").strip()
    if not value:
        return None
    if "youtube.com/" in value:
        if "://" not in value:
            value = "https://" + value
        parts = [p for p in urlparse(value).path.split("/") if p]
        if len(parts) >= 2 and parts[0] == "channel":
            value = parts[1]
        elif parts and parts[0].startswith("@"):
            value = parts[0]
        else:
            return None
    if CHANNEL_ID_RE.match(value):
        return ("id", value)
    match = HANDLE_RE.match(value)
    if match:
        return ("forHandle", "@" + match.group(1))
    return None


def board_safe(text: Any) -> str:
    """Uppercase *text* and keep only characters the board can show."""
    folded = unicodedata.normalize("NFKD", str(text or ""))
    kept = []
    for ch in folded.upper():
        if ch.isspace():
            kept.append(" ")
        elif BoardChars.get_char_code(ch) is not None:
            kept.append(ch)
    return re.sub(r" {2,}", " ", "".join(kept)).strip()


def fit(text: str, width: int) -> str:
    """Truncate *text* to *width* tiles (colour markers count as one)."""
    return take_tiles(text, width)[0]


def fit_words(text: str, width: int) -> str:
    """Like :func:`fit`, but cut at a word boundary when one is available."""
    head, tail = take_tiles(text, width)
    if tail and not tail.startswith(" ") and " " in head.strip():
        head = head.rsplit(" ", 1)[0]
    return head.rstrip()


def _label_value(label: str, value: str, cols: int) -> str:
    gap = max(cols - len(label) - len(value), 1)
    return fit(f"{label}{' ' * gap}{value}", cols)


def _counted(short: str, count: Optional[int], noun: str) -> str:
    return f"{short} {noun if count == 1 else noun + 'S'}"


def _scrub(message: str, key: str) -> str:
    return message.replace(key, "***") if key else message


# ----------------------------------------------------------------------
# Plugin
# ----------------------------------------------------------------------


class YouTubeStatsPlugin(PluginBase):
    """Subscribers, views and videos for one YouTube channel."""

    def __init__(self, manifest: Dict[str, Any]):
        super().__init__(manifest)
        self._lock = threading.Lock()
        self._reset_state()

    def _reset_state(self) -> None:
        self._snapshot: Optional[Dict[str, Any]] = None
        self._snapshot_at = 0.0
        self._last_good: Optional[Dict[str, Any]] = None
        self._last_good_at = 0.0
        self._cooldown_until = 0.0
        self._cooldown_error: Optional[YouTubeError] = None

    @property
    def plugin_id(self) -> str:
        return "youtube_stats"

    # ------------------------------------------------------------------
    # Config
    # ------------------------------------------------------------------

    def validate_config(self, config: Dict[str, Any]) -> List[str]:
        errors: List[str] = []
        if not str(config.get("api_key") or "").strip():
            errors.append("A YouTube Data API key is required.")
        if not str(config.get("channel") or "").strip():
            errors.append("A channel handle or channel ID is required.")
        elif parse_channel(config.get("channel")) is None:
            errors.append(BAD_CHANNEL_ERROR.replace("Set the channel to", "The channel must be"))
        validate_refresh = getattr(self, "_validate_refresh_seconds", None)
        if validate_refresh is not None:
            errors.extend(validate_refresh(config))
        return errors

    def on_config_change(self, old_config: Dict[str, Any], new_config: Dict[str, Any]) -> None:
        with self._lock:
            self._reset_state()

    # ------------------------------------------------------------------
    # YouTube
    # ------------------------------------------------------------------

    @staticmethod
    def _error_reasons(body: Any) -> Tuple[set, str]:
        error = body.get("error") if isinstance(body, dict) else None
        if not isinstance(error, dict):
            return set(), ""
        reasons = set()
        for entry in (error.get("errors") or []) + (error.get("details") or []):
            if isinstance(entry, dict) and entry.get("reason"):
                reasons.add(str(entry["reason"]))
        return reasons, str(error.get("message") or "")

    def _request(self, key: str, filter_name: str, filter_value: str) -> Dict[str, Any]:
        params = {"part": "snippet,statistics", filter_name: filter_value, "key": key}
        try:
            response = requests.get(
                CHANNELS_URL,
                params=params,
                headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
                timeout=REQUEST_TIMEOUT_SECONDS,
            )
        except requests.exceptions.RequestException as exc:
            logger.info("YouTube request failed: %s", type(exc).__name__)
            raise YouTubeError(UNREACHABLE_ERROR, transient=True, cooldown=TRANSIENT_COOLDOWN_SECONDS)

        try:
            body = response.json()
        except ValueError:
            body = None

        status = response.status_code
        if status == 200:
            if not isinstance(body, dict):
                raise YouTubeError(
                    "YouTube sent an unexpected answer. Will try again shortly.",
                    transient=True,
                    cooldown=TRANSIENT_COOLDOWN_SECONDS,
                )
            return body

        reasons, message = self._error_reasons(body)
        if reasons & _KEY_REASONS or "API key not valid" in message or "API key expired" in message:
            raise YouTubeError(BAD_KEY_ERROR, transient=False, cooldown=AUTH_COOLDOWN_SECONDS)
        if reasons & _QUOTA_REASONS or status == 429:
            raise YouTubeError(QUOTA_ERROR, transient=True, cooldown=QUOTA_COOLDOWN_SECONDS)
        if reasons & _NOT_ENABLED_REASONS:
            raise YouTubeError(NOT_ENABLED_ERROR, transient=False, cooldown=AUTH_COOLDOWN_SECONDS)
        if reasons & _BLOCKED_REASONS:
            raise YouTubeError(BLOCKED_ERROR, transient=False, cooldown=AUTH_COOLDOWN_SECONDS)
        if status == 404 or "channelNotFound" in reasons:
            raise YouTubeError(self._not_found(filter_value), transient=False, cooldown=AUTH_COOLDOWN_SECONDS)
        if status >= 500 or body is None:
            raise YouTubeError(
                f"YouTube is having trouble ({status}). Will try again shortly.",
                transient=True,
                cooldown=TRANSIENT_COOLDOWN_SECONDS,
            )
        detail = f": {_scrub(message, key)}" if message else ""
        raise YouTubeError(
            f"YouTube returned an error ({status}){detail}", transient=False, cooldown=AUTH_COOLDOWN_SECONDS
        )

    @staticmethod
    def _not_found(channel: str) -> str:
        return f"YouTube found no channel {channel}. Check the handle or channel ID in this plugin's settings."

    def _load_snapshot(self, key: str, channel: Tuple[str, str]) -> Dict[str, Any]:
        now = _monotonic()
        if self._snapshot is not None and now - self._snapshot_at < SNAPSHOT_TTL_SECONDS:
            return self._snapshot
        if self._cooldown_error is not None and now < self._cooldown_until:
            raise self._cooldown_error
        try:
            body = self._request(key, *channel)
            items = body.get("items") or []
            if not items or not isinstance(items[0], dict):
                raise YouTubeError(self._not_found(channel[1]), transient=False, cooldown=AUTH_COOLDOWN_SECONDS)
        except YouTubeError as exc:
            self._cooldown_error = exc
            self._cooldown_until = now + exc.cooldown
            raise
        self._cooldown_error = None
        self._snapshot = items[0]
        self._snapshot_at = now
        return self._snapshot

    # ------------------------------------------------------------------
    # Data
    # ------------------------------------------------------------------

    def _board_size(self) -> Tuple[int, int]:
        board = getattr(self, "board", None)
        if board is None:
            return DEFAULT_COLS, DEFAULT_ROWS
        return board.cols, board.rows

    def fetch_data(self) -> PluginResult:
        try:
            key = str(self.config.get("api_key") or "").strip()
            if not key:
                return PluginResult(available=False, error=NO_KEY_ERROR)
            channel = parse_channel(self.config.get("channel"))
            if channel is None:
                return PluginResult(available=False, error=BAD_CHANNEL_ERROR)

            with self._lock:
                try:
                    item = self._load_snapshot(key, channel)
                except YouTubeError as exc:
                    fresh_enough = _monotonic() - self._last_good_at <= STALE_DATA_LIMIT_SECONDS
                    if exc.transient and self._last_good is not None and fresh_enough:
                        logger.info("YouTube: %s; showing the last numbers", exc)
                        item = self._last_good
                    else:
                        logger.warning("YouTube: %s", exc)
                        return PluginResult(available=False, error=str(exc))
                else:
                    if item is not self._last_good:
                        self._last_good = item
                        self._last_good_at = self._snapshot_at

            cols, rows = self._board_size()
            data = self._build_data(item)
            return PluginResult(available=True, data=data, formatted_lines=self._format_display(data, cols, rows))
        except Exception as exc:  # fetch_data must never raise
            logger.exception("Error fetching YouTube stats")
            return PluginResult(available=False, error=str(exc))

    @staticmethod
    def _build_data(item: Dict[str, Any]) -> Dict[str, Any]:
        snippet = item.get("snippet") or {}
        stats = item.get("statistics") or {}
        hidden = bool(stats.get("hiddenSubscriberCount"))

        subscribers = None if hidden else (_to_int(stats.get("subscriberCount")) or 0)
        views = _to_int(stats.get("viewCount")) or 0
        videos = _to_int(stats.get("videoCount")) or 0

        subs_short = HIDDEN if hidden else compact_number(subscribers)
        views_short = compact_number(views)
        videos_short = compact_number(videos)
        return {
            "channel_name": board_safe(snippet.get("title")),
            "handle": board_safe(snippet.get("customUrl")),
            "logo_tile": TILE_LOGO,
            "subscribers": subs_short,
            "subscribers_exact": HIDDEN if hidden else with_commas(subscribers),
            "subscribers_hidden": "YES" if hidden else "NO",
            "subscriber_count": subscribers,
            "views": views_short,
            "views_exact": with_commas(views),
            "view_count": views,
            "videos": videos_short,
            "videos_exact": with_commas(videos),
            "video_count": videos,
            "subscribers_line": "SUBSCRIBERS HIDDEN" if hidden else _counted(subs_short, subscribers, "SUBSCRIBER"),
            "views_line": _counted(views_short, views, "VIEW"),
            "videos_line": _counted(videos_short, videos, "VIDEO"),
        }

    # ------------------------------------------------------------------
    # Display
    # ------------------------------------------------------------------

    @staticmethod
    def _format_display(data: Dict[str, Any], cols: int, rows: int) -> List[str]:
        """Fallback layout for a board *cols* x *rows* tiles when there is no template."""
        title = fit_words(f"{data['logo_tile']} {data['channel_name']}".rstrip(), cols)
        if rows < 6:
            subs = "SUBS HIDDEN" if data["subscribers_hidden"] == "YES" else f"{data['subscribers']} SUBS"
            lines = [title, subs, data["views_line"], data["videos_line"]]
        else:
            lines = [
                title,
                "",
                _label_value("SUBSCRIBERS", data["subscribers"], cols),
                _label_value("VIEWS", data["views"], cols),
                _label_value("VIDEOS", data["videos"], cols),
                "",
            ]
        return [fit(line, cols) for line in lines[:rows]]

    def get_formatted_display(self) -> Optional[List[str]]:
        result = self.get_data(self.board)
        if not result.available or not result.data:
            return None
        cols, rows = self._board_size()
        return self._format_display(result.data, cols, rows)


# Export hook: the loader looks for a module-level `Plugin`.
Plugin = YouTubeStatsPlugin
