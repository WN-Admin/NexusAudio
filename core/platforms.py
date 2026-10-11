"""Multi-platform tracklist resolvers for the NexusAudio Downloader.

Each supported streaming service is wrapped in a :class:`PlatformResolver`
subclass.  A resolver's only job is to turn a service URL into a normalized
list of ``{name, artist, album}`` dicts; the actual audio still comes from the
yt-dlp download pipeline in :mod:`core.downloader`.

Resolvers that can also be used as a *download* source (YouTube, SoundCloud)
attach a ``source_url`` to each track, which the downloader prefers over a
YouTube search when fetching audio.

Public API::

    PLATFORMS              # ordered registry, keyed by slug
    PLATFORM_LABELS        # slug -> human readable label
    resolve(url, ...)      # auto-detect and resolve
    detect(url)            # -> resolver or None
"""

from __future__ import annotations

import logging
import re

import requests

logger = logging.getLogger(__name__)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

HTTP_TIMEOUT = 20

# Noise that streaming-service titles carry but that is not part of the
# actual song name.  Ordered longest-first so "(Official Music Video)" is
# stripped before the shorter "(Official Video)" pattern can match.
_NOISE_PATTERNS = [
    r"\(remaster(?:ed)?\s+\d{4}\)",
    r"\(?(?:\d{4}\s+)?remaster(?:ed)?\b\)?",
    r"\(?\blive\b(?:\s+(?:at|from|in))?[^)]*\)?",
    r"\(?\bofficial\s+music\s+video\b\)?",
    r"\(?\bofficial\s+video\b\)?",
    r"\(?\bofficial\s+audio\b\)?",
    r"\(?\blyric\s+video\b\)?",
    r"\(?\blyrics?\b\)?",
    r"\(?\b(?:music\s+video|visualizer|explicit|clean)\b\)?",
    r"\(?\b(?:hd|hq|4k|1080p?|720p?|audio\s+only)\b\)?",
    # Non-English promo noise seen on regional charts.
    r"\(?\b(?:video\s+oficial|videoclip|video\s+clip|performance\s+video)\b\)?",
    r"\(?\b(?:con|en)\s+letra\b\)?",
    r"(?:\bvideo\s+)?\b(?:lyric|lyrics|letra|letras)\b\)?",
    r"\[[^\]]*\]",
    r"【[^】]*】",
]
_NOISE_RE = re.compile("|".join(_NOISE_PATTERNS), re.IGNORECASE)
_WS_RE = re.compile(r"\s+")
_ARTIST_SPLIT_RE = re.compile(r"^(.{1,80}?)\s+[-–—]\s+(.+)$")
# Leftover delimiter from a partially-stripped parenthetical, e.g. "Hey Jude 2015)".
_DANGLING_RE = re.compile(r"[\(\)\[\]]")


def clean_title(text):
    """Strip promo noise from a service-provided title."""
    if not text:
        return ""
    cleaned = _NOISE_RE.sub(" ", str(text))
    cleaned = _DANGLING_RE.sub(" ", cleaned)
    cleaned = _WS_RE.sub(" ", cleaned).strip(" -–—|·,")
    return cleaned.strip()


def split_artist_title(text):
    """Split a ``"Artist - Title"`` string.

    Returns ``(artist, title)``; ``artist`` is ``''`` when the string has no
    separator or the left-hand side is implausibly long.
    """
    cleaned = clean_title(text)
    match = _ARTIST_SPLIT_RE.match(cleaned)
    if not match:
        return "", cleaned
    return match.group(1).strip(), match.group(2).strip()


def _track(name, artist, album="", source_url="", duration=None, platform="", extra=None):
    """Build a normalized track dict.

    Keys match what :meth:`core.downloader.Downloader.download_track`
    consumes, plus optional metadata for tagging.
    """
    entry = {
        "name": clean_title(name),
        "artist": clean_title(artist),
        "album": clean_title(album) if album else "",
    }
    if source_url:
        entry["source_url"] = source_url
    if duration:
        entry["duration"] = duration
    if platform:
        entry["platform"] = platform
    if extra:
        entry.update(extra)
    if not entry["name"]:
        return None
    return entry


