"""
NexusAudio Downloader Module
Fully integrated Spotify + YouTube downloader with metadata/lyrics, queue, and cancellation
"""
import os
import re
import sys
import logging
import threading
from collections import deque
from yt_dlp import YoutubeDL

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config
from core.metadata import fetch_lyrics, embed_lyrics
from core import platforms
from core.utils import sanitize_filename

logger = logging.getLogger(__name__)


class DownloadCancelledError(Exception):
    """Raised inside yt-dlp hooks to abort cleanly on user cancel."""


_FORBIDDEN_CHARS_RE = re.compile(r'[/\\<>:"|?*\x00-\x1f]')


def _sanitize_filename(text, max_length=200):
    """Strip only filesystem-illegal characters, preserve international text."""
    return sanitize_filename(text, max_length)


#: Short labels shown in the Platform column of the Downloading tab.
PLATFORM_BADGES = {
    'youtube': 'YT',
    'soundcloud': 'SC',
    'deezer': 'DZ',
    'apple': 'AM',
    'spotify': 'SP',
    'soulseek': 'SL',
}


def platform_badge(track_info):
    """Short platform label for a queued track.

    Metadata-only platforms (Deezer, Apple Music, Spotify) have no audio of
    their own — the bytes come from a YouTube search — so those report ``YT``.
    """
    slug = (track_info or {}).get('platform') or ''
    if not slug:
        return 'YT'
    if (track_info or {}).get('source_url'):
        return PLATFORM_BADGES.get(slug, slug.upper()[:3])
    if slug in ('youtube', 'soundcloud'):
        return PLATFORM_BADGES.get(slug, slug.upper()[:3])
    return 'YT'


