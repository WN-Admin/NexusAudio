import os
import logging
from mutagen.easyid3 import EasyID3
from mutagen.flac import FLAC
from mutagen.mp4 import MP4
from mutagen.oggvorbis import OggVorbis
from core.utils import sanitize_filename, safe_join

logger = logging.getLogger(__name__)


def _is_blank(value):
    return value is None or (isinstance(value, str) and not value.strip())


def _apply(audio, key, value, wrap=False):
    """Set a tag, removing it when the value is blank.

    mutagen's handling of an empty string differs between formats (some drop
    the frame, some keep an empty one), so clearing is done explicitly. This
    is what makes emptying a field in the tag panel actually delete the tag.
    """
    if _is_blank(value):
        try:
            del audio[key]
        except (KeyError, ValueError, TypeError):
            pass
        return
    audio[key] = [value] if wrap else value

_MP3_EXTENDED_MAP = {
    'composer': 'composer', 'albumartist': 'albumartist', 'discnumber': 'discnumber',
    'bpm': 'bpm', 'isrc': 'isrc', 'comment': 'comment', 'copyright': 'copyright',
    'encodedby': 'encodedby', 'performer': 'performer', 'lyricist': 'lyricist',
    'publisher': 'publisher', 'grouping': 'grouping',
}

_MP4_EXTENDED_MAP = {
    'composer': '\xa9wrt', 'albumartist': 'aART', 'discnumber': 'disk',
    'bpm': 'tmpo', 'comment': '\xa9cmt', 'copyright': 'cprt',
    'encodedby': '\xa9too', 'grouping': '\xa9grp', 'compilation': 'cpil',
    'gapless': 'pgap',
}

EXTENDED_KEYS = list(_MP3_EXTENDED_MAP.keys())