class PlatformResolver:
    """Base class for a streaming-service tracklist resolver."""

    slug = ""
    label = ""
    patterns: list = []
    supports_search = False
    #: True when :mod:`yt_dlp` can download audio straight from this service.
    downloadable = False

    def matches(self, url):
        return any(re.search(pattern, url, re.IGNORECASE) for pattern in self.patterns)

    def resolve(self, url, cancel=None):  # pragma: no cover - interface
        raise NotImplementedError

    def search(self, term, cancel=None):  # pragma: no cover - interface
        raise NotImplementedError(
            f"{self.label or self.slug} does not support free-text search"
        )

    # -- helpers -----------------------------------------------------

    @staticmethod
    def _get_json(url, params=None):
        response = requests.get(
            url, params=params, timeout=HTTP_TIMEOUT, headers={"User-Agent": USER_AGENT}
        )
        response.raise_for_status()
        return response.json()


# ----------------------------------------------------------------------
# Deezer — public JSON API, no credentials required
# ----------------------------------------------------------------------

_DEEZEER_RE = re.compile(
    r"deezer\.com/(?:[a-z]{2}/)?(?:playlist|album|track)/(\d+)", re.IGNORECASE
)


class DeezerResolver(PlatformResolver):
    slug = "deezer"
    label = "Deezer"
    supports_search = True
    patterns = [r"deezer\.com"]

    def _track_from_api(self, data, order=0):
        artist = data.get("artist") or {}
        album = data.get("album") or {}
        contributor = data.get("contributors") or []
        if not artist.get("name") and contributor:
            artist = contributor[0]
        return _track(
            name=data.get("title", ""),
            artist=artist.get("name", ""),
            album=album.get("title", ""),
            duration=data.get("duration"),
            platform=self.slug,
            extra={"isrc": data.get("isrc", "")} if data.get("isrc") else None,
        )

    def resolve(self, url, cancel=None):
        match = _DEEZEER_RE.search(url or "")
        if not match:
            return self.search(url, cancel=cancel)

        kind = re.search(r"(playlist|album|track)/(\d+)", match.group(0), re.IGNORECASE)
        kind, ident = kind.group(1), match.group(1)
        try:
            if kind == "playlist":
                payload = self._get_json(f"https://api.deezer.com/playlist/{ident}")
                rows = payload.get("tracks", {}).get("data", [])
            elif kind == "album":
                payload = self._get_json(f"https://api.deezer.com/album/{ident}")
                rows = payload.get("tracks", {}).get("data", [])
            else:
                rows = [self._get_json(f"https://api.deezer.com/track/{ident}")]
        except requests.RequestException as exc:
            raise RuntimeError(f"Deezer lookup failed: {exc}") from exc

        tracks = []
        for index, row in enumerate(rows or []):
            if cancel is not None and cancel():
                break
            if not row:
                continue
            entry = self._track_from_api(row, index)
            if entry:
                tracks.append(entry)
        return tracks

    def search(self, term, cancel=None):
        try:
            payload = self._get_json(
                "https://api.deezer.com/search/track", params={"q": term, "limit": 50}
            )
        except requests.RequestException as exc:
            raise RuntimeError(f"Deezer search failed: {exc}") from exc
        tracks = []
        for row in payload.get("data", []):
            if cancel is not None and cancel():
                break
            entry = self._track_from_api(row)
            if entry:
                tracks.append(entry)
        return tracks


# ----------------------------------------------------------------------
# Apple Music — via the public iTunes Lookup/Search endpoints.
# Covers tracks and albums; Apple Music *playlists* have no public
# unauthenticated API, so those URLs report a clear error instead.
# ----------------------------------------------------------------------

_APPLE_ID_RE = re.compile(r"/(?:album|playlist|song|music-video)/[^/]+/(\d+)", re.IGNORECASE)
_APPLE_SONG_ID_RE = re.compile(r"[?&]i=(\d+)")


