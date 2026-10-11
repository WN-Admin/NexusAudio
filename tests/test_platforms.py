import os
import sys
import unittest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from core.platforms import (
    PLATFORMS_BY_SLUG,
    PLATFORM_LABELS,
    AppleMusicResolver,
    DeezerResolver,
    SoundCloudResolver,
    YouTubeResolver,
    clean_title,
    detect,
    resolve,
    split_artist_title,
)


class TestTitleCleaning(unittest.TestCase):
    def test_strips_official_video_noise(self):
        self.assertEqual(
            clean_title("APT. (Official Music Video)"),
            "APT.",
        )

    def test_strips_bracketed_noise(self):
        self.assertEqual(
            clean_title("Song Name [Official Lyric Video]"),
            "Song Name",
        )

    def test_preserves_plain_title(self):
        self.assertEqual(clean_title("One More Time"), "One More Time")

    def test_handles_empty(self):
        self.assertEqual(clean_title(""), "")
        self.assertEqual(clean_title(None), "")

    def test_split_artist_title(self):
        self.assertEqual(
            split_artist_title("Daft Punk - One More Time"),
            ("Daft Punk", "One More Time"),
        )

    def test_split_without_separator(self):
        artist, title = split_artist_title("One More Time")
        self.assertEqual(artist, "")
        self.assertEqual(title, "One More Time")

    def test_split_ignores_noise(self):
        artist, title = split_artist_title("Artist - Song (Official Video)")
        self.assertEqual(artist, "Artist")
        self.assertEqual(title, "Song")


class TestDetection(unittest.TestCase):
    def test_detects_spotify(self):
        resolver = detect("https://open.spotify.com/track/abc123")
        self.assertIs(resolver, PLATFORMS_BY_SLUG['spotify'])

    def test_detects_apple_music(self):
        resolver = detect("https://music.apple.com/us/album/foo/1440830670")
        self.assertIs(resolver, PLATFORMS_BY_SLUG['apple'])

    def test_detects_deezer(self):
        resolver = detect("https://www.deezer.com/playlist/908622995")
        self.assertIs(resolver, PLATFORMS_BY_SLUG['deezer'])

    def test_detects_youtube(self):
        resolver = detect("https://www.youtube.com/watch?v=ekr2nIex040")
        self.assertIs(resolver, PLATFORMS_BY_SLUG['youtube'])

    def test_detects_youtube_music(self):
        resolver = detect("https://music.youtube.com/playlist?list=PLabc")
        self.assertIs(resolver, PLATFORMS_BY_SLUG['youtube'])

    def test_detects_youtu_be(self):
        resolver = detect("https://youtu.be/ekr2nIex040")
        self.assertIs(resolver, PLATFORMS_BY_SLUG['youtube'])

    def test_detects_soundcloud(self):
        resolver = detect("https://soundcloud.com/monstercat/sets/vol-5")
        self.assertIs(resolver, PLATFORMS_BY_SLUG['soundcloud'])

    def test_returns_none_for_unknown(self):
        self.assertIsNone(detect("https://example.com/playlist/1"))
        self.assertIsNone(detect(""))

    def test_all_resolvers_have_labels(self):
        for slug, label in PLATFORM_LABELS.items():
            self.assertTrue(label, f"{slug} has no label")


