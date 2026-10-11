# NexusAudio Project Conventions

## Python Conventions
- **Format**: Python 3.10+, PyQt6 GUI framework
- **Audio tagging**: mutagen library (EasyID3/FLAC/MP4/OggVorbis)
- **Download engine**: yt-dlp with FFmpeg post-processing
- **API**: spotipy for Spotify, requests for lyrics.ovh

## Thread Safety
- All GUI updates must go through PyQt6 signals (`pyqtSignal`)
- Use `QThread` subclass for long-running operations (see `DownloadThread`)
- Never call `setText()`, `addItem()`, etc. from non-main threads
- Use signal `emit()` to marshal results to main thread

## Download Sources
- Tracklists are resolved by `core/platforms.py`; each service has a `PlatformResolver` subclass
- `PLATFORMS` is the registry — add a new service by appending a resolver, and it appears in the GUI Source dropdown automatically
- Resolvers only produce `{name, artist, album}`; audio always comes from the yt-dlp pipeline in `core/downloader.py`
- Resolvers with `downloadable = True` (YouTube, SoundCloud) attach `source_url`, which `download_track` tries before falling back to a YouTube search
- Deezer and Apple Music (iTunes Lookup) need no credentials; Spotify uses spotipy when creds exist and otherwise page-scrapes
- `resolve()` raises `ValueError` for bad input and `RuntimeError` for upstream failures; `Downloader.get_tracks_from_url` catches both and returns `[]`

## Soulseek P2P
- `core/nicotine_integration.py` implements the wire protocol directly; the authoritative reference is the Nicotine+ protocol doc (nicotine-plus.org/doc/SLSKPROTOCOL.html), with message layouts cross-checked against Soulseek.NET
- Framing differs by connection type and is easy to get wrong:
  - Server and peer messages: `uint32 length, uint32 code, payload`
  - Peer **init** messages: `uint32 length (code + payload), uint8 code, payload`
  - File ('F') messages: raw, no code or length
- Peer message codes: 9 FileSearchResponse, 40 TransferRequest, 41 TransferResponse, 43 QueueUpload, 44 PlaceInQueueResponse, 50 UploadDenied
- `FileSearchResponse` (code 9) payloads are **zlib compressed**, begin with a length-prefixed username, and each file entry is preceded by a uint8 code byte
- Download flow: ConnectToPeer(18) -> GetPeerAddress(3) -> 'P' connection + PeerInit -> QueueUpload(43) -> TransferRequest(40) -> TransferResponse(41) -> uploader opens 'F' connection -> FileTransferInit -> we send FileOffset(0) -> raw bytes
- PeerInit is exchanged **simultaneously**: send ours before reading theirs, or a send-first peer deadlocks
- Transfer tokens are monotonic, not `int(time.time())` — same-second requests would collide

## Download Queue
- `Downloader._drain_queue` must never let an exception escape; `process_queue` wraps it in `try/finally` so `_queue_running` is always reset
- A wedged `_queue_running=True` silently blocks every later download for the whole session — always reset it in `finally`
- `download_track` is a `(bool, str)` boundary: it converts any escaping exception into a reported failure rather than raising into the queue
- The queue is additive: `process_queue()` on a running queue appends and returns, it does not drop items

## Tag Handling
- Internal key `'year'` maps to format-specific keys (MP3 `date`, FLAC `date`, M4A `\xa9day`)
- Internal key `'tracknumber'` maps to format-specific keys
- OGG uses the same tag keys as FLAC (Vorbis Comments format)

## Error Handling
- Never use bare `except:` — always specify exception type
- Use `logging.getLogger(__name__)` for module-level logging
- Use `QMessageBox` for user-facing errors

## Settings Persistence
- Config saved to `config.py` by rewriting the file
- Env vars override config values at import time
- Spotify credentials embedded with env-based override

## Git Workflow
- Single `main`/`master` branch
- Commit messages: prefix with `fix:`, `feat:`, `chore:`, `refactor:`