class AppleMusicResolver(PlatformResolver):
    slug = "apple"
    label = "Apple Music"
    supports_search = True
    patterns = [r"music\.apple\.com", r"itunes\.apple\.com"]

    def _entry_from_result(self, result, playlist_title=""):
        if result.get("wrapperType") == "collection":
            # The album record itself carries no track number; real tracks
            # arrive as separate "track" rows via entity=song.
            return None
        return _track(
            name=result.get("trackName", ""),
            artist=result.get("artistName", ""),
            album=result.get("collectionName") or playlist_title,
            duration=result.get("trackTimeMillis", 0) // 1000 or None,
            platform=self.slug,
        )

    def resolve(self, url, cancel=None):
        if re.search(r"/playlist/", url or "", re.IGNORECASE):
            raise RuntimeError(
                "Apple Music playlists require a developer token and are not "
                "supported without API credentials. Paste an album or song link instead."
            )

        song_id = _APPLE_SONG_ID_RE.search(url or "")
        if song_id:
            ident, entity = song_id.group(1), None
        else:
            match = _APPLE_ID_RE.search(url or "")
            if not match:
                return self.search(url, cancel=cancel)
            ident = match.group(1)
            kind_match = re.search(r"/(album|song|music-video)/", url, re.IGNORECASE)
            entity = "song" if kind_match and kind_match.group(1) == "album" else None

        params = {"id": ident, "entity": entity} if entity else {"id": ident}
        try:
            payload = self._get_json("https://itunes.apple.com/lookup", params=params)
        except requests.RequestException as exc:
            raise RuntimeError(f"Apple Music lookup failed: {exc}") from exc

        if payload.get("resultCount", 0) == 0:
            raise RuntimeError(f"Apple Music found no match for {ident}")

        results = payload.get("results", [])
        album_title = next(
            (r.get("collectionName", "") for r in results if r.get("wrapperType") == "collection"),
            "",
        )
        tracks = []
        for result in results:
            if cancel is not None and cancel():
                break
            entry = self._entry_from_result(result, album_title)
            if entry:
                tracks.append(entry)
        return tracks

    def search(self, term, cancel=None):
        try:
            payload = self._get_json(
                "https://itunes.apple.com/search",
                params={"term": term, "entity": "song", "limit": 50},
            )
        except requests.RequestException as exc:
            raise RuntimeError(f"Apple Music search failed: {exc}") from exc
        tracks = []
        for result in payload.get("results", []):
            if cancel is not None and cancel():
                break
            entry = self._entry_from_result(result)
            if entry:
                tracks.append(entry)
        return tracks


# ----------------------------------------------------------------------
# YouTube / YouTube Music — metadata via yt-dlp, audio downloadable
# ----------------------------------------------------------------------

_YT_ID_RE = re.compile(
    r"(?:youtube\.com/(?:watch\?|playlist\?|embed/|v/|shorts/|live/)|youtu\.be/)",
    re.IGNORECASE,
)


class YouTubeResolver(PlatformResolver):
    slug = "youtube"
    label = "YouTube / YouTube Music"
    downloadable = True
    supports_search = True
    patterns = [_YT_ID_RE.pattern]

    def _entry_from_video(self, video, playlist_title=""):
        raw_title = video.get("title") or ""
        url = video.get("url") or video.get("webpage_url") or ""
        if not url and video.get("id"):
            url = f"https://www.youtube.com/watch?v={video['id']}"

        uploader = clean_title(video.get("uploader") or video.get("channel") or "")
        leading_artist, remainder = split_artist_title(raw_title)
        if leading_artist:
            artist, name = leading_artist, remainder
            if not artist and uploader:
                artist = uploader
        else:
            name = clean_title(raw_title)
            artist = uploader

        if artist and name.lower().startswith(artist.lower() + " - "):
            name = name[len(artist) + 3:].strip()
        if not artist:
            artist = uploader

        return _track(
            name=name,
            artist=artist,
            album=playlist_title,
            source_url=url,
            duration=video.get("duration"),
            platform=self.slug,
        )

    def resolve(self, url, cancel=None):
        from yt_dlp import YoutubeDL

        options = {
            "quiet": True,
            "no_warnings": True,
            "extract_flat": "in_playlist",
            "skip_download": True,
        }
        with YoutubeDL(options) as ydl:
            info = ydl.extract_info(url, download=False)

        if not info:
            raise RuntimeError(f"Could not read YouTube URL: {url}")

        if info.get("_type") == "playlist" or "entries" in info:
            playlist_title = clean_title(info.get("title", ""))
            videos = info.get("entries") or []
        else:
            playlist_title = ""
            videos = [info]

        tracks = []
        for video in videos:
            if cancel is not None and cancel():
                break
            if not video:
                continue
            entry = self._entry_from_video(video, playlist_title)
            if entry:
                tracks.append(entry)
        return tracks

    def search(self, term, cancel=None):
        from yt_dlp import YoutubeDL

        options = {"quiet": True, "no_warnings": True, "extract_flat": True, "skip_download": True}
        with YoutubeDL(options) as ydl:
            info = ydl.extract_info(f"ytsearch50:{term}", download=False)
        tracks = []
        for video in (info or {}).get("entries", []):
            if cancel is not None and cancel():
                break
            if not video:
                continue
            entry = self._entry_from_video(video)
            if entry:
                tracks.append(entry)
        return tracks