class TestDeezerResolver(unittest.TestCase):
    def setUp(self):
        self.resolver = DeezerResolver()

    @patch('core.platforms.PlatformResolver._get_json')
    def test_resolves_playlist(self, mock_get):
        mock_get.return_value = {
            'title': 'Hits',
            'tracks': {'data': [
                {'title': 'One More Time', 'artist': {'name': 'Daft Punk'},
                 'album': {'title': 'Discovery'}, 'duration': 320, 'isrc': 'FR123'},
                {'title': 'Aerodynamic', 'artist': {'name': 'Daft Punk'},
                 'album': {'title': 'Discovery'}, 'duration': 212},
            ]},
        }
        tracks = self.resolver.resolve('https://www.deezer.com/playlist/908622995')
        self.assertEqual(len(tracks), 2)
        self.assertEqual(tracks[0]['name'], 'One More Time')
        self.assertEqual(tracks[0]['artist'], 'Daft Punk')
        self.assertEqual(tracks[0]['album'], 'Discovery')
        self.assertEqual(tracks[0]['platform'], 'deezer')
        self.assertEqual(tracks[0]['isrc'], 'FR123')

    @patch('core.platforms.PlatformResolver._get_json')
    def test_resolves_album(self, mock_get):
        mock_get.return_value = {
            'title': 'Discovery',
            'tracks': {'data': [
                {'title': 'Digital Love', 'artist': {'name': 'Daft Punk'},
                 'album': {'title': 'Discovery'}},
            ]},
        }
        tracks = self.resolver.resolve('https://www.deezer.com/album/302127')
        self.assertEqual(len(tracks), 1)
        self.assertEqual(tracks[0]['name'], 'Digital Love')

    @patch('core.platforms.PlatformResolver._get_json')
    def test_resolves_single_track(self, mock_get):
        mock_get.return_value = {
            'title': 'Harder Better', 'artist': {'name': 'Daft Punk'},
            'album': {'title': 'Discovery'},
        }
        tracks = self.resolver.resolve('https://www.deezer.com/track/302127')
        self.assertEqual(len(tracks), 1)

    @patch('core.platforms.PlatformResolver._get_json')
    def test_skips_entries_without_title(self, mock_get):
        mock_get.return_value = {
            'tracks': {'data': [{'title': '', 'artist': {'name': 'X'}}]},
        }
        self.assertEqual(self.resolver.resolve('https://www.deezer.com/album/1'), [])

    @patch('core.platforms.PlatformResolver._get_json')
    def test_falls_back_to_contributors(self, mock_get):
        mock_get.return_value = {
            'title': 'Song', 'artist': {}, 'contributors': [{'name': 'Real Artist'}],
            'album': {'title': 'Alb'},
        }
        tracks = self.resolver.resolve('https://www.deezer.com/track/1')
        self.assertEqual(tracks[0]['artist'], 'Real Artist')

    def test_honours_cancel(self):
        with patch('core.platforms.PlatformResolver._get_json') as mock_get:
            mock_get.return_value = {
                'tracks': {'data': [
                    {'title': 'A', 'artist': {'name': 'X'}, 'album': {'title': 'Y'}},
                    {'title': 'B', 'artist': {'name': 'X'}, 'album': {'title': 'Y'}},
                ]},
            }
            tracks = self.resolver.resolve(
                'https://www.deezer.com/album/1', cancel=lambda: True)
        self.assertEqual(tracks, [])


class TestAppleMusicResolver(unittest.TestCase):
    def setUp(self):
        self.resolver = AppleMusicResolver()

    @patch('core.platforms.PlatformResolver._get_json')
    def test_resolves_album(self, mock_get):
        mock_get.return_value = {
            'resultCount': 3,
            'results': [
                {'wrapperType': 'collection', 'collectionName': 'Christmas Collection'},
                {'wrapperType': 'track', 'trackName': 'Silent Night',
                 'artistName': 'Frank Sinatra', 'trackTimeMillis': 180000},
                {'wrapperType': 'track', 'trackName': 'White Christmas',
                 'artistName': 'Frank Sinatra', 'trackTimeMillis': 150000},
            ],
        }
        tracks = self.resolver.resolve(
            'https://music.apple.com/us/album/x/1440830670')
        self.assertEqual(len(tracks), 2)
        self.assertEqual(tracks[0]['name'], 'Silent Night')
        self.assertEqual(tracks[0]['album'], 'Christmas Collection')
        self.assertEqual(tracks[0]['duration'], 180)
        self.assertEqual(tracks[0]['platform'], 'apple')

    @patch('core.platforms.PlatformResolver._get_json')
    def test_resolves_song_via_i_param(self, mock_get):
        mock_get.return_value = {
            'resultCount': 1,
            'results': [{'wrapperType': 'track', 'trackName': 'Get Lucky',
                         'artistName': 'Daft Punk', 'collectionName': 'RAM'}],
        }
        tracks = self.resolver.resolve(
            'https://music.apple.com/us/album/x/1440830670?i=1440831065')
        self.assertEqual(len(tracks), 1)
        self.assertEqual(tracks[0]['name'], 'Get Lucky')

    def test_playlist_raises_clear_error(self):
        with self.assertRaises(RuntimeError) as ctx:
            self.resolver.resolve('https://music.apple.com/us/playlist/x/pl.123')
        self.assertIn('developer token', str(ctx.exception))

    @patch('core.platforms.PlatformResolver._get_json')
    def test_no_results_raises(self, mock_get):
        mock_get.return_value = {'resultCount': 0, 'results': []}
        with self.assertRaises(RuntimeError):
            self.resolver.resolve('https://music.apple.com/us/album/x/999')

    @patch('core.platforms.PlatformResolver._get_json')
    def test_search(self, mock_get):
        mock_get.return_value = {
            'resultCount': 1,
            'results': [{'wrapperType': 'track', 'trackName': 'One More Time',
                         'artistName': 'Daft Punk', 'collectionName': 'Discovery'}],
        }
        tracks = self.resolver.search('daft punk')
        self.assertEqual(len(tracks), 1)
        self.assertEqual(tracks[0]['artist'], 'Daft Punk')


