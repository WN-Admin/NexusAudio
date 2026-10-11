import os
import sys
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch, Mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from core.musicbrainz import (
    search_release, _run_release_search, _escape_phrase, _track_int,
    embed_cover_art, apply_release_to_files,
)

FFMPEG_AVAILABLE = shutil.which('ffmpeg') is not None


def _make_fixture(path, duration=1):
    subprocess.run(
        ['ffmpeg', '-y', '-loglevel', 'error', '-f', 'lavfi',
         '-i', f'sine=frequency=440:duration={duration}', path],
        check=True,
    )


class TestTrackInt(unittest.TestCase):
    def test_plain_number(self):
        self.assertEqual(_track_int('7'), 7)

    def test_zero_padded(self):
        self.assertEqual(_track_int('07'), 7)

    def test_disc_slash_total(self):
        self.assertEqual(_track_int('3/12'), 3)

    def test_empty_returns_none(self):
        self.assertIsNone(_track_int(''))
        self.assertIsNone(_track_int(None))

    def test_non_numeric_returns_none(self):
        self.assertIsNone(_track_int('A1'))


class TestEscapePhrase(unittest.TestCase):
    def test_escapes_quotes(self):
        self.assertEqual(_escape_phrase('Guns N\' Roses "Live"'), 'Guns N\' Roses \\"Live\\"')

    def test_escapes_backslash(self):
        self.assertEqual(_escape_phrase('a\\b'), 'a\\\\b')

    def test_plain_text_unchanged(self):
        self.assertEqual(_escape_phrase('Daft Punk'), 'Daft Punk')


class TestSearchRelease(unittest.TestCase):
    @patch('core.musicbrainz.musicbrainzngs.search_releases')
    def test_builds_quoted_field_query(self, mock_search):
        mock_search.return_value = {
            'release-list': [{'id': 'r1', 'title': 'Discovery', 'date': '2001', 'artist-credit': []}]
        }
        search_release(artist='Daft Punk', album='Discovery')
        called_query = mock_search.call_args.kwargs['query']
        self.assertIn('artist:"Daft Punk"', called_query)
        self.assertIn('release:"Discovery"', called_query)
        self.assertIn(' AND ', called_query)

    @patch('core.musicbrainz.musicbrainzngs.search_releases')
    def test_falls_back_to_loose_query_when_strict_finds_nothing(self, mock_search):
        # First call (strict quoted phrase) -> no results.
        # Second call (loose, unquoted) -> one result.
        mock_search.side_effect = [
            {'release-list': []},
            {'release-list': [{'id': 'abc-123', 'title': 'Discovery (Remastered)',
                                'date': '2001', 'artist-credit': []}]},
        ]
        releases = search_release(artist='Daft Punk', album='Discovery')
        self.assertEqual(mock_search.call_count, 2)
        first_query = mock_search.call_args_list[0].kwargs['query']
        second_query = mock_search.call_args_list[1].kwargs['query']
        self.assertIn('"', first_query)
        self.assertNotIn('"', second_query)
        self.assertEqual(len(releases), 1)
        self.assertEqual(releases[0]['id'], 'abc-123')

    @patch('core.musicbrainz.musicbrainzngs.search_releases')
    def test_does_not_retry_when_strict_query_succeeds(self, mock_search):
        mock_search.return_value = {
            'release-list': [{'id': 'xyz', 'title': 'Discovery', 'date': '2001', 'artist-credit': []}]
        }
        releases = search_release(artist='Daft Punk', album='Discovery')
        self.assertEqual(mock_search.call_count, 1)
        self.assertEqual(len(releases), 1)

    @patch('core.musicbrainz.musicbrainzngs.search_releases')
    def test_network_error_returns_empty_list_not_exception(self, mock_search):
        mock_search.side_effect = Exception('boom')
        releases = search_release(artist='X', album='Y')
        self.assertEqual(releases, [])

    @patch('core.musicbrainz.musicbrainzngs.search_releases')
    def test_parses_artist_credit_and_track_count(self, mock_search):
        mock_search.return_value = {
            'release-list': [{
                'id': 'r1', 'title': 'Discovery', 'date': '2001-03-12',
                'artist-credit': [{'artist': {'name': 'Daft Punk'}}],
                'medium-list': [{'track-count': 14}],
            }]
        }
        releases = search_release(album='Discovery')
        self.assertEqual(releases[0]['artist'], 'Daft Punk')
        self.assertEqual(releases[0]['track_count'], 14)


