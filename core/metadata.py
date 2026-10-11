"""
NexusAudio Metadata & Lyrics Module
Handles metadata extraction and lyric fetching for audio files
"""
import os
import re
import logging
import requests
from urllib.parse import quote
from mutagen.easyid3 import EasyID3
from mutagen.id3 import ID3NoHeaderError
from mutagen.flac import FLAC
from mutagen.mp4 import MP4
from mutagen.oggvorbis import OggVorbis

logger = logging.getLogger(__name__)


def extract_metadata(filepath):
    """Extract metadata from audio file"""
    ext = os.path.splitext(filepath)[1].lower()
    metadata = {
        'filepath': filepath,
        'filename': os.path.basename(filepath),
        'title': '',
        'artist': '',
        'album': '',
        'genre': '',
        'year': '',
        'tracknumber': '',
        'lyrics': '',
        'duration': 0,
        'bitrate': 0,
    }
    
    try:
        if ext == '.mp3':
            audio = EasyID3(filepath)
            metadata['title'] = audio.get('title', [''])[0]
            metadata['artist'] = audio.get('artist', [''])[0]
            metadata['album'] = audio.get('album', [''])[0]
            metadata['genre'] = audio.get('genre', [''])[0]
            metadata['year'] = audio.get('date', [''])[0]
            metadata['tracknumber'] = audio.get('tracknumber', [''])[0]
            try:
                from mutagen.mp3 import MP3 as Mp3File
                mp3 = Mp3File(filepath)
                metadata['duration'] = int(mp3.info.length)
                metadata['bitrate'] = int(mp3.info.bitrate / 1000)
            except Exception:
                pass
            try:
                from mutagen.id3 import ID3
                id3 = ID3(filepath)
                lyrics_frames = id3.getall('USLT')
                if lyrics_frames:
                    metadata['lyrics'] = lyrics_frames[0].text
            except Exception:
                pass
                
        elif ext == '.flac':
            audio = FLAC(filepath)
            metadata['title'] = audio.get('title', [''])[0] if audio.get('title') else ''
            metadata['artist'] = audio.get('artist', [''])[0] if audio.get('artist') else ''
            metadata['album'] = audio.get('album', [''])[0] if audio.get('album') else ''
            metadata['genre'] = audio.get('genre', [''])[0] if audio.get('genre') else ''
            metadata['year'] = audio.get('date', [''])[0] if audio.get('date') else ''
            metadata['tracknumber'] = audio.get('tracknumber', [''])[0] if audio.get('tracknumber') else ''
            metadata['duration'] = int(audio.info.length)
            metadata['bitrate'] = int(audio.info.bitrate / 1000)
            if 'lyrics' in audio:
                metadata['lyrics'] = audio['lyrics'][0]
                
        elif ext == '.m4a':
            audio = MP4(filepath)
            metadata['title'] = audio.get('\xa9nam', [''])[0] if audio.get('\xa9nam') else ''
            metadata['artist'] = audio.get('\xa9ART', [''])[0] if audio.get('\xa9ART') else ''
            metadata['album'] = audio.get('\xa9alb', [''])[0] if audio.get('\xa9alb') else ''
            metadata['duration'] = int(audio.info.length)
            metadata['bitrate'] = int(audio.info.bitrate / 1000)
            
        elif ext == '.ogg':
            audio = OggVorbis(filepath)
            metadata['title'] = audio.get('title', [''])[0] if audio.get('title') else ''
            metadata['artist'] = audio.get('artist', [''])[0] if audio.get('artist') else ''
            metadata['album'] = audio.get('album', [''])[0] if audio.get('album') else ''
            metadata['genre'] = audio.get('genre', [''])[0] if audio.get('genre') else ''
            metadata['year'] = audio.get('date', [''])[0] if audio.get('date') else ''
            metadata['tracknumber'] = audio.get('tracknumber', [''])[0] if audio.get('tracknumber') else ''
            metadata['duration'] = int(audio.info.length)
            metadata['bitrate'] = int(audio.info.bitrate / 1000)
            
    except Exception as e:
        logger.warning("Metadata extraction error for %s: %s", filepath, e)
    
    return metadata


def _slugify(text):
    """Convert text to a Genius URL slug."""
    text = text.lower().strip()
    text = re.sub(r'[^a-z0-9]+', '-', text)
    return text.strip('-')


def _scrape_genius_lyrics(title, artist):
    """Fetch lyrics directly from Genius.com (no API key needed)."""
    slug = f'{_slugify(artist)}-{_slugify(title)}-lyrics'
    url = f'https://genius.com/{slug}'
    headers = {
        'User-Agent': (
            'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
            '(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        )
    }
    try:
        resp = requests.get(url, headers=headers, timeout=10)
        if resp.status_code != 200 or not isinstance(resp.text, str):
            return ''
        containers = re.findall(
            r'<div[^>]*data-lyrics-container="true"[^>]*>(.*?)</div>',
            resp.text, re.DOTALL
        )
        parts = []
        for c in containers:
            text = re.sub(r'<[^>]+>', '', c)
            text = text.replace('&#x27;', "'")
            text = text.replace('&amp;', '&')
            text = text.replace('&quot;', '"')
            text = text.replace('&lt;', '<')
            text = text.replace('&gt;', '>')
            text = text.replace('[', '\n[').strip()
            if text:
                parts.append(text)
        return '\n\n'.join(parts).strip() if parts else ''
    except requests.RequestException as e:
        logger.debug("Genius scrape failed for %s - %s: %s", title, artist, e)
        return ''


def fetch_lyrics(title, artist):
    """Fetch lyrics for a song. Tries lyrics.ovh API first, then Genius.com."""
    try:
        # The API takes the artist/title as path segments, so each must be
        # percent-encoded: raw interpolation breaks on '/', '#' or '?'
        # (e.g. AC/DC) and silently requests the wrong endpoint.
        url = (
            "https://api.lyrics.ovh/v1/"
            f"{quote(artist, safe='')}/{quote(title, safe='')}"
        )
        response = requests.get(url, timeout=5)
        if response.status_code == 200:
            data = response.json()
            lyrics = data.get('lyrics', '')
            if lyrics:
                return lyrics
    except requests.RequestException as e:
        logger.debug("Lyrics.ovh failed for %s - %s: %s", title, artist, e)
    return _scrape_genius_lyrics(title, artist)


def embed_lyrics(filepath, lyrics):
    """Embed lyrics into audio file"""
    ext = os.path.splitext(filepath)[1].lower()
    
    try:
        if ext == '.mp3':
            from mutagen.id3 import ID3, USLT
            try:
                id3 = ID3(filepath)
            except ID3NoHeaderError:
                id3 = ID3()
            
            # Remove existing lyrics
            id3.delall('USLT')
            # Add new lyrics
            id3.add(USLT(encoding=3, lang='eng', desc='', text=lyrics))
            id3.save(filepath)
            return True
            
        elif ext == '.flac':
            audio = FLAC(filepath)
            audio['lyrics'] = lyrics
            audio.save()
            return True
            
        elif ext == '.m4a':
            audio = MP4(filepath)
            audio['\xa9lyr'] = [lyrics]
            audio.save()
            return True
            
        elif ext == '.ogg':
            audio = OggVorbis(filepath)
            audio['lyrics'] = lyrics
            audio.save()
            return True
            
    except Exception as e:
        logger.warning("Embed lyrics error for %s: %s", filepath, e)
        return False
    
    return False


def get_supported_formats():
    """Return list of supported formats for metadata/lyrics"""
    return ['.mp3', '.flac', '.m4a', '.ogg']