class TestYouTubeResolver(unittest.TestCase):
    def setUp(self):
        self.resolver = YouTubeResolver()

    def _mock_ydl(self, mock_youtube_dl, info):
        ydl = MagicMock()
        ydl.__enter__.return_value.extract_info.return_value = info
        mock_youtube_dl.return_value = ydl

    @patch('yt_dlp.YoutubeDL')
    def test_resolves_playlist_with_source_urls(self, mock_ydl):
        self._mock_ydl(mock_ydl, {
            '_type': 'playlist',
            'title': 'Pop Hits',
            'entries': [
                {'title': 'ROSÉ & Bruno Mars - APT. (Official Music Video)',
                 'uploader': 'ROSÉ', 'id': 'ekr2nIex040', 'duration': 175},
                {'title': 'Lady Gaga - Die With A Smile',
                 'uploader': 'Lady Gaga', 'id': 'kPa7bsKwL-c', 'duration': 200},
            ],
        })
        tracks = self.resolver.resolve('https://www.youtube.com/playlist?list=PLabc')
        self.assertEqual(len(tracks), 2)
        self.assertEqual(tracks[0]['name'], 'APT.')
        self.assertEqual(tracks[0]['artist'], 'ROSÉ & Bruno Mars')
        self.assertEqual(tracks[0]['source_url'],
                         'https://www.youtube.com/watch?v=ekr2nIex040')
        self.assertEqual(tracks[0]['album'], 'Pop Hits')
        self.assertEqual(tracks[0]['platform'], 'youtube')

    @patch('yt_dlp.YoutubeDL')
    def test_resolves_single_video(self, mock_ydl):
        self._mock_ydl(mock_ydl, {
            '_type': 'video',
            'title': 'Artist - Song Name',
            'uploader': 'Artist',
            'id': 'abc123',
        })
        tracks = self.resolver.resolve('https://youtu.be/abc123')
        self.assertEqual(len(tracks), 1)
        self.assertEqual(tracks[0]['name'], 'Song Name')
        self.assertEqual(tracks[0]['source_url'],
                         'https://www.youtube.com/watch?v=abc123')

    @patch('yt_dlp.YoutubeDL')
    def test_falls_back_to_uploader_when_no_separator(self, mock_ydl):
        self._mock_ydl(mock_ydl, {
            '_type': 'video',
            'title': 'Just A Title',
            'uploader': 'Channel Name',
            'id': 'abc123',
        })
        tracks = self.resolver.resolve('https://youtu.be/abc123')
        self.assertEqual(tracks[0]['name'], 'Just A Title')
        self.assertEqual(tracks[0]['artist'], 'Channel Name')

    @patch('yt_dlp.YoutubeDL')
    def test_raises_on_empty_result(self, mock_ydl):
        self._mock_ydl(mock_ydl, None)
        with self.assertRaises(RuntimeError):
            self.resolver.resolve('https://youtu.be/abc123')