@unittest.skipUnless(FFMPEG_AVAILABLE, 'ffmpeg not available to generate test fixtures')
class TestEmbedCoverArt(unittest.TestCase):
    """Real round-trip tests against actual audio files - fake/garbage byte
    fixtures aren't sufficient here because mutagen needs a valid container
    to actually rewrite, which is exactly what masked these bugs before."""

    @classmethod
    def setUpClass(cls):
        cls.tmpdir = tempfile.mkdtemp()
        cls.paths = {}
        for ext in ('mp3', 'flac', 'ogg', 'm4a'):
            path = os.path.join(cls.tmpdir, f'test.{ext}')
            _make_fixture(path)
            cls.paths[ext] = path
        cls.cover_bytes = (b'\xff\xd8\xff\xe0FAKEJPEGPAYLOAD') * 20

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmpdir, ignore_errors=True)

    def _extract(self, ext):
        path = self.paths[ext]
        if ext == 'mp3':
            from mutagen.id3 import ID3
            audio = ID3(path)
            tags = audio.getall('APIC')
            return tags[0].data if tags else None
        elif ext == 'flac':
            from mutagen.flac import FLAC
            audio = FLAC(path)
            return audio.pictures[0].data if audio.pictures else None
        elif ext == 'ogg':
            import base64
            from mutagen.oggvorbis import OggVorbis
            from mutagen.flac import Picture
            audio = OggVorbis(path)
            raw = audio.get('metadata_block_picture', [])
            if not raw:
                return None
            return Picture(base64.b64decode(raw[0])).data
        elif ext == 'm4a':
            from mutagen.mp4 import MP4
            audio = MP4(path)
            covr = audio.get('covr', [])
            return bytes(covr[0]) if covr else None

    def test_mp3_roundtrip(self):
        self.assertTrue(embed_cover_art(self.paths['mp3'], self.cover_bytes, 'image/jpeg'))
        self.assertEqual(self._extract('mp3'), self.cover_bytes)

    def test_flac_roundtrip(self):
        self.assertTrue(embed_cover_art(self.paths['flac'], self.cover_bytes, 'image/jpeg'))
        self.assertEqual(self._extract('flac'), self.cover_bytes)

    def test_ogg_roundtrip(self):
        self.assertTrue(embed_cover_art(self.paths['ogg'], self.cover_bytes, 'image/jpeg'))
        self.assertEqual(self._extract('ogg'), self.cover_bytes)

    def test_m4a_roundtrip(self):
        self.assertTrue(embed_cover_art(self.paths['m4a'], self.cover_bytes, 'image/jpeg'))
        self.assertEqual(self._extract('m4a'), self.cover_bytes)

    def test_unsupported_extension_returns_false(self):
        bogus = os.path.join(self.tmpdir, 'notes.txt')
        with open(bogus, 'wb') as f:
            f.write(b'hello')
        self.assertFalse(embed_cover_art(bogus, self.cover_bytes))


class TestApplyReleaseToFiles(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not FFMPEG_AVAILABLE:
            raise unittest.SkipTest('ffmpeg not available to generate test fixtures')

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.path = os.path.join(self.tmpdir, 'song.mp3')
        _make_fixture(self.path)

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_matches_zero_padded_tracknumber(self):
        from core.tagger import Tagger
        Tagger.write_tags(self.path, {'tracknumber': '02'})
        album_info = {'album': 'Discovery', 'artist': 'Daft Punk', 'date': '2001'}
        tracks = [
            {'title': 'One More Time', 'artist': 'Daft Punk', 'track': '1'},
            {'title': 'Aerodynamic', 'artist': 'Daft Punk', 'track': '2'},
        ]
        matched = apply_release_to_files([self.path], album_info, tracks)
        self.assertEqual(matched, 1)
        tags = Tagger.read_tags(self.path)
        self.assertEqual(tags['title'], 'Aerodynamic')

    def test_falls_back_to_position_when_no_tracknumber(self):
        from core.tagger import Tagger
        album_info = {'album': 'Discovery', 'artist': 'Daft Punk', 'date': '2001'}
        tracks = [{'title': 'One More Time', 'artist': 'Daft Punk', 'track': '1'}]
        apply_release_to_files([self.path], album_info, tracks)
        tags = Tagger.read_tags(self.path)
        self.assertEqual(tags['title'], 'One More Time')


if __name__ == '__main__':
    unittest.main()
