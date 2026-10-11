"""MusicBrainz lookup and cover art download for NexusAudio Tag Editor."""
import os
import base64
import logging
import requests
import musicbrainzngs
from mutagen.flac import FLAC, Picture
from mutagen.oggvorbis import OggVorbis
from mutagen.mp4 import MP4, MP4Cover

logger = logging.getLogger(__name__)

musicbrainzngs.set_useragent("NexusAudio", "1.0", "https://github.com/NexusAudio")

COVER_ART_URL = "https://coverartarchive.org/release/{release_id}/front"
COVER_ART_JSON = "https://coverartarchive.org/release/{release_id}"


def _escape_phrase(text):
    """Escape characters that would break a Lucene quoted-phrase query."""
    return text.replace("\\", "\\\\").replace('"', '\\"')


def _run_release_search(query):
    try:
        result = musicbrainzngs.search_releases(query=query, limit=25)
        releases = []
        for r in result.get("release-list", []):
            artist_name = ""
            if "artist-credit" in r:
                for credit in r["artist-credit"]:
                    if isinstance(credit, dict) and "artist" in credit:
                        artist_name += credit["artist"].get("name", "")
                    elif isinstance(credit, str):
                        artist_name += credit
            releases.append({
                "id": r["id"],
                "title": r.get("title", ""),
                "artist": artist_name,
                "date": r.get("date", ""),
                "track_count": r.get("medium-list", [{}])[0].get("track-count", 0) if r.get("medium-list") else 0,
            })
        return releases
    except Exception as e:
        logger.warning("MusicBrainZ search error: %s", e)
        return []


def search_release(artist="", album=""):
    """Search MusicBrainZ for releases matching artist/album.
    Returns list of dicts: {id, title, artist, date, track_count}."""
    query_parts = []
    if artist:
        query_parts.append(f'artist:"{_escape_phrase(artist)}"')
    if album:
        query_parts.append(f'release:"{_escape_phrase(album)}"')
    query = " AND ".join(query_parts) if query_parts else album

    releases = _run_release_search(query)
    if not releases:
        # An exact quoted-phrase match found nothing - this is brittle for
        # titles that differ slightly (e.g. "(Deluxe Edition)" suffixes,
        # punctuation). Retry with a looser, unquoted query before giving up.
        loose_terms = " ".join(t for t in (artist, album) if t)
        if loose_terms and loose_terms != query:
            releases = _run_release_search(loose_terms)
    return releases


def get_release_details(release_id):
    """Fetch full release details including track listing.
    Returns (album_info, tracks) where tracks is list of dicts."""
    try:
        result = musicbrainzngs.get_release_by_id(
            release_id,
            includes=["recordings", "artist-credits", "tags", "labels"]
        )
        release = result.get("release", {})
        artist_name = ""
        for credit in release.get("artist-credit", []):
            if isinstance(credit, dict) and "artist" in credit:
                artist_name += credit["artist"].get("name", "")
            elif isinstance(credit, str):
                artist_name += credit

        album_info = {
            "album": release.get("title", ""),
            "artist": artist_name,
            "date": release.get("date", ""),
            "mbrainz_id": release_id,
        }

        tracks = []
        for medium in release.get("medium-list", []):
            for track in medium.get("track-list", []):
                rec = track.get("recording", {})
                track_artist = artist_name
                if "artist-credit" in rec:
                    a = ""
                    for credit in rec["artist-credit"]:
                        if isinstance(credit, dict) and "artist" in credit:
                            a += credit["artist"].get("name", "")
                        elif isinstance(credit, str):
                            a += credit
                    if a:
                        track_artist = a
                tracks.append({
                    "title": rec.get("title", ""),
                    "artist": track_artist,
                    "track": track.get("number", ""),
                    "length": rec.get("length", ""),
                })
        return album_info, tracks
    except Exception as e:
        logger.warning("MusicBrainZ release details error: %s", e)
        return {}, []


def fetch_cover_art(release_id):
    """Download front cover art for a release. Returns image bytes or None."""
    try:
        resp = requests.get(COVER_ART_URL.format(release_id=release_id), timeout=10)
        if resp.status_code == 200:
            return resp.content
    except Exception as e:
        logger.debug("Cover art fetch failed for %s: %s", release_id, e)
    return None