# ----------------------------------------------------------------------
# SoundCloud — metadata via yt-dlp, audio downloadable.
# Needs a full (non-flat) extraction because flat SoundCloud sets return
# entries with null titles.
# ----------------------------------------------------------------------

_SC_SET_RE = re.compile(r"soundcloud\.com/[^/]+/sets/[^/?#]+", re.IGNORECASE)


class SoundCloudResolver(PlatformResolver):
    slug = "soundcloud"
    label = "SoundCloud"
    downloadable = True
    patterns = [r"soundcloud\.com"]

    def _entry_from_track(self, track, set_title=""):
        uploader = clean_title(track.get("uploader") or track.get("channel") or "")
        raw_title = clean_title(track.get("title") or "")
        if not raw_title:
            return None
        # SoundCloud titles are conventionally "Artist - Title", and the
        # artist may be a collaboration longer than the uploader account
        # name, so the title's own split wins when present.
        title_artist, remainder = split_artist_title(raw_title)
        prefix = f"{uploader} - "
        if title_artist and not raw_title.lower().startswith(prefix.lower()):
            artist, name = title_artist, remainder
        elif raw_title.lower().startswith(prefix.lower()):
            artist, name = uploader, raw_title[len(prefix):].strip()
        else:
            artist, name = uploader, raw_title
        return _track(
            name=name,
            artist=artist,
            album=set_title,
            source_url=track.get("webpage_url", ""),
            duration=track.get("duration"),
            platform=self.slug,
        )

    def resolve(self, url, cancel=None):
        from yt_dlp import YoutubeDL

        is_set = bool(_SC_SET_RE.search(url or ""))
        options = {
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,
            "ignoreerrors": True,
        }
        if not is_set:
            # Single tracks resolve fine flat; full extraction is slow.
            options["extract_flat"] = True

        with YoutubeDL(options) as ydl:
            info = ydl.extract_info(url, download=False)

        if not info:
            raise RuntimeError(f"Could not read SoundCloud URL: {url}")

        if info.get("_type") == "playlist" or "entries" in info:
            set_title = clean_title(info.get("title", ""))
            items = info.get("entries") or []
        else:
            set_title = ""
            items = [info]

        tracks = []
        for item in items:
            if cancel is not None and cancel():
                break
            if not item:
                continue
            entry = self._entry_from_track(item, set_title)
            if entry:
                tracks.append(entry)
        return tracks


# ----------------------------------------------------------------------
# Spotify — API when spotipy + credentials are available, otherwise the
# public page scrape that already backs the downloader's fallback path.
# ----------------------------------------------------------------------

_SPOTIFY_RE = re.compile(r"open\.spotify\.com/\S*", re.IGNORECASE)