class TestSoundCloudResolver(unittest.TestCase):
    def setUp(self):
        self.resolver = SoundCloudResolver()

    @patch('yt_dlp.YoutubeDL')
    def test_resolves_set(self, mock_ydl):
        ydl = MagicMock()
        ydl.__enter__.return_value.extract_info.return_value = {
            '_type': 'playlist',
            'title': 'Monstercat Vol 5',
            'entries': [
                {'title': 'Knife Party - Harpoon', 'uploader': 'Knife Party',
                 'webpage_url': 'https://soundcloud.com/kp/harpoon', 'duration': 352},
            ],
        }
        mock_ydl.return_value = ydl
        tracks = self.resolver.resolve(
            'https://soundcloud.com/monstercat/sets/vol-5')
        self.assertEqual(len(tracks), 1)
        self.assertEqual(tracks[0]['name'], 'Harpoon')
        self.assertEqual(tracks[0]['artist'], 'Knife Party')
        self.assertEqual(tracks[0]['album'], 'Monstercat Vol 5')
        self.assertEqual(tracks[0]['source_url'],
                         'https://soundcloud.com/kp/harpoon')

    @patch('yt_dlp.YoutubeDL')
    def test_uses_flat_for_single_track(self, mock_ydl):
        ydl = MagicMock()
        ydl.__enter__.return_value.extract_info.return_value = {
            '_type': 'track', 'title': 'Artist - Song', 'uploader': 'Artist',
            'webpage_url': 'https://soundcloud.com/a/s',
        }
        mock_ydl.return_value = ydl
        self.resolver.resolve('https://soundcloud.com/a/s')
        options = mock_ydl.call_args[0][0]
        self.assertTrue(options.get('extract_flat'))


class TestSpotifyResolver(unittest.TestCase):
    def test_api_path_returns_tracks(self):
        from core.platforms import SpotifyResolver
        client = MagicMock()
        client.track.return_value = {
            'name': 'Song', 'artists': [{'name': 'Artist'}],
            'album': {'name': 'Album'}, 'duration_ms': 200000,
        }
        resolver = SpotifyResolver(client)
        tracks = resolver.resolve('https://open.spotify.com/track/abc')
        self.assertEqual(len(tracks), 1)
        self.assertEqual(tracks[0]['name'], 'Song')
        self.assertEqual(tracks[0]['duration'], 200)

    def test_api_failure_falls_back_to_scrape(self):
        from core.platforms import SpotifyResolver
        client = MagicMock()
        client.track.side_effect = RuntimeError('bad token')
        resolver = SpotifyResolver(client)
        with patch.object(SpotifyResolver, '_scrape', return_value=[]) as scrape:
            with self.assertRaises(RuntimeError):
                resolver.resolve('https://open.spotify.com/track/abc')
        scrape.assert_called_once()

    def test_empty_result_raises(self):
        from core.platforms import SpotifyResolver
        resolver = SpotifyResolver(None)
        with patch.object(SpotifyResolver, '_scrape', return_value=[]):
            with self.assertRaises(RuntimeError):
                resolver.resolve('https://open.spotify.com/track/abc')

    def test_non_spotify_url_returns_empty(self):
        from core.platforms import SpotifyResolver
        resolver = SpotifyResolver(None)
        self.assertEqual(resolver.resolve('https://example.com/x'), [])


class TestResolveEntryPoint(unittest.TestCase):
    def test_empty_url_raises_value_error(self):
        with self.assertRaises(ValueError):
            resolve('')

    def test_unsupported_url_raises_value_error(self):
        with self.assertRaises(ValueError) as ctx:
            resolve('https://example.com/playlist/1')
        self.assertIn('Unsupported', str(ctx.exception))

    def test_plain_text_raises_value_error(self):
        with self.assertRaises(ValueError) as ctx:
            resolve('just some words')
        self.assertIn('Unrecognized', str(ctx.exception))

    def test_unknown_slug_raises(self):
        with self.assertRaises(ValueError):
            resolve('https://x.com', slug='tidal')

    def test_forced_slug_used_without_url_match(self):
        with patch.object(DeezerResolver, 'search', return_value=[
            {'name': 'S', 'artist': 'A', 'album': 'B', 'platform': 'deezer'}
        ]) as search:
            tracks = resolve('daft punk', slug='deezer')
        search.assert_called_once()
        self.assertEqual(len(tracks), 1)

    def test_auto_detect_routes_to_right_resolver(self):
        with patch.object(AppleMusicResolver, 'resolve', return_value=[]) as mock_resolve:
            resolve('https://music.apple.com/us/album/x/1')
        mock_resolve.assert_called_once()

    def test_cancel_callback_forwarded(self):
        with patch.object(YouTubeResolver, 'resolve', return_value=[]) as mock_resolve:
            resolve('https://youtu.be/abc', cancel=lambda: False)
        mock_resolve.assert_called_once()
        self.assertIsNotNone(mock_resolve.call_args.kwargs.get('cancel'))


if __name__ == '__main__':
    unittest.main()
