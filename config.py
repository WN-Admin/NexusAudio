"""NexusAudio configuration — JSON-backed, env-overridable, typed accessors."""

from __future__ import annotations

import json
import logging
import os
import sys
import tempfile
from types import ModuleType

__all__ = [
    'Config',
    'config',
    'save',
    'DOWNLOAD_DIR',
    'COOKIES_BROWSER',
    'THEME',
    'P2P_USER',
    'P2P_PASS',
    'P2P_SERVER',
    'P2P_PORT',
    'CLOSE_ACTION',
    'EMBED_METADATA',
    'EMBED_LYRICS',
    'get_spotify_creds',
]

logger = logging.getLogger(__name__)

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
_CONFIG_DIR = os.path.join(os.path.expanduser('~'), '.config', 'nexusaudio')
_CONFIG_PATH = os.path.join(_CONFIG_DIR, 'config.json')

_DEFAULTS = {
    'download_dir': os.path.join(_BASE_DIR, 'downloads'),
    'cookies_browser': 'firefox',
    'theme': 'Nexus Dark',
    'spotify_client_id': '',
    'spotify_client_secret': '',
    'p2p_username': '',
    'p2p_password': '',
    'p2p_server': 'server.slsknet.org',
    'p2p_port': 2242,
    'close_action': 'ask',
    'embed_metadata': True,
    'embed_lyrics': False,
}

# Mapping from module-level name → Config property
_PROXY_MAP = {
    'DOWNLOAD_DIR': 'download_dir',
    'COOKIES_BROWSER': 'cookies_browser',
    'THEME': 'theme',
    'P2P_USER': 'p2p_username',
    'P2P_PASS': 'p2p_password',
    'P2P_SERVER': 'p2p_server',
    'P2P_PORT': 'p2p_port',
    'CLOSE_ACTION': 'close_action',
    'EMBED_METADATA': 'embed_metadata',
    'EMBED_LYRICS': 'embed_lyrics',
}


class Config:
    """NexusAudio configuration backed by ~/.config/nexusaudio/config.json."""

    def __init__(self) -> None:
        self._data: dict = {}
        self._load()

    # ------------------------------------------------------------------
    # persistence
    # ------------------------------------------------------------------

    def _load(self) -> None:
        self._data = dict(_DEFAULTS)
        if not os.path.isfile(_CONFIG_PATH):
            return
        try:
            with open(_CONFIG_PATH, 'r') as f:
                raw = json.load(f)
            if not isinstance(raw, dict):
                raise TypeError('config.json root must be a JSON object')
            self._data.update(raw)
            for k, v in _DEFAULTS.items():
                self._data.setdefault(k, v)
        except Exception as exc:
            logger.warning('config.json corrupted — using defaults: %s', exc)
            self._data = dict(_DEFAULTS)

    def save(self) -> None:
        """Atomically write current configuration to disk.

        Written via a temp file + ``os.replace`` so an interrupted write can
        never truncate or corrupt an existing config, and with owner-only
        permissions because the file can contain a Soulseek password.
        """
        os.makedirs(_CONFIG_DIR, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(dir=_CONFIG_DIR, prefix='.config-', suffix='.json')
        try:
            with os.fdopen(fd, 'w') as f:
                json.dump(self._data, f, indent=2, ensure_ascii=False)
                f.flush()
                os.fsync(f.fileno())
            os.chmod(tmp_path, 0o600)
            os.replace(tmp_path, _CONFIG_PATH)
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

    # ------------------------------------------------------------------
    # typed properties
    # ------------------------------------------------------------------

    @property
    def download_dir(self) -> str:
        return self._data['download_dir']

    @download_dir.setter
    def download_dir(self, value: str) -> None:
        self._data['download_dir'] = value

    @property
    def cookies_browser(self) -> str:
        return self._data['cookies_browser']

    @cookies_browser.setter
    def cookies_browser(self, value: str) -> None:
        self._data['cookies_browser'] = value

    @property
    def theme(self) -> str:
        return self._data['theme']

    @theme.setter
    def theme(self, value: str) -> None:
        self._data['theme'] = value

    @property
    def spotify_client_id(self) -> str:
        return os.environ.get('SPOTIFY_CLIENT_ID') or self._data.get('spotify_client_id', '')

    @spotify_client_id.setter
    def spotify_client_id(self, value: str) -> None:
        self._data['spotify_client_id'] = value

    @property
    def spotify_client_secret(self) -> str:
        return os.environ.get('SPOTIFY_SECRET') or self._data.get('spotify_client_secret', '')

    @spotify_client_secret.setter
    def spotify_client_secret(self, value: str) -> None:
        self._data['spotify_client_secret'] = value

    @property
    def p2p_username(self) -> str:
        return self._data.get('p2p_username', '')

    @p2p_username.setter
    def p2p_username(self, value: str) -> None:
        self._data['p2p_username'] = value

    @property
    def p2p_password(self) -> str:
        return self._data.get('p2p_password', '')

    @p2p_password.setter
    def p2p_password(self, value: str) -> None:
        self._data['p2p_password'] = value

    @property
    def p2p_server(self) -> str:
        return self._data.get('p2p_server', 'server.slsknet.org')

    @p2p_server.setter
    def p2p_server(self, value: str) -> None:
        self._data['p2p_server'] = value

    @property
    def p2p_port(self) -> int:
        return self._data.get('p2p_port', 2242)

    @p2p_port.setter
    def p2p_port(self, value: int) -> None:
        self._data['p2p_port'] = value

    @property
    def close_action(self) -> str:
        """Window close behavior: 'ask', 'minimize', or 'quit'."""
        return self._data.get('close_action', 'ask')

    @close_action.setter
    def close_action(self, value: str) -> None:
        if value not in ('ask', 'minimize', 'quit'):
            value = 'ask'
        self._data['close_action'] = value

    @property
    def embed_metadata(self) -> bool:
        return bool(self._data.get('embed_metadata', True))

    @embed_metadata.setter
    def embed_metadata(self, value) -> None:
        self._data['embed_metadata'] = bool(value)

    @property
    def embed_lyrics(self) -> bool:
        return bool(self._data.get('embed_lyrics', False))

    @embed_lyrics.setter
    def embed_lyrics(self, value) -> None:
        self._data['embed_lyrics'] = bool(value)


# ------------------------------------------------------------------
# module-level helpers
# ------------------------------------------------------------------

def get_spotify_creds() -> tuple[str, str]:
    """Return (client_id, client_secret) with env override precedence."""
    return _config.spotify_client_id, _config.spotify_client_secret


def save() -> None:
    """Convenience wrapper — delegates to ``Config.save()``."""
    _config.save()


# ------------------------------------------------------------------
# singleton
# ------------------------------------------------------------------

_config = Config()

# ------------------------------------------------------------------
# module-as-proxy trick so ``import config; config.DOWNLOAD_DIR``
# and ``config.DOWNLOAD_DIR = '...'`` delegate to the Config singleton.
# Only names listed in _PROXY_MAP are intercepted.
# ------------------------------------------------------------------

class _ConfigProxy(ModuleType):
    _PROXY_MAP = _PROXY_MAP

    def __getattribute__(self, name: str):
        mapping = type(self)._PROXY_MAP
        if name in mapping:
            return getattr(_config, mapping[name])
        return super().__getattribute__(name)

    def __setattr__(self, name: str, value):
        mapping = type(self)._PROXY_MAP
        if name in mapping:
            return setattr(_config, mapping[name], value)
        super().__setattr__(name, value)
sys.modules[__name__].__class__ = _ConfigProxy
