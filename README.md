# NexusAudio - Downloader, Tag Editor & P2P

A fully integrated PyQt6-based GUI application that combines Spotify playlist downloading, spreadsheet-style audio tag editing, and Soulseek P2P file sharing.

**No external programs needed** — everything runs in a single Python application.

## Quick Start

### Linux / macOS

```bash
pip install .            # one-time install
nexusaudio               # launch (works from any directory)
```

Or without pip install (auto-venv):

```bash
./run.sh
```

### Windows

```bash
run.bat                  # auto-creates venv, installs, launches
```

Or for a permanent install:

```bash
pip install .
nexusaudio
```

### Docker

```bash
./run.sh --docker        # auto-builds + launches in container
```

Or step-by-step:

```bash
docker compose build
docker compose up
```

## Features

### Downloader Tab
- Download tracks and playlists from **Spotify, Apple Music, Deezer, YouTube /
  YouTube Music, and SoundCloud** — paste any supported URL and the platform is
  auto-detected, or force one with the Source dropdown
- Deezer, Apple Music and YouTube links work with **no API keys**
- Apple Music supports track and album links; playlists need a developer token
- YouTube and SoundCloud links download audio straight from the source
- Supports MP3 (64-320 kbps), FLAC, M4A, OGG
- Automatic age-restricted content handling via browser cookies
- Progress bar, queue, cancellation
- Press Enter in the URL field to fetch; click any track row to select it
- Multiple searches in a row are safe — the queue recovers from failures
- Auto-embed ID3/Vorbis/MP4 tags (title, artist, album)
- Auto-fetch and embed lyrics (lyrics.ovh + Genius fallback)

### Tag Editor Tab
- Spreadsheet-like interface for editing audio tags
- Supports: MP3 (ID3v2), FLAC (Vorbis Comments), M4A (MP4 tags), OGG
- Bulk rename to "Artist - Title" format
- MusicBrainz lookup: search by artist/album, apply matched track titles/numbers
  to selected files, and download + embed cover art (all 4 formats)

### P2P Tab (Soulseek)
- Full SLSK wire protocol over TCP sockets (peer-init, search, transfer)
- Search peers, transfer files, queue position reporting
- Indirect (NAT traversal) support via ConnectToPeer / PierceFireWall
- Reconnection with exponential backoff

### Settings Tab
- Download folder, cookies browser, audio format/quality
- Soulseek credentials, theme (5 themes), metadata/lyrics toggles

## Spotify API (Optional)

The web scraper works without any credentials, but for faster large-playlist resolution
you can provide API keys:

```bash
pip install .[spotify]   # install with spotipy
export SPOTIFY_CLIENT_ID=your_id
export SPOTIFY_SECRET=your_secret
```

## Requirements

- **Python 3.10+**
- **FFmpeg** — required by yt-dlp for audio conversion
  - Linux: `sudo apt install ffmpeg`
  - macOS: `brew install ffmpeg`
  - Windows: `winget install ffmpeg` or download from ffmpeg.org
- **PyQt6** — installed automatically via pip

## Project Structure

```
NexusAudio/
  main.py            # Application entry point
  config.py          # Configuration with JSON persistence
  setup.py           # Pip install support
  pyproject.toml     # Build metadata
  run.sh             # Linux/macOS launcher (auto-venv)
  run.bat            # Windows launcher (auto-venv)
  docker-compose.yml # Docker launcher
  Dockerfile         # Container image
  requirements.txt   # Legacy dependency list
  gui/main_window.py # Main GUI (4 tabs)
  core/
    downloader.py    # Download queue + yt-dlp audio pipeline
    platforms.py     # Tracklist resolvers (Spotify, Apple, Deezer, YT, SoundCloud)
    tagger.py        # Audio tag R/W (MP3, FLAC, M4A)
    metadata.py      # Metadata extraction + lyrics
    themes.py        # 5 color schemes
    nicotine_integration.py  # Soulseek P2P protocol
  downloads/         # Default download directory
```
