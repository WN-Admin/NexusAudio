import os
import sys
import unittest
import tempfile
import shutil
from unittest.mock import patch, Mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from core.metadata import extract_metadata, fetch_lyrics, embed_lyrics


class TestExtractMetadata(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.mp3_path = os.path.join(self.tmpdir, 'track.mp3')
        with open(self.mp3_path, 'wb') as f:
            f.write(b'\x00')
        from mutagen.easyid3 import EasyID3
        audio = EasyID3()
        audio.save(self.mp3_path)

        self.flac_path = os.path.join(self.tmpdir, 'track.flac')
        with open(self.flac_path, 'wb') as f:
            f.write(b'fLaC' + os.urandom(256))

        self.m4a_path = os.path.join(self.tmpdir, 'track.m4a')
        with open(self.m4a_path, 'wb') as f:
            f.write(b'\x00\x00\x00\x20ftypmp42' + os.urandom(100))

        self.ogg_path = os.path.join(self.tmpdir, 'track.ogg')
        with open(self.ogg_path, 'wb') as f:
            f.write(b'OggS' + os.urandom(200))

        self.unknown_path = os.path.join(self.tmpdir, 'file.xyz')
        with open(self.unknown_path, 'wb') as f:
            f.write(b'\x00\x01\x02\x03')

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    def test_extract_metadata_returns_dict_with_expected_keys(self):
        metadata = extract_metadata(self.mp3_path)
        expected_keys = {'filepath', 'filename', 'title', 'artist', 'album',
                         'genre', 'year', 'tracknumber', 'lyrics',
                         'duration', 'bitrate'}
        for key in expected_keys:
            self.assertIn(key, metadata)
        self.assertEqual(metadata['filepath'], self.mp3_path)
        self.assertEqual(metadata['filename'], 'track.mp3')

    def test_unsupported_format_returns_defaults(self):
        metadata = extract_metadata(self.unknown_path)
        self.assertEqual(metadata['title'], '')
        self.assertEqual(metadata['artist'], '')
        self.assertEqual(metadata['duration'], 0)
        self.assertEqual(metadata['bitrate'], 0)

    def test_invalid_flac_returns_defaults(self):
        metadata = extract_metadata(self.flac_path)
        self.assertIsInstance(metadata, dict)
        self.assertEqual(metadata['filepath'], self.flac_path)

    def test_invalid_m4a_returns_defaults(self):
        metadata = extract_metadata(self.m4a_path)
        self.assertIsInstance(metadata, dict)
        self.assertEqual(metadata['filepath'], self.m4a_path)

    def test_invalid_ogg_returns_defaults(self):
        metadata = extract_metadata(self.ogg_path)
        self.assertIsInstance(metadata, dict)
        self.assertEqual(metadata['filepath'], self.ogg_path)


class TestFetchLyrics(unittest.TestCase):
    @patch('core.metadata.requests.get')
    def test_returns_lyrics_on_success(self, mock_get):
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {'lyrics': 'Hello world\nline two'}
        mock_get.return_value = mock_response
        lyrics = fetch_lyrics('Test Song', 'Test Artist')
        self.assertEqual(lyrics, 'Hello world\nline two')
        mock_get.assert_called_once_with(
            'https://api.lyrics.ovh/v1/Test%20Artist/Test%20Song',
            timeout=5
        )

    @patch('core.metadata.requests.get')
    def test_encodes_path_segments(self, mock_get):
        """A '/' in the artist must not restructure the request path."""
        mock_response = Mock()
        mock_response.status_code = 404
        mock_get.return_value = mock_response
        fetch_lyrics('Thunderstruck', 'AC/DC')
        url = mock_get.call_args_list[0][0][0]
        self.assertEqual(
            url, 'https://api.lyrics.ovh/v1/AC%2FDC/Thunderstruck')

    @patch('core.metadata.requests.get')
    def test_returns_empty_on_not_found(self, mock_get):
        mock_response = Mock()
        mock_response.status_code = 404
        mock_get.return_value = mock_response
        self.assertEqual(fetch_lyrics('Unknown', 'Nobody'), '')

    @patch('core.metadata.requests.get')
    def test_returns_empty_on_exception(self, mock_get):
        import requests
        mock_get.side_effect = requests.RequestException('Connection error')
        self.assertEqual(fetch_lyrics('Test', 'Artist'), '')

    @patch('core.metadata.requests.get')
    def test_missing_lyrics_key_returns_empty(self, mock_get):
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {}
        mock_get.return_value = mock_response
        self.assertEqual(fetch_lyrics('Test', 'Artist'), '')

    @patch('core.metadata.requests.get')
    def test_returns_empty_on_timeout(self, mock_get):
        import requests
        mock_get.side_effect = requests.Timeout('timed out')
        self.assertEqual(fetch_lyrics('Test', 'Artist'), '')


class TestEmbedLyrics(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.mp3_path = os.path.join(self.tmpdir, 'track.mp3')
        with open(self.mp3_path, 'wb') as f:
            f.write(b'\x00')
        from mutagen.easyid3 import EasyID3
        audio = EasyID3()
        audio.save(self.mp3_path)

        self.flac_path = os.path.join(self.tmpdir, 'track.flac')
        with open(self.flac_path, 'wb') as f:
            f.write(b'fLaC' + os.urandom(256))

        self.m4a_path = os.path.join(self.tmpdir, 'track.m4a')
        with open(self.m4a_path, 'wb') as f:
            f.write(b'\x00' * 100)

        self.ogg_path = os.path.join(self.tmpdir, 'track.ogg')
        with open(self.ogg_path, 'wb') as f:
            f.write(b'OggS' + os.urandom(200))

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    def test_mp3_returns_true(self):
        self.assertTrue(embed_lyrics(self.mp3_path, 'Test lyrics'))

    def test_mp3_roundtrip(self):
        embed_lyrics(self.mp3_path, 'Round trip lyrics')
        metadata = extract_metadata(self.mp3_path)
        self.assertEqual(metadata.get('lyrics', ''), 'Round trip lyrics')

    def test_flac_handles_invalid_gracefully(self):
        result = embed_lyrics(self.flac_path, 'Flac lyrics')
        self.assertIsInstance(result, bool)

    def test_m4a_handles_invalid_gracefully(self):
        result = embed_lyrics(self.m4a_path, 'M4a lyrics')
        self.assertIsInstance(result, bool)

    def test_ogg_handles_invalid_gracefully(self):
        result = embed_lyrics(self.ogg_path, 'Ogg lyrics')
        self.assertIsInstance(result, bool)

    def test_unsupported_format_returns_false(self):
        path = os.path.join(self.tmpdir, 'file.wav')
        with open(path, 'wb') as f:
            f.write(b'\x00' * 100)
        self.assertFalse(embed_lyrics(path, 'lyrics'))

    def test_nonexistent_file_returns_false(self):
        self.assertFalse(embed_lyrics('/nonexistent/file.mp3', 'lyrics'))


if __name__ == '__main__':
    unittest.main()