def get_cover_art_urls(release_id):
    """Get available cover art URLs for a release. Returns list of dicts with thumbnails."""
    try:
        resp = requests.get(COVER_ART_JSON.format(release_id=release_id), timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            images = []
            for img in data.get("images", []):
                images.append({
                    "types": img.get("types", []),
                    "front": img.get("front", False),
                    "thumb_small": img.get("thumbnails", {}).get("small", ""),
                    "thumb_large": img.get("thumbnails", {}).get("large", ""),
                    "image": img.get("image", ""),
                })
            return images
    except Exception as e:
        logger.debug("Cover art URLs failed for %s: %s", release_id, e)
    return []


def embed_cover_art(filepath, image_data, mime_type="image/jpeg"):
    """Embed cover art into an audio file. Supports MP3, FLAC, M4A, OGG."""
    ext = os.path.splitext(filepath)[1].lower()
    try:
        if ext == ".mp3":
            from mutagen.id3 import ID3, ID3NoHeaderError, APIC
            try:
                id3 = ID3(filepath)
            except ID3NoHeaderError:
                id3 = ID3()
            id3.delall("APIC")
            id3.add(APIC(encoding=3, mime=mime_type, type=3, desc="", data=image_data))
            id3.save(filepath)
            return True
        elif ext == ".flac":
            audio = FLAC(filepath)
            audio.clear_pictures()
            pic = Picture()
            pic.data = image_data
            pic.type = 3
            pic.mime = mime_type
            pic.desc = ""
            audio.add_picture(pic)
            audio.save()
            return True
        elif ext == ".ogg":
            # OggVorbis has no add_picture()/.pictures API like FLAC does -
            # cover art must be stored as a base64-encoded METADATA_BLOCK_PICTURE comment.
            pic = Picture()
            pic.data = image_data
            pic.type = 3
            pic.mime = mime_type
            pic.desc = ""
            encoded = base64.b64encode(pic.write()).decode("ascii")
            audio = OggVorbis(filepath)
            audio["metadata_block_picture"] = [encoded]
            audio.save()
            return True
        elif ext == ".m4a":
            audio = MP4(filepath)
            if mime_type == "image/png":
                covr = [MP4Cover(image_data, MP4Cover.FORMAT_PNG)]
            else:
                covr = [MP4Cover(image_data, MP4Cover.FORMAT_JPEG)]
            audio["covr"] = covr
            audio.save()
            return True
    except Exception as e:
        logger.warning("Cover art embed failed for %s: %s", filepath, e)
    return False


def _track_int(value):
    """Parse a track-number string like '3', '03', or '3/12' into an int, or None."""
    if not value:
        return None
    head = str(value).split("/")[0].strip()
    try:
        return int(head)
    except ValueError:
        return None


def apply_release_to_files(filepaths, album_info, tracks):
    """Write album-level tags + track-specific tags to a list of files."""
    from core.tagger import Tagger
    matched = 0
    for filepath in filepaths:
        tags = Tagger.read_tags(filepath)
        track_num = _track_int(tags.get("tracknumber", ""))
        matched_track = None
        if track_num is not None:
            for t in tracks:
                if _track_int(t.get("track")) == track_num:
                    matched_track = t
                    break
        if not matched_track:
            source = tracks[matched] if matched < len(tracks) else None
            if source:
                # Copy: mutating the fetched release would inject track numbers
                # that make later files match the wrong track.
                matched_track = dict(source)
                matched_track["track"] = str(matched + 1)
        write = {
            "album": album_info.get("album", ""),
            "artist": album_info.get("artist", ""),
        }
        if matched_track:
            write["title"] = matched_track.get("title", "")
            write["tracknumber"] = matched_track.get("track", "")
            if matched_track.get("artist"):
                write["artist"] = matched_track["artist"]
        if album_info.get("date"):
            write["year"] = album_info["date"][:4]
        Tagger.write_tags(filepath, write)
        matched += 1
    return matched