class Downloader:
    """Integrated downloader combining Spotify API and YouTube downloading"""

    def __init__(self, progress_callback=None, status_callback=None, queue_progress_callback=None, on_track_event=None):
        self.progress_callback = progress_callback
        self.status_callback = status_callback
        self.queue_progress_callback = queue_progress_callback
        #: Called as ``(event, index, total, name, artist, platform)`` where
        #: event is one of queued/start/done/failed.  Drives the Downloading tab.
        self.on_track_event = on_track_event
        self.sp = None
        self.active_downloads = {}
        self._cancel_event = threading.Event()
        self._queue = deque()
        self._queue_lock = threading.Lock()
        self._queue_thread = None
        self._queue_running = False
        self._current_track_index = 0
        self._total_queue_items = 0
        self._setup_spotify()

    def _setup_spotify(self):
        """Initialize Spotify API client using secured credentials (optional dep)"""
        try:
            import spotipy
            from spotipy.oauth2 import SpotifyClientCredentials
        except ImportError:
            logger.info("spotipy not installed — using web scraping fallback")
            return
        try:
            client_id, client_secret = config.get_spotify_creds()
            if client_id and client_secret:
                self.sp = spotipy.Spotify(auth_manager=SpotifyClientCredentials(
                    client_id=client_id,
                    client_secret=client_secret
                ))
        except Exception as e:
            logger.warning("Spotify setup error: %s", e)
            if self.status_callback:
                self.status_callback(f"Spotify setup error: {e}")

    def cancel(self):
        """Cancel the current download operation"""
        self._cancel_event.set()
        logger.info("Download cancellation requested")

    def cancel_all(self):
        """Cancel current download and clear the queue"""
        self.cancel()
        with self._queue_lock:
            self._queue = deque()
            self._queue_running = False
        logger.info("All downloads cancelled and queue cleared")

    def reset_cancel(self):
        """Reset the cancel flag for a new download"""
        self._cancel_event.clear()

    def get_queue_length(self):
        """Return number of pending items in the queue"""
        with self._queue_lock:
            return len(self._queue)

    def get_queue(self):
        """Return a copy of the current queue"""
        with self._queue_lock:
            return list(self._queue) if self._queue else []

    def search_youtube(self, query, limit=5, clean_audio=False):
        """Search YouTube and return video URLs.

        When ``clean_audio=True``, prioritize official audio, topic channels,
        lyric videos, and visualizers while deprioritizing music videos,
        live performances, covers, remixes, etc.
        """
        if self._cancel_event.is_set():
            return []
        try:
            ydl_opts = {
                'quiet': True,
                'no_warnings': True,
                'extract_flat': True
            }
            with YoutubeDL(ydl_opts) as ydl:
                search_query = query
                search_limit = limit * 3 if clean_audio else limit
                if clean_audio:
                    search_query = f"{query} official audio"
                result = ydl.extract_info(f"ytsearch{search_limit}:{search_query}", download=False)
                if result and 'entries' in result:
                    entries = [e for e in result['entries'] if e]
                    if clean_audio:
                        entries = self._rank_clean_audio(entries, query)
                    urls = []
                    for entry in entries[:limit]:
                        vid = entry.get('id') or entry.get('url')
                        if vid:
                            urls.append(f"https://www.youtube.com/watch?v={vid}")
                    return urls
        except Exception as e:
            logger.warning("YouTube search error: %s", e)
            if self.status_callback:
                self.status_callback(f"YouTube search error: {e}")
        return []

    def _rank_clean_audio(self, entries, original_query):
        """Rank YouTube entries by likelihood of being clean official audio.

        Priority order:
        1. Official artist topic channels (e.g., "Artist - Topic")
        2. Titles containing "official audio", "audio", "lyric video", "visualizer"
        3. Official artist channels (verified)
        4. Deprioritize: music video, live, cover, remix, acoustic, karaoke, instrumental, reaction
        """
        # Noise patterns that indicate non-clean versions
        noise_patterns = [
            r'\bmusic\s+video\b', r'\blive\b', r'\bcover\b', r'\bremix\b',
            r'\bacoustic\b', r'\bkaraoke\b', r'\binstrumental\b', r'\breaction\b',
            r'\b8d\s+audio\b', r'\bncs\b', r'\bass\b', r'\bboosted\b',
            r'\bextended\b', r'\b1\s*hour\b', r'\bloop\b', r'\bmashup\b',
            r'\btiktok\b', r'\bshorts\b', r'\breel\b',
        ]
        noise_re = re.compile('|'.join(noise_patterns), re.IGNORECASE)

        # Clean patterns that indicate good versions
        clean_patterns = [
            r'\bofficial\s+audio\b', r'\blyric\s+video\b', r'\bvisualizer\b',
            r'\baudio\b', r'\bofficial\b',
        ]
        clean_re = re.compile('|'.join(clean_patterns), re.IGNORECASE)

        def score_entry(entry):
            title = (entry.get('title') or '').lower()
            channel = (entry.get('uploader') or entry.get('channel') or '').lower()
            score = 0

            # Heavy penalty for noise
            if noise_re.search(title):
                score -= 50

            # Bonus for clean indicators in title
            if clean_re.search(title):
                score += 30

            # Topic channels are usually clean official audio
            if ' - topic' in channel or channel.endswith('topic'):
                score += 40

            # Official artist channel (often has verified badge, but we can't detect that easily)
            # Check if channel name matches artist from query
            query_artist = original_query.split()[0].lower() if original_query else ''
            if query_artist and query_artist in channel:
                score += 20

            # Shorter titles often cleaner (less "feat. x, y, z - official music video")
            score -= len(title) / 100

            return score

        return sorted(entries, key=score_entry, reverse=True)

    def search_spotify(self, query, limit=10):
        """Search Spotify for tracks"""
        if not self.sp:
            return []
        if self._cancel_event.is_set():
            return []
        try:
            results = self.sp.search(q=query, limit=limit, type='track')
            tracks = []
            for item in results['tracks']['items']:
                tracks.append({
                    'name': item['name'],
                    'artist': item['artists'][0]['name'],
                    'album': item['album']['name'],
                    'duration_ms': item['duration_ms'],
                    'spotify_url': item['external_urls']['spotify']
                })
            return tracks
        except Exception as e:
            logger.warning("Spotify search error: %s", e)
            if self.status_callback:
                self.status_callback(f"Spotify search error: {e}")
            return []

    def get_tracks_from_url(self, url, platform=None):
        """Extract a track list from any supported service URL.

        Delegates to :mod:`core.platforms`, which supports Spotify, Apple
        Music, Deezer, YouTube / YouTube Music and SoundCloud.  ``platform``
        may be a platform slug to force a particular resolver; when omitted
        the platform is auto-detected from the URL.
        """
        try:
            tracks = platforms.resolve(
                url,
                slug=platform,
                spotify_client=self.sp,
                cancel=lambda: self._cancel_event.is_set(),
            )
        except (ValueError, RuntimeError) as e:
            logger.warning("Track fetch failed: %s", e)
            if self.status_callback:
                self.status_callback(str(e))
            return []
        except Exception as e:
            logger.warning("Unexpected track fetch error: %s", e)
            if self.status_callback:
                self.status_callback(f"Could not fetch tracks: {e}")
            return []

        if not tracks and not self._cancel_event.is_set():
            message = "No tracks found at that link"
            logger.warning(message)
            if self.status_callback:
                self.status_callback(message)
        elif tracks:
            logger.info("Resolved %d track(s) from %s", len(tracks), platform or "auto-detected platform")
        return tracks

    def download_track(self, track_info, output_dir, audio_format='mp3', quality='192', cookies=None, embed_metadata=True, embed_lyrics=False):
        """Download a single track from YouTube based on Spotify metadata.
        Tries multiple YouTube results on failure."""
        try:
            return self._download_track(
                track_info, output_dir, audio_format, quality, cookies,
                embed_metadata, embed_lyrics)
        except Exception as e:
            # Callers (notably the queue runner) rely on a (bool, str) result,
            # so normalise any escaping exception into a reported failure.
            logger.exception("download_track failed for %s", track_info)
            return False, f"{type(e).__name__}: {e}"

    def _download_track(self, track_info, output_dir, audio_format='mp3', quality='192', cookies=None, embed_metadata=True, embed_lyrics=False):
        name = track_info.get('name') or ''
        artist = track_info.get('artist') or ''
        query = f"{name} {artist}".strip()
        if not query:
            return False, "Track is missing a title"

        if self._cancel_event.is_set():
            return False, "Cancelled"

        # Platforms that expose their own audio (YouTube, SoundCloud) carry a
        # source_url; try it first since it is an exact match, then fall back
        # to a YouTube search for metadata-only platforms.
        source_url = track_info.get('source_url')
        urls = [source_url] if source_url else []
        # Use clean audio search for metadata-only platforms (Spotify, Apple Music, Deezer)
        # which don't have a source_url - this prioritizes official audio, topic channels,
        # lyric videos, visualizers and filters out music videos, live versions, covers, etc.
        use_clean_search = not source_url
        urls += [u for u in self.search_youtube(query, limit=5, clean_audio=use_clean_search) if u != source_url]

        if not urls:
            return False, f"Not found on YouTube: {query}"

        if self._cancel_event.is_set():
            return False, "Cancelled"

        safe_artist = _sanitize_filename(artist)
        safe_title = _sanitize_filename(name)
        filename = f"{safe_artist} - {safe_title}"
        outtmpl = os.path.join(output_dir, f'{filename}.%(ext)s')

        ydl_opts = {
            'format': 'bestaudio/best',
            'outtmpl': outtmpl,
            'quiet': True,
            'no_warnings': True,
        }

        ydl_opts['postprocessors'] = [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': audio_format,
        }]
        if audio_format == 'mp3':
            ydl_opts['postprocessors'][0]['preferredquality'] = quality

        if cookies:
            if os.path.isfile(cookies):
                ydl_opts['cookiefile'] = cookies
            else:
                ydl_opts['cookiesfrombrowser'] = (cookies,)

        failed_urls = []
        for idx, video_url in enumerate(urls):
            if self._cancel_event.is_set():
                return False, "Cancelled"

            if idx > 0 and self.status_callback:
                self.status_callback(f"Trying result {idx + 1}/{len(urls)} for '{query}'")

            try:
                def progress_hook(d):
                    if self._cancel_event.is_set():
                        raise DownloadCancelledError()
                    if self.progress_callback:
                        if d['status'] == 'finished':
                            self.progress_callback(100)
                        elif d['status'] == 'downloading':
                            total = d.get('total_bytes') or d.get('total_bytes_estimate')
                            downloaded = d.get('downloaded_bytes')
                            if total and downloaded is not None:
                                self.progress_callback(min(99.0, downloaded / total * 100))
                            else:
                                p = d.get('_percent_str', '0%').replace('%', '')
                                try:
                                    self.progress_callback(float(p))
                                except (ValueError, TypeError):
                                    pass

                ydl_opts['progress_hooks'] = [progress_hook]

                with YoutubeDL(ydl_opts) as ydl:
                    ydl.download([video_url])

                if self._cancel_event.is_set():
                    return False, "Cancelled"

                if embed_metadata:
                    self._tag_file(output_dir, filename, track_info, audio_format)

                if self._cancel_event.is_set():
                    return False, "Cancelled"

                if embed_lyrics:
                    self._add_lyrics(output_dir, filename, track_info, audio_format)

                return True, "Downloaded successfully"

            except DownloadCancelledError:
                return False, "Cancelled"
            except Exception as e:
                err_str = str(e)
                failed_urls.append(video_url)
                logger.warning("Download failed for %s: %s", video_url, err_str)
                if self._cancel_event.is_set():
                    return False, "Cancelled"
                continue

        return False, f"All {len(urls)} YouTube results failed for '{query}': {failed_urls}"

    def _add_lyrics(self, output_dir, filename, track_info, audio_format='mp3'):
        """Fetch and embed lyrics"""
        lyrics = fetch_lyrics(track_info['name'], track_info['artist'])
        if lyrics:
            ext_map = {'mp3': '.mp3', 'flac': '.flac', 'm4a': '.m4a', 'ogg': '.ogg'}
            ext = ext_map.get(audio_format, '.mp3')
            filepath = os.path.join(output_dir, f"{filename}{ext}")
            if os.path.exists(filepath):
                embed_lyrics(filepath, lyrics)
                return True
        return False

    def _tag_file(self, output_dir, filename, track_info, audio_format):
        """Apply tags to downloaded file (supports MP3, FLAC, M4A, OGG)"""
        ext_map = {'mp3': '.mp3', 'flac': '.flac', 'm4a': '.m4a', 'ogg': '.ogg'}
        ext = ext_map.get(audio_format)
        if not ext:
            return
        filepath = os.path.join(output_dir, f"{filename}{ext}")

        if not os.path.exists(filepath):
            return

        try:
            if audio_format == 'mp3':
                from mutagen.easyid3 import EasyID3
                from mutagen.id3 import ID3NoHeaderError
                try:
                    audio = EasyID3(filepath)
                except ID3NoHeaderError:
                    audio = EasyID3()
                    audio.save(filepath)
                    audio = EasyID3(filepath)
                audio['title'] = track_info['name']
                audio['artist'] = track_info['artist']
                audio['album'] = track_info.get('album', '')
                audio.save()
            elif audio_format in ('flac', 'ogg'):
                from mutagen.flac import FLAC
                from mutagen.oggvorbis import OggVorbis
                if audio_format == 'flac':
                    audio = FLAC(filepath)
                else:
                    audio = OggVorbis(filepath)
                audio['title'] = track_info['name']
                audio['artist'] = track_info['artist']
                audio['album'] = track_info.get('album', '')
                audio.save()
            elif audio_format == 'm4a':
                from mutagen.mp4 import MP4
                audio = MP4(filepath)
                audio['\xa9nam'] = [track_info['name']]
                audio['\xa9ART'] = [track_info['artist']]
                audio['\xa9alb'] = [track_info.get('album', '')]
                audio.save()
        except Exception as e:
            logger.warning("Tagging error for %s: %s", filename, e)
            if self.status_callback:
                self.status_callback(f"Tagging error for {filename}: {e}")

    def queue_track(self, track_info, output_dir, audio_format='mp3', quality='192', cookies=None, embed_metadata=True, embed_lyrics=False):
        """Add a track to the download queue"""
        item = {
            'track_info': track_info,
            'output_dir': output_dir,
            'audio_format': audio_format,
            'quality': quality,
            'cookies': cookies,
            'embed_metadata': embed_metadata,
            'embed_lyrics': embed_lyrics,
        }
        with self._queue_lock:
            self._queue.append(item)
        logger.info("Queued track: %s - %s", track_info.get('artist', '?'), track_info.get('name', '?'))
        self._emit_track_event('queued', track_info, total=len(self._queue))
        return len(self._queue)

    def _emit_track_event(self, event, track_info=None, index=None, total=None):
        """Notify listeners about a track state change (drives the UI)."""
        if not self.on_track_event:
            return
        track_info = track_info or {}
        try:
            self.on_track_event(
                event,
                index if index is not None else len(self._queue),
                total if total is not None else self._total_queue_items,
                track_info.get('name', ''),
                track_info.get('artist', ''),
                platform_badge(track_info),
            )
        except Exception:
            logger.debug("on_track_event handler failed", exc_info=True)

    def process_queue(self):
        """Process the download queue in a background thread"""
        with self._queue_lock:
            if self._queue_running:
                logger.warning("Queue already running — appending to it")
                if self.status_callback:
                    self.status_callback("Queue already running — added to existing queue")
                return
            self._queue_running = True
            self._total_queue_items = len(self._queue)

        def _run_queue():
            # A guard so an unexpected exception can never leave
            # _queue_running stuck True, which would block every later
            # download for the lifetime of the process.
            try:
                self._drain_queue()
            except Exception as e:
                logger.exception("Queue runner crashed")
                if self.status_callback:
                    self.status_callback(f"Queue error: {e}")
            finally:
                with self._queue_lock:
                    self._queue_running = False
                    self._current_track_index = 0
                if self.progress_callback:
                    self.progress_callback(100)

        self._queue_thread = threading.Thread(target=_run_queue, daemon=True)
        try:
            self._queue_thread.start()
        except RuntimeError as e:
            # Thread creation can fail (e.g. interpreter shutdown). Reset the
            # flag or every later download is blocked for the whole session.
            with self._queue_lock:
                self._queue_running = False
            logger.exception("Could not start queue thread")
            if self.status_callback:
                self.status_callback(f"Queue error: {e}")

    def _drain_queue(self):
        self.reset_cancel()
        while True:
            with self._queue_lock:
                if not self._queue or self._cancel_event.is_set():
                    break
                item = self._queue.popleft()
                self._current_track_index = self._total_queue_items - len(self._queue)

            track = item['track_info']
            current = self._current_track_index
            total = self._total_queue_items

            if self.status_callback:
                # .get(): a malformed track must not raise KeyError here and
                # kill the whole queue along with every remaining track.
                label = f"{track.get('name', '?')} - {track.get('artist', '?')}"
                self.status_callback(f"[{current}/{total}] {label}")

            if self.queue_progress_callback:
                self.queue_progress_callback(current, total)
            self._emit_track_event('start', track, index=current, total=total)

            try:
                success, msg = self.download_track(
                    track,
                    item['output_dir'],
                    item['audio_format'],
                    item['quality'],
                    item['cookies'],
                    item['embed_metadata'],
                    item['embed_lyrics'],
                )
            except Exception as e:
                # Never let one bad track kill the whole queue.
                logger.exception("Download raised for %s", track)
                success, msg = False, f"{type(e).__name__}: {e}"

            if not success and self.status_callback:
                self.status_callback(f"Failed [{current}/{total}]: {msg}")
            self._emit_track_event(
                'done' if success else 'failed', track, index=current, total=total)

            if self._cancel_event.is_set():
                with self._queue_lock:
                    self._queue.clear()
                return

        if self.status_callback:
            self.status_callback("Queue finished")

    @property
    def queue_running(self):
        return self._queue_running


