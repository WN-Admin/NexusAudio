import os
import sys
import json
import unittest
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import config as cfg_mod

_BASE_DATA = dict(cfg_mod._config._data)
_BASE_CONFIG_DIR = cfg_mod._CONFIG_DIR


class TestConfig(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.config_dir = os.path.join(self.tmpdir, '.config', 'nexusaudio')
        self.config_path = os.path.join(self.config_dir, 'config.json')
        cfg_mod._CONFIG_DIR = self.config_dir
        cfg_mod._CONFIG_PATH = self.config_path
        cfg_mod._config._load()

    def tearDown(self):
        cfg_mod._config._data = dict(_BASE_DATA)
        cfg_mod._CONFIG_DIR = _BASE_CONFIG_DIR
        cfg_mod._CONFIG_PATH = os.path.join(_BASE_CONFIG_DIR, 'config.json')
        if os.path.isfile(self.config_path):
            try:
                os.remove(self.config_path)
            except OSError:
                pass

    def test_config_loads_defaults_on_fresh_start(self):
        cfg_mod._config._load()
        self.assertTrue(hasattr(cfg_mod, 'DOWNLOAD_DIR'))
        self.assertTrue(hasattr(cfg_mod, 'THEME'))
        self.assertTrue(hasattr(cfg_mod, 'COOKIES_BROWSER'))
        self.assertIn('downloads', cfg_mod.DOWNLOAD_DIR)
        self.assertEqual(cfg_mod.THEME, 'Nexus Dark')
        self.assertEqual(cfg_mod.COOKIES_BROWSER, 'firefox')

    def test_config_proxy_read_write_roundtrip(self):
        cfg_mod.THEME = 'Ocean'
        self.assertEqual(cfg_mod.THEME, 'Ocean')
        cfg_mod.COOKIES_BROWSER = 'chrome'
        self.assertEqual(cfg_mod.COOKIES_BROWSER, 'chrome')
        cfg_mod.P2P_USER = 'test_user'
        self.assertEqual(cfg_mod.P2P_USER, 'test_user')

    def test_config_save_and_reload(self):
        original = cfg_mod.DOWNLOAD_DIR
        new_dir = '/tmp/nexus_test_music'
        cfg_mod.DOWNLOAD_DIR = new_dir
        cfg_mod.save()
        cfg_mod.DOWNLOAD_DIR = original
        self.assertEqual(cfg_mod.DOWNLOAD_DIR, original)
        cfg_mod._config._load()
        self.assertEqual(cfg_mod.DOWNLOAD_DIR, new_dir)

    def test_config_defaults_persist_in_json(self):
        cfg_mod.save()
        self.assertTrue(os.path.isfile(self.config_path))
        with open(self.config_path) as f:
            data = json.load(f)
        self.assertIn('download_dir', data)
        self.assertIn('theme', data)
        self.assertEqual(data['theme'], 'Nexus Dark')
        self.assertEqual(data['cookies_browser'], 'firefox')
        self.assertEqual(data['p2p_port'], 2242)

    def test_config_unknown_proxy_returns_normal_attribute(self):
        self.assertTrue(hasattr(cfg_mod, 'save'))
        self.assertTrue(hasattr(cfg_mod, 'get_spotify_creds'))

    def test_get_spotify_creds_returns_empty_defaults(self):
        cid, csec = cfg_mod.get_spotify_creds()
        self.assertEqual(cid, '')
        self.assertEqual(csec, '')


if __name__ == '__main__':
    unittest.main()