class SpotifyResolver(PlatformResolver):
    slug = "spotify"
    label = "Spotify"
    patterns = [_SPOTIFY_RE.pattern]

    def __init__(self, client=None):
        self.client = client

    # -- API path ---------------------------------------------------

    def _entry_from_track(self, track):
        if not track or not track.get("name"):
            return None
        artists = track.get("artists") or []
        album = track.get("album") or {}
        return _track(
            name=track.get("name", ""),
            artist=artists[0].get("name", "") if artists else "",
            album=album.get("name", ""),
            duration=(track.get("duration_ms") or 0) // 1000 or None,
            platform=self.slug,
        )

    def _resolve_via_api(self, url):
        tracks = []
        if re.search(r"/track/", url):
            entry = self._entry_from_track(self.client.track(url))
            if entry:
                tracks.append(entry)
        elif re.search(r"/album/", url):
            results = self.client.album_tracks(url)
            while results:
                for item in results.get("items", []):
                    entry = self._entry_from_track(item)
                    if entry:
                        tracks.append(entry)
                results = self.client.next(results) if results.get("next") else None
        elif re.search(r"/playlist/", url):
            results = self.client.playlist_tracks(url)
            while results:
                for item in results.get("items", []):
                    entry = self._entry_from_track(item.get("track"))
                    if entry:
                        tracks.append(entry)
                results = self.client.next(results) if results.get("next") else None
        else:
            raise RuntimeError("Unsupported Spotify URL — expected /track/, /album/ or /playlist/")
        return tracks

    # -- scrape path ------------------------------------------------

    @staticmethod
    def _scrape(url):
        import json
        import re

        # Convert to embed URL which has a simpler, more reliable structure
        # open.spotify.com/playlist/... -> open.spotify.com/embed/playlist/...
        embed_url = re.sub(r'open\.spotify\.com/(playlist|album|track)/([^/?#]+)', r'open.spotify.com/embed/\1/\2', url)

        response = requests.get(embed_url, timeout=HTTP_TIMEOUT, headers={"User-Agent": USER_AGENT})
        response.raise_for_status()
        html = response.text

        # Extract __NEXT_DATA__ from embed page
        match = re.search(r'<script[^>]*id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.DOTALL)
        if not match:
            logger.debug("Could not find __NEXT_DATA__ in Spotify embed page")
            return []

        try:
            data = json.loads(match.group(1))
        except json.JSONDecodeError as exc:
            logger.warning("Failed to parse Spotify embed JSON: %s", exc)
            return []

        # Navigate to track list: props.pageProps.state.data.entity.trackList
        try:
            entity = data.get("props", {}).get("pageProps", {}).get("state", {}).get("data", {}).get("entity", {})
            track_list = entity.get("trackList", [])
        except Exception as exc:
            logger.warning("Unexpected Spotify embed data structure: %s", exc)
            return []

        if not track_list:
            # Try album/track format (single item)
            if entity.get("type") == "track":
                track_list = [entity]
            elif entity.get("type") == "album":
                # Albums in embed may not have trackList easily accessible
                logger.debug("Album track list not found in embed page")
                return []

        tracks = []
        for item in track_list:
            name = item.get("title", "")
            artist = item.get("subtitle", "")
            duration_ms = item.get("duration", 0)

            # Build album name from entity if available (for playlists)
            album = entity.get("name", "") if entity.get("type") == "playlist" else ""

            entry = _track(
                name=name,
                artist=artist,
                album=album,
                duration=duration_ms // 1000 if duration_ms else None,
                platform="spotify",
            )
            if entry:
                tracks.append(entry)

        return tracks

    def resolve(self, url, cancel=None):
        if not self.matches(url):
            return []
        if self.client is not None:
            try:
                return self._resolve_via_api(url)
            except Exception as exc:
                logger.warning("Spotify API failed (%s) — falling back to page scrape", exc)
        try:
            tracks = self._scrape(url)
        except requests.RequestException as exc:
            raise RuntimeError(f"Spotify lookup failed: {exc}") from exc
        except Exception as exc:
            raise RuntimeError(f"Spotify page could not be read: {exc}") from exc
        if not tracks:
            raise RuntimeError("Could not extract tracks from that Spotify URL")
        return tracks


# ----------------------------------------------------------------------
# Registry
# ----------------------------------------------------------------------

PLATFORMS = [
    SpotifyResolver(),
    AppleMusicResolver(),
    DeezerResolver(),
    YouTubeResolver(),
    SoundCloudResolver(),
]

PLATFORMS_BY_SLUG = {resolver.slug: resolver for resolver in PLATFORMS}

PLATFORM_LABELS = {resolver.slug: resolver.label for resolver in PLATFORMS}


def get_resolver(slug):
    """Return the resolver for ``slug``, or None."""
    return PLATFORMS_BY_SLUG.get(slug)


def detect(url):
    """Return the resolver matching ``url``, or None if unrecognized."""
    if not url:
        return None
    for resolver in PLATFORMS:
        if resolver.matches(url):
            return resolver
    return None


def resolve(url, slug=None, spotify_client=None, cancel=None):
    """Resolve ``url`` into a list of normalized track dicts.

    When ``slug`` is given the named platform is used directly (falling back
    to free-text search for platforms that support it); otherwise the
    platform is auto-detected from the URL.

    Raises ``ValueError`` for an empty or unsupported URL and ``RuntimeError``
    when the upstream lookup itself fails.
    """
    url = (url or "").strip()
    if not url:
        raise ValueError("Please enter a URL")

    if slug:
        resolver = PLATFORMS_BY_SLUG.get(slug)
        if resolver is None:
            raise ValueError(f"Unknown platform: {slug}")
    else:
        resolver = detect(url)
        if resolver is None:
            if url.startswith("http"):
                raise ValueError(
                    "Unsupported link. Supported: Spotify, Apple Music, Deezer, "
                    "YouTube / YouTube Music, SoundCloud."
                )
            raise ValueError(
                "Unrecognized link. Paste a Spotify, Apple Music, Deezer, YouTube "
                "or SoundCloud URL, or pick a platform and enter a search term."
            )

    if isinstance(resolver, SpotifyResolver) and spotify_client is not None:
        resolver = SpotifyResolver(spotify_client)

    if not resolver.matches(url) and not resolver.supports_search:
        raise ValueError(f"That does not look like a {resolver.label} URL")

    return resolver.resolve(url, cancel=cancel)
