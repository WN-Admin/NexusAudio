import os
import sys
import unittest
import tempfile
import shutil

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from core.tagger import Tagger


class TestGetSupportedFiles(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.mp3_path = os.path.join(self.tmpdir, 'test.mp3')
        self.flac_path = os.path.join(self.tmpdir, 'test.flac')
        self.ogg_path = os.path.join(self.tmpdir, 'song.ogg')
        self.m4a_path = os.path.join(self.tmpdir, 'song.m4a')
        self.txt_path = os.path.join(self.tmpdir, 'notes.txt')
        self.jpg_path = os.path.join(self.tmpdir, 'cover.jpg')
        for p in [self.mp3_path, self.flac_path, self.ogg_path,
                  self.m4a_path, self.txt_path, self.jpg_path]:
            with open(p, 'wb') as f:
                f.write(b'\x00')

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    def test_filters_correctly(self):
        files = Tagger.get_supported_files(self.tmpdir)
        basenames = [os.path.basename(f) for f in files]
        self.assertIn('test.mp3', basenames)
        self.assertIn('test.flac', basenames)
        self.assertIn('song.ogg', basenames)
        self.assertIn('song.m4a', basenames)
        self.assertNotIn('notes.txt', basenames)
        self.assertNotIn('cover.jpg', basenames)

    def test_invalid_dir_returns_empty(self):
        self.assertEqual(Tagger.get_supported_files('/nonexistent/path'), [])

    def test_returns_sorted(self):
        extra = os.path.join(self.tmpdir, 'aaa.mp3')
        with open(extra, 'wb') as f:
            f.write(b'\x00')
        files = Tagger.get_supported_files(self.tmpdir)
        names = [os.path.basename(f) for f in files]
        self.assertEqual(names, sorted(names))


class TestReadTags(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.mp3_path = os.path.join(self.tmpdir, 'test.mp3')
        with open(self.mp3_path, 'wb') as f:
            f.write(b'\x00')
        from mutagen.easyid3 import EasyID3
        audio = EasyID3()
        audio.save(self.mp3_path)

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    def test_returns_dict_with_expected_keys(self):
        tags = Tagger.read_tags(self.mp3_path)
        expected_keys = {'filepath', 'filename', 'title', 'artist', 'album',
                         'genre', 'year', 'tracknumber'}
        for key in expected_keys:
            self.assertIn(key, tags)
        self.assertEqual(tags['filepath'], self.mp3_path)
        self.assertEqual(tags['filename'], 'test.mp3')

    def test_nonexistent_file_returns_defaults(self):
        tags = Tagger.read_tags('/nonexistent/file.mp3')
        self.assertIn('filepath', tags)
        self.assertEqual(tags['filename'], 'file.mp3')

    def test_missing_extension_returns_partial_dict(self):
        path = os.path.join(self.tmpdir, 'no_ext')
        with open(path, 'wb') as f:
            f.write(b'data')
        tags = Tagger.read_tags(path)
        self.assertIn('filepath', tags)
        self.assertIn('filename', tags)


class TestWriteTags(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.mp3_path = os.path.join(self.tmpdir, 'test.mp3')
        with open(self.mp3_path, 'wb') as f:
            f.write(b'\x00')
        from mutagen.easyid3 import EasyID3
        audio = EasyID3()
        audio.save(self.mp3_path)

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    def test_blank_value_removes_tag(self):
        """Emptying a field must delete the tag, not silently do nothing."""
        Tagger.write_tags(self.mp3_path, {'title': 'Keep', 'artist': 'Gone'})
        Tagger.write_tags(self.mp3_path, {'artist': ''})
        tags = Tagger.read_tags(self.mp3_path)
        self.assertEqual(tags['title'], 'Keep')
        self.assertFalse(tags['artist'])

    def test_blank_value_removes_extended_tag(self):
        Tagger.write_tags(self.mp3_path, {'composer': 'Someone'})
        self.assertEqual(Tagger.read_tags(self.mp3_path)['composer'], 'Someone')
        Tagger.write_tags(self.mp3_path, {'composer': ''})
        self.assertFalse(Tagger.read_tags(self.mp3_path)['composer'])

    def test_returns_true_on_success(self):
        result = Tagger.write_tags(self.mp3_path, {
            'title': 'Test Song', 'artist': 'Test Artist',
        })
        self.assertTrue(result)
        tags = Tagger.read_tags(self.mp3_path)
        self.assertEqual(tags['title'], 'Test Song')
        self.assertEqual(tags['artist'], 'Test Artist')

    def test_roundtrip_all_fields(self):
        Tagger.write_tags(self.mp3_path, {
            'title': 'Round Trip', 'artist': 'An Artist',
            'album': 'An Album', 'genre': 'Rock',
            'year': '2024', 'tracknumber': '3',
        })
        tags = Tagger.read_tags(self.mp3_path)
        self.assertEqual(tags['title'], 'Round Trip')
        self.assertEqual(tags['artist'], 'An Artist')
        self.assertEqual(tags['album'], 'An Album')
        self.assertEqual(tags['genre'], 'Rock')
        self.assertEqual(tags['year'], '2024')
        self.assertEqual(tags['tracknumber'], '3')

    def test_unknown_extension_returns_false(self):
        path = os.path.join(self.tmpdir, 'test.txt')
        with open(path, 'wb') as f:
            f.write(b'hello')
        result = Tagger.write_tags(path, {'title': 'Test'})
        self.assertFalse(result)

    def test_handles_flac_gracefully(self):
        path = os.path.join(self.tmpdir, 'test.flac')
        with open(path, 'wb') as f:
            f.write(b'fLaC\x00')
        result = Tagger.write_tags(path, {'title': 'Song'})
        self.assertIsInstance(result, bool)

    def test_handles_m4a_gracefully(self):
        path = os.path.join(self.tmpdir, 'test.m4a')
        with open(path, 'wb') as f:
            f.write(b'\x00' * 20)
        result = Tagger.write_tags(path, {'title': 'Song'})
        self.assertIsInstance(result, bool)

    def test_handles_ogg_gracefully(self):
        path = os.path.join(self.tmpdir, 'test.ogg')
        with open(path, 'wb') as f:
            f.write(b'OggS' + os.urandom(100))
        result = Tagger.write_tags(path, {'title': 'Song'})
        self.assertIsInstance(result, bool)


class TestRename(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.mp3_path = os.path.join(self.tmpdir, 'test.mp3')
        with open(self.mp3_path, 'wb') as f:
            f.write(b'\x00')
        from mutagen.easyid3 import EasyID3
        audio = EasyID3()
        audio.save(self.mp3_path)

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    def test_rename_returns_true(self):
        Tagger.write_tags(self.mp3_path, {
            'title': 'Wonder', 'artist': 'Natalie Merchant',
        })
        result = Tagger.rename_to_artist_title(self.mp3_path)
        self.assertTrue(result)
        expected = os.path.join(self.tmpdir, 'Natalie Merchant - Wonder.mp3')
        self.assertTrue(os.path.exists(expected))
        self.assertFalse(os.path.exists(self.mp3_path))

    def test_missing_tags_returns_false(self):
        self.assertFalse(Tagger.rename_to_artist_title(self.mp3_path))

    def test_target_exists_returns_false(self):
        Tagger.write_tags(self.mp3_path, {
            'title': 'Song', 'artist': 'Artist',
        })
        target = os.path.join(self.tmpdir, 'Artist - Song.mp3')
        with open(target, 'wb') as f:
            f.write(b'\x00')
        from mutagen.easyid3 import EasyID3
        audio = EasyID3()
        audio.save(target)
        self.assertFalse(Tagger.rename_to_artist_title(self.mp3_path))


if __name__ == '__main__':
    unittest.main()
