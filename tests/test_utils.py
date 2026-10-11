import os
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.utils import sanitize_filename, safe_join
from core.tagger import Tagger


class TestSanitizeFilename(unittest.TestCase):
    def test_strips_path_separators(self):
        self.assertEqual(sanitize_filename('AC/DC'), 'ACDC')
        self.assertEqual(sanitize_filename('..\\..\\evil'), 'evil')

    def test_strips_reserved_chars(self):
        self.assertEqual(sanitize_filename('a:b*c?d"e<f>g|h'), 'abcdefgh')

    def test_preserves_international_text(self):
        self.assertEqual(sanitize_filename('Beyoncé — Halo'), 'Beyoncé — Halo')
        self.assertEqual(sanitize_filename('宇多田ヒカル'), '宇多田ヒカル')

    def test_empty_and_dotdot_become_unknown(self):
        self.assertEqual(sanitize_filename(''), 'Unknown')
        self.assertEqual(sanitize_filename('   '), 'Unknown')
        self.assertEqual(sanitize_filename('..'), 'Unknown')

    def test_truncates_long_names(self):
        self.assertEqual(len(sanitize_filename('x' * 500)), 200)


class TestSafeJoin(unittest.TestCase):
    def test_allows_normal_child(self):
        self.assertEqual(safe_join('/tmp/music', 'a.mp3'), '/tmp/music/a.mp3')

    def test_blocks_traversal(self):
        self.assertIsNone(safe_join('/tmp/music', '../etc/passwd'))
        self.assertIsNone(safe_join('/tmp/music', '../../etc/passwd'))

    def test_blocks_absolute_escape(self):
        self.assertIsNone(safe_join('/tmp/music', '/etc/passwd'))

    def test_sibling_prefix_is_not_inside(self):
        # /tmp/music2 must not count as being inside /tmp/music
        self.assertIsNone(safe_join('/tmp/music', '../music2/x.mp3'))


class TestRenameCannotEscapeDirectory(unittest.TestCase):
    """Tag values are attacker-controllable metadata used to build a path."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.library = os.path.join(self.tmp.name, 'library')
        self.outside = os.path.join(self.tmp.name, 'outside')
        os.makedirs(self.library)
        os.makedirs(self.outside)

    def tearDown(self):
        self.tmp.cleanup()

    def _make_file(self, name):
        path = os.path.join(self.library, name)
        with open(path, 'wb') as fh:
            fh.write(b'\x00')
        return path

    def test_traversal_tag_cannot_write_outside_library(self):
        source = self._make_file('track.mp3')
        with patch.object(
                Tagger, 'read_tags',
                return_value={'artist': '../../../outside', 'title': 'pwned'}):
            Tagger.rename_to_artist_title(source)
        escaped = os.path.join(self.outside, 'pwned.mp3')
        self.assertFalse(os.path.exists(escaped))
        # Whatever happened, every file is still inside the library.
        for name in os.listdir(self.outside):
            self.assertNotIn('pwned', name)
        for name in os.listdir(self.library):
            self.assertNotIn('..', name)

    def test_normal_tag_renames_successfully(self):
        source = self._make_file('track.mp3')
        with patch.object(
                Tagger, 'read_tags',
                return_value={'artist': 'Artist', 'title': 'Song'}):
            self.assertTrue(Tagger.rename_to_artist_title(source))
        self.assertTrue(os.path.exists(
            os.path.join(self.library, 'Artist - Song.mp3')))

    def test_slash_in_tag_stays_in_directory(self):
        source = self._make_file('track.mp3')
        with patch.object(
                Tagger, 'read_tags',
                return_value={'artist': 'AC/DC', 'title': 'Thunder'}):
            Tagger.rename_to_artist_title(source)
        for name in os.listdir(self.library):
            self.assertNotIn('/', name)
            self.assertNotIn('..', name)


if __name__ == '__main__':
    unittest.main()