class Tagger:
    @staticmethod
    def get_supported_files(directory):
        supported = ('.mp3', '.flac', '.m4a', '.ogg')
        files = []
        if not os.path.isdir(directory):
            return files
        for f in os.listdir(directory):
            if f.lower().endswith(supported):
                files.append(os.path.join(directory, f))
        return sorted(files)

    @staticmethod
    def read_tags(filepath):
        ext = os.path.splitext(filepath)[1].lower()
        tags = {'filepath': filepath, 'filename': os.path.basename(filepath)}

        try:
            if ext == '.mp3':
                audio = EasyID3(filepath)
                tags['title'] = audio.get('title', [''])[0]
                tags['artist'] = audio.get('artist', [''])[0]
                tags['album'] = audio.get('album', [''])[0]
                tags['genre'] = audio.get('genre', [''])[0]
                tags['year'] = audio.get('date', [''])[0]
                tags['tracknumber'] = audio.get('tracknumber', [''])[0]
                for gk, ek in _MP3_EXTENDED_MAP.items():
                    tags[gk] = audio.get(ek, [''])[0]
            elif ext == '.flac':
                audio = FLAC(filepath)
                tags['title'] = audio.get('title', [''])[0] if audio.get('title') else ''
                tags['artist'] = audio.get('artist', [''])[0] if audio.get('artist') else ''
                tags['album'] = audio.get('album', [''])[0] if audio.get('album') else ''
                tags['genre'] = audio.get('genre', [''])[0] if audio.get('genre') else ''
                tags['year'] = audio.get('date', [''])[0] if audio.get('date') else ''
                tags['tracknumber'] = audio.get('tracknumber', [''])[0] if audio.get('tracknumber') else ''
                for gk in EXTENDED_KEYS:
                    tags[gk] = audio.get(gk, [''])[0] if audio.get(gk) else ''
            elif ext == '.m4a':
                audio = MP4(filepath)
                tags['title'] = audio.get('\xa9nam', [''])[0] if audio.get('\xa9nam') else ''
                tags['artist'] = audio.get('\xa9ART', [''])[0] if audio.get('\xa9ART') else ''
                tags['album'] = audio.get('\xa9alb', [''])[0] if audio.get('\xa9alb') else ''
                for gk, mk in _MP4_EXTENDED_MAP.items():
                    vals = audio.get(mk)
                    if vals:
                        v = vals[0]
                        tags[gk] = str(v) if not isinstance(v, bytes) else v.decode('utf-8', errors='replace')
                    else:
                        tags[gk] = ''
            elif ext == '.ogg':
                audio = OggVorbis(filepath)
                tags['title'] = audio.get('title', [''])[0] if audio.get('title') else ''
                tags['artist'] = audio.get('artist', [''])[0] if audio.get('artist') else ''
                tags['album'] = audio.get('album', [''])[0] if audio.get('album') else ''
                tags['genre'] = audio.get('genre', [''])[0] if audio.get('genre') else ''
                tags['year'] = audio.get('date', [''])[0] if audio.get('date') else ''
                tags['tracknumber'] = audio.get('tracknumber', [''])[0] if audio.get('tracknumber') else ''
                for gk in EXTENDED_KEYS:
                    tags[gk] = audio.get(gk, [''])[0] if audio.get(gk) else ''
        except Exception as e:
            logger.warning("Failed to read tags from %s: %s", filepath, e)

        return tags

    @staticmethod
    def write_tags(filepath, tags):
        ext = os.path.splitext(filepath)[1].lower()

        try:
            if ext == '.mp3':
                audio = EasyID3(filepath)
                if 'title' in tags: _apply(audio, 'title', tags['title'])
                if 'artist' in tags: _apply(audio, 'artist', tags['artist'])
                if 'album' in tags: _apply(audio, 'album', tags['album'])
                if 'genre' in tags: _apply(audio, 'genre', tags['genre'])
                if 'year' in tags: _apply(audio, 'date', tags['year'])
                if 'tracknumber' in tags: _apply(audio, 'tracknumber', tags['tracknumber'])
                for gk, ek in _MP3_EXTENDED_MAP.items():
                    if gk in tags:
                        _apply(audio, ek, tags[gk])
                audio.save()
            elif ext in ('.flac', '.ogg'):
                if ext == '.flac':
                    audio = FLAC(filepath)
                else:
                    audio = OggVorbis(filepath)
                if 'title' in tags: _apply(audio, 'title', tags['title'])
                if 'artist' in tags: _apply(audio, 'artist', tags['artist'])
                if 'album' in tags: _apply(audio, 'album', tags['album'])
                if 'genre' in tags: _apply(audio, 'genre', tags['genre'])
                if 'year' in tags: _apply(audio, 'date', tags['year'])
                if 'tracknumber' in tags: _apply(audio, 'tracknumber', tags['tracknumber'])
                for gk in EXTENDED_KEYS:
                    if gk in tags:
                        _apply(audio, gk, tags[gk])
                audio.save()
            elif ext == '.m4a':
                audio = MP4(filepath)
                if 'title' in tags: _apply(audio, '\xa9nam', tags['title'], wrap=True)
                if 'artist' in tags: _apply(audio, '\xa9ART', tags['artist'], wrap=True)
                if 'album' in tags: _apply(audio, '\xa9alb', tags['album'], wrap=True)
                for gk, mk in _MP4_EXTENDED_MAP.items():
                    if gk not in tags:
                        continue
                    val = tags[gk]
                    if _is_blank(val):
                        try:
                            del audio[mk]
                        except (KeyError, ValueError, TypeError):
                            pass
                    elif mk in ('tmpo', 'cpil', 'pgap'):
                        try:
                            audio[mk] = [int(val)]
                        except (ValueError, TypeError):
                            audio[mk] = [0]
                    elif mk == 'disk':
                        parts = str(val).split('/')
                        try:
                            audio[mk] = [(int(parts[0]), int(parts[1]) if len(parts) > 1 else 0)]
                        except (ValueError, TypeError):
                            pass
                    else:
                        audio[mk] = [str(val)]
                audio.save()
            else:
                return False
            return True
        except Exception as e:
            logger.warning("Failed to write tags to %s: %s", filepath, e)
            return False

    @staticmethod
    def delete_all_tags(filepath):
        """Remove all tags from a file."""
        ext = os.path.splitext(filepath)[1].lower()
        try:
            if ext == '.mp3':
                audio = EasyID3(filepath)
                audio.delete()
            elif ext == '.flac':
                audio = FLAC(filepath)
                audio.delete()
            elif ext == '.ogg':
                audio = OggVorbis(filepath)
                audio.delete()
            elif ext == '.m4a':
                audio = MP4(filepath)
                audio.delete()
            return True
        except Exception as e:
            logger.warning("Failed to delete tags from %s: %s", filepath, e)
            return False

    @staticmethod
    def rename_to_artist_title(filepath):
        try:
            tags = Tagger.read_tags(filepath)
            if tags.get('artist') and tags.get('title'):
                ext = os.path.splitext(filepath)[1]
                # Tags are attacker-controllable metadata; sanitize before
                # building a path so a '/' in a tag cannot escape the folder.
                new_name = (
                    f"{sanitize_filename(tags['artist'])} - "
                    f"{sanitize_filename(tags['title'])}{ext}"
                )
                new_path = safe_join(os.path.dirname(filepath) or '.', new_name)
                if new_path is None:
                    logger.warning("Unsafe rename target for %s", filepath)
                    return False
                if new_path != filepath and os.path.exists(new_path):
                    logger.warning("Target exists, skipping rename: %s", new_path)
                    return False
                os.rename(filepath, new_path)
                return True
        except OSError as e:
            logger.warning("Rename failed for %s: %s", filepath, e)
        return False
