import os
import re
import sys
import subprocess
import threading
import logging
from PyQt6.QtWidgets import (QApplication, QMainWindow, QTabWidget, QWidget, QVBoxLayout, QHBoxLayout,
                             QPushButton, QLineEdit, QLabel, QProgressBar, QComboBox,
                             QTableWidget, QTableWidgetItem, QHeaderView, QFileDialog,
                             QMessageBox, QCheckBox, QTextEdit, QListWidget, QListWidgetItem,
                             QGroupBox, QMenu, QDialog, QDialogButtonBox, QSystemTrayIcon,
                             QSplitter, QScrollArea)
from PyQt6.QtCore import Qt, pyqtSignal, QTimer
from PyQt6.QtGui import QIcon, QKeySequence, QShortcut

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core.downloader import Downloader
from mutagen.flac import FLAC
from mutagen.oggvorbis import OggVorbis
from mutagen.mp4 import MP4
from core.tagger import Tagger, EXTENDED_KEYS
from core.metadata import fetch_lyrics, embed_lyrics
from core.musicbrainz import (
    search_release, get_release_details, fetch_cover_art,
    embed_cover_art, apply_release_to_files
)
from core.themes import get_stylesheet, get_theme_names
from core.platforms import PLATFORM_LABELS
import config

logger = logging.getLogger(__name__)


class MainWindow(QMainWindow):
    p2p_status_signal = pyqtSignal(str)
    p2p_search_signal = pyqtSignal(object)
    p2p_download_signal = pyqtSignal(str, str, int)
    p2p_transfer_signal = pyqtSignal(int, int, object, str)
    p2p_connected_signal = pyqtSignal(bool)
    p2p_error_signal = pyqtSignal(str)
    progress_signal = pyqtSignal(float)
    status_signal = pyqtSignal(str)
    queue_progress_signal = pyqtSignal(int, int)
    lyrics_progress_signal = pyqtSignal(int, int, str)
    lyrics_done_signal = pyqtSignal(int, int)
    mb_search_signal = pyqtSignal(list)
    mb_status_signal = pyqtSignal(str)
    mb_apply_signal = pyqtSignal(int)
    mb_cover_signal = pyqtSignal(int)
    mb_cover_progress_signal = pyqtSignal(int, int, str)
    fetch_done_signal = pyqtSignal(list, int)
    download_done_signal = pyqtSignal()
    track_event_signal = pyqtSignal(str, int, int, str, str, str)
    dl_progress_signal = pyqtSignal(float)

    def __init__(self):
        super().__init__()
        self.progress_signal.connect(self._on_progress)
        self.status_signal.connect(self._on_status)
        self.queue_progress_signal.connect(self._on_queue_progress)
        self.mb_search_signal.connect(self._on_mb_search_result)
        self.mb_status_signal.connect(self._on_mb_status)
        self.mb_apply_signal.connect(self._on_mb_apply_done)
        self.mb_cover_signal.connect(self._on_mb_cover_done)
        self.mb_cover_progress_signal.connect(self._on_mb_cover_progress)
        self.fetch_done_signal.connect(self._on_fetch_done)
        self.download_done_signal.connect(self._on_download_done)
        self.track_event_signal.connect(self._on_track_event)
        self.dl_progress_signal.connect(self._on_dl_progress)
        self._tag_panel_filepath = None
        self._cancel_requested = False
        # Idle timer: restores sorting once the P2P result stream goes quiet.
        # Enabling sorting per-result lets Qt re-sort mid-batch.
        self._p2p_sort_timer = QTimer(self)
        self._p2p_sort_timer.setSingleShot(True)
        self._p2p_sort_timer.setInterval(400)
        self._p2p_sort_timer.timeout.connect(
            lambda: self.p2p_results.setSortingEnabled(True))
        self._track_rows = []
        self._fetch_token = 0
        self._resolved_platform = None
        self.downloader = Downloader(
            progress_callback=self.progress_signal.emit,
            status_callback=self.status_signal.emit,
            queue_progress_callback=self.queue_progress_signal.emit,
            on_track_event=lambda *a: self.track_event_signal.emit(*a),
        )
        icon_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "nexusaudio.png")
        if os.path.exists(icon_path):
            self.setWindowIcon(QIcon(icon_path))
        self._check_dependencies()
        self.init_ui()
        self._setup_shortcuts()
        self.load_files()
        self._setup_tray()

    def _check_dependencies(self):
        missing = []
        try:
            subprocess.run(['ffmpeg', '-version'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5)
        except (FileNotFoundError, subprocess.TimeoutExpired):
            missing.append("ffmpeg (required for audio conversion)")
        try:
            import yt_dlp
        except ImportError:
            missing.append("yt-dlp (required for downloads)")
        if missing:
            msg = "Missing dependencies:\n\n" + "\n".join(f"  - {m}" for m in missing)
            msg += "\n\nInstall with: pip install yt-dlp\nffmpeg: sudo apt install ffmpeg"
            self._toast_warning(msg)

    def _toast_warning(self, message):
        logger.warning(message)
        QMessageBox.warning(self, "NexusAudio", message)

    def init_ui(self):
        self.setWindowTitle("NexusAudio - Downloader & Tag Editor")
        self.setGeometry(100, 100, 1200, 800)
        self.current_theme = config.THEME
        self.tabs = QTabWidget()
        self.setCentralWidget(self.tabs)
        self.create_downloader_tab()
        self.create_downloading_tab()
        self.create_tagger_tab()
        self.create_p2p_tab()
        self.create_settings_tab()
        self.apply_theme(self.current_theme)
        self.p2p_manager = None

    def _setup_shortcuts(self):
        QShortcut(QKeySequence("Ctrl+Return"), self.url_input, self.fetch_tracks)
        QShortcut(QKeySequence("Ctrl+D"), self, self.start_download)
        QShortcut(QKeySequence("Escape"), self, self.cancel_download)
        QShortcut(QKeySequence("F5"), self, self.load_files)
        QShortcut(QKeySequence("Delete"), self.table, self._delete_selected)

    def create_downloader_tab(self):
        self.downloader_widget = QWidget()
        self.tabs.addTab(self.downloader_widget, "Downloader")
        layout = QVBoxLayout()

        url_layout = QHBoxLayout()
        url_label = QLabel("Source:")
        self.url_input = QLineEdit()
        self.url_input.setPlaceholderText(
            "Spotify, Apple Music, Deezer, YouTube or SoundCloud URL..."
        )
        self.url_input.setToolTip(
            "Track, album or playlist URL from any supported service "
            "(Ctrl+Enter to fetch)"
        )
        url_layout.addWidget(url_label)
        url_layout.addWidget(self.url_input)
        # Pressing Enter in the URL field fetches, matching the Fetch button.
        self.url_input.returnPressed.connect(self.fetch_tracks)

        self.platform_combo = QComboBox()
        self.platform_combo.addItem("Auto-detect", "")
        for slug, label in PLATFORM_LABELS.items():
            self.platform_combo.addItem(label, slug)
        self.platform_combo.setToolTip(
            "Force a specific platform, or leave on Auto-detect to read it from the URL"
        )
        self.platform_combo.setMinimumWidth(160)
        url_layout.addWidget(self.platform_combo)
        self.fetch_btn = QPushButton("Fetch")
        self.fetch_btn.setToolTip("Fetch track list from Spotify URL")
        self.fetch_btn.clicked.connect(self.fetch_tracks)
        url_layout.addWidget(self.fetch_btn)
        layout.addLayout(url_layout)

        options_layout = QHBoxLayout()
        format_label = QLabel("Format:")
        self.format_combo = QComboBox()
        self.format_combo.addItems(['mp3', 'flac', 'm4a', 'ogg'])
        self.format_combo.currentTextChanged.connect(self.update_quality_options)
        options_layout.addWidget(format_label)
        options_layout.addWidget(self.format_combo)

        quality_label = QLabel("Quality:")
        self.quality_combo = QComboBox()
        self.quality_combo.addItems(['64', '128', '192', '256', '320'])
        self.quality_combo.setCurrentText('192')
        options_layout.addWidget(quality_label)
        options_layout.addWidget(self.quality_combo)

        cookies_label = QLabel("Cookies:")
        self.cookies_combo = QComboBox()
        self.cookies_combo.addItems(['firefox', 'chrome', 'chromium', 'brave', 'edge', 'safari', 'none'])
        self.cookies_combo.setCurrentText(config.COOKIES_BROWSER)
        options_layout.addWidget(cookies_label)
        options_layout.addWidget(self.cookies_combo)
        options_layout.addStretch()
        layout.addLayout(options_layout)

        tracks_header = QHBoxLayout()
        tracks_header.addWidget(QLabel("Tracks:"))
        self.select_all_btn = QPushButton("Select All")
        self.select_all_btn.setToolTip("Check all tracks")
        self.select_all_btn.clicked.connect(self.select_all_tracks)
        tracks_header.addWidget(self.select_all_btn)
        self.deselect_all_btn = QPushButton("Deselect All")
        self.deselect_all_btn.setToolTip("Uncheck all tracks")
        self.deselect_all_btn.clicked.connect(self.deselect_all_tracks)
        tracks_header.addWidget(self.deselect_all_btn)
        tracks_header.addStretch()
        layout.addLayout(tracks_header)

        self.track_table = QTableWidget()
        self.track_table.setColumnCount(6)
        self.track_table.setHorizontalHeaderLabels(['', 'Title', 'Artist', 'Album', '#', 'Source'])
        header = self.track_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        self.track_table.setColumnWidth(0, 40)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.Fixed)
        self.track_table.setColumnWidth(4, 50)
        header.setSectionResizeMode(5, QHeaderView.ResizeMode.Fixed)
        self.track_table.setColumnWidth(5, 90)
        self.track_table.cellChanged.connect(self.on_track_check_changed)
        self.track_table.itemClicked.connect(self.on_track_item_clicked)
        layout.addWidget(self.track_table)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        layout.addWidget(self.progress_bar)

        self.status_label = QLabel("Ready")
        layout.addWidget(self.status_label)

        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        self.download_btn = QPushButton("Download Selected")
        self.download_btn.setToolTip("Start downloading selected tracks")
        self.download_btn.clicked.connect(self.start_download)
        btn_layout.addWidget(self.download_btn)
        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.setToolTip("Cancel current download and clear queue")
        self.cancel_btn.clicked.connect(self.cancel_download)
        self.cancel_btn.setEnabled(False)
        btn_layout.addWidget(self.cancel_btn)
        layout.addLayout(btn_layout)

        self.downloader_widget.setLayout(layout)

    def create_downloading_tab(self):
        """Live view of every track being grabbed, from any platform."""
        self.downloading_widget = QWidget()
        self.tabs.addTab(self.downloading_widget, "Downloading")
        layout = QVBoxLayout()
        layout.setSpacing(4)

        toolbar = QHBoxLayout()
        self.dl_summary = QLabel("No active transfers")
        toolbar.addWidget(self.dl_summary)
        toolbar.addStretch()
        clear_done_btn = QPushButton("Clear Finished")
        clear_done_btn.setToolTip("Remove completed and failed rows from this list")
        clear_done_btn.clicked.connect(self._dl_clear_finished)
        toolbar.addWidget(clear_done_btn)
        self.dl_cancel_btn = QPushButton("Cancel All")
        self.dl_cancel_btn.setToolTip("Cancel active downloads and clear the queue")
        self.dl_cancel_btn.clicked.connect(self._dl_cancel_all)
        self.dl_cancel_btn.setEnabled(False)
        toolbar.addWidget(self.dl_cancel_btn)
        layout.addLayout(toolbar)

        self.dl_table = QTableWidget()
        self.dl_table.setColumnCount(7)
        self.dl_table.setHorizontalHeaderLabels(
            ['#', 'Title', 'Artist', 'Platform', 'Progress', 'Status', 'Size'])
        dl_header = self.dl_table.horizontalHeader()
        dl_header.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        self.dl_table.setColumnWidth(0, 45)
        dl_header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        dl_header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        dl_header.setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed)
        self.dl_table.setColumnWidth(3, 80)
        dl_header.setSectionResizeMode(4, QHeaderView.ResizeMode.Fixed)
        self.dl_table.setColumnWidth(4, 140)
        dl_header.setSectionResizeMode(5, QHeaderView.ResizeMode.Fixed)
        self.dl_table.setColumnWidth(5, 110)
        dl_header.setSectionResizeMode(6, QHeaderView.ResizeMode.Fixed)
        self.dl_table.setColumnWidth(6, 100)
        self.dl_table.verticalHeader().setVisible(False)
        self.dl_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.dl_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        layout.addWidget(self.dl_table)

        self.downloading_widget.setLayout(layout)

        # Row bookkeeping: queue rows waiting to start, and the active row.
        self._dl_pending_rows = []
        self._dl_active_row = None
        self._dl_p2p_rows = {}

    def _dl_set_row(self, row, title=None, artist=None, platform=None,
                    status=None, size=None):
        for col, value in ((1, title), (2, artist), (3, platform), (5, status)):
            if value is None:
                continue
            item = self.dl_table.item(row, col)
            if item is None:
                item = QTableWidgetItem()
                self.dl_table.setItem(row, col, item)
            item.setText(str(value))
        if size is not None:
            item = self.dl_table.item(row, 6)
            if item is None:
                item = QTableWidgetItem()
                self.dl_table.setItem(row, 6, item)
            item.setText(self._format_size(int(size)))

    def _dl_set_progress(self, row, percent):
        bar = self.dl_table.cellWidget(row, 4)
        if bar is None:
            bar = QProgressBar()
            bar.setRange(0, 100)
            bar.setTextVisible(True)
            self.dl_table.setCellWidget(row, 4, bar)
        bar.setValue(max(0, min(100, int(percent))))

    def _dl_new_row(self, title, artist, platform, status='Queued'):
        row = self.dl_table.rowCount()
        self.dl_table.insertRow(row)
        self.dl_table.setItem(row, 0, QTableWidgetItem(str(row + 1)))
        self._dl_set_row(row, title=title, artist=artist, platform=platform, status=status)
        self._dl_set_progress(row, 0)
        return row

    def _dl_init_row(self, row, title, artist, platform, status='Queued'):
        """Create a row with every cell populated, so no column is ever None."""
        self.dl_table.insertRow(row)
        self.dl_table.setItem(row, 0, QTableWidgetItem(str(row + 1)))
        for col in (1, 2, 3, 5, 6):
            self.dl_table.setItem(row, col, QTableWidgetItem(''))
        self._dl_set_row(row, title=title, artist=artist, platform=platform, status=status)
        self._dl_set_progress(row, 0)
        return row

    def _on_track_event(self, event, index, total, name, artist, platform):
        """Track lifecycle events from the download queue."""
        if event == 'queued':
            row = self._dl_init_row(self.dl_table.rowCount(), name, artist, platform)
            self._dl_pending_rows.append(row)
            self._dl_refresh_summary()
        elif event == 'start':
            if self._dl_pending_rows:
                self._dl_active_row = self._dl_pending_rows.pop(0)
            if self._dl_active_row is None:
                return
            self._dl_set_row(self._dl_active_row, status='Downloading')
            self._dl_set_progress(self._dl_active_row, 0)
        elif event in ('done', 'failed'):
            row = self._dl_active_row
            if row is None:
                return
            if event == 'done':
                self._dl_set_row(row, status='Complete')
                self._dl_set_progress(row, 100)
            else:
                self._dl_set_row(row, status='Failed')
            self._dl_active_row = None
            self._dl_refresh_summary()

    def _on_dl_progress(self, percent):
        """Per-track progress for whichever track is currently downloading."""
        # QProgressBar.setValue() only accepts int in PyQt6, and the signal
        # now carries the float percentages yt-dlp reports.
        value = max(0, min(100, int(percent)))
        self.progress_bar.setValue(value)
        if self._dl_active_row is None:
            return
        self._dl_set_progress(self._dl_active_row, value)

    _DL_TERMINAL = ('complete', 'failed', 'cancelled')

    def _dl_refresh_summary(self):
        active = 0
        finished = 0
        failed = 0
        for row in range(self.dl_table.rowCount()):
            cell = self.dl_table.item(row, 5)
            if cell is None:
                continue
            text = cell.text().lower()
            if text in self._DL_TERMINAL:
                finished += 1
                if text == 'failed':
                    failed += 1
            else:
                active += 1
        if active:
            self.dl_summary.setText(f"{active} active, {finished} finished")
        elif finished:
            self.dl_summary.setText(
                f"{finished} finished" + (f", {failed} failed" if failed else ""))
        else:
            self.dl_summary.setText("No active transfers")
        self.dl_cancel_btn.setEnabled(active > 0)

    def _dl_clear_finished(self):
        removed = [
            row for row in range(self.dl_table.rowCount())
            if (cell := self.dl_table.item(row, 5)) is not None
            and cell.text().lower() in self._DL_TERMINAL
        ]
        if not removed:
            return
        removed_set = set(removed)

        def remap(row):
            if row is None:
                return None
            if row in removed_set:
                return None
            return row - sum(1 for r in removed if r < row)

        for row in reversed(removed):
            self.dl_table.removeRow(row)
        self._dl_active_row = remap(self._dl_active_row)
        self._dl_pending_rows = [
            mapped for r in self._dl_pending_rows
            if (mapped := remap(r)) is not None
        ]
        self._dl_p2p_rows = {
            ticket: remap(row) for ticket, row in self._dl_p2p_rows.items()
            if remap(row) is not None
        }
        for row in range(self.dl_table.rowCount()):
            item = self.dl_table.item(row, 0)
            if item:
                item.setText(str(row + 1))
        self._dl_refresh_summary()

    def _dl_cancel_all(self):
        self._cancel_requested = True
        self.downloader.cancel_all()
        for row in self._dl_pending_rows:
            self._dl_set_row(row, status='Cancelled')
        self._dl_pending_rows = []
        if self._dl_active_row is not None:
            self._dl_set_row(self._dl_active_row, status='Cancelled')
            self._dl_active_row = None
        self._dl_refresh_summary()
        self.status_label.setText("Cancelled")

    # -- Soulseek transfers, shown in the same table ------------------

    def _dl_add_p2p_row(self, username, filename, ticket):
        """Add a Soulseek transfer to the Downloading tab."""
        row = self._dl_init_row(row=self.dl_table.rowCount(),
                                title=os.path.basename(filename),
                                artist=username, platform='Soulseek', status='Queued')
        self._dl_p2p_rows[ticket] = row
        self._dl_refresh_summary()

    def _dl_update_p2p_row(self, ticket, downloaded, path, status):
        row = self._dl_p2p_rows.get(ticket)
        if row is None:
            return
        total = self._p2p_transfer_size(ticket)
        if downloaded:
            if total:
                self._dl_set_progress(row, downloaded * 100 / total)
                self._dl_set_row(row, size=downloaded)
            else:
                self._dl_set_row(row, size=downloaded)
        text = status.lower()
        if text in ('complete', 'failed'):
            self._dl_set_row(row, status='Complete' if text == 'complete' else 'Failed')
            if text == 'complete':
                self._dl_set_progress(row, 100)
            self._dl_refresh_summary()
        else:
            # Don't clobber a still-queued row with 'Downloading' on the
            # first progress callback.
            current = self.dl_table.item(row, 5)
            if current is None or current.text().lower() == 'queued':
                self._dl_set_row(row, status='Queued')
            else:
                self._dl_set_row(row, status='Downloading')
            self.dl_cancel_btn.setEnabled(True)

    def create_tagger_tab(self):
        self.tagger_widget = QWidget()
        self.tabs.addTab(self.tagger_widget, "Tag Editor")
        layout = QVBoxLayout()
        layout.setSpacing(4)

        # --- Toolbar ---
        toolbar = QHBoxLayout()
        toolbar_btns = [
            ("Refresh Files", "Reload file list from download folder", self.load_files),
            ("Rename: Artist - Title", "Rename all files to Artist - Title", self.rename_files),
            ("Delete File", "Permanently delete selected file", self._delete_selected),
            ("Fetch Lyrics", "Fetch and embed lyrics for all files", self.fetch_lyrics_for_all),
            ("Extended Tags", "Edit all tag fields in a dialog", self._show_extended_tags),
        ]
        for text, tip, slot in toolbar_btns:
            b = QPushButton(text)
            b.setToolTip(tip)
            b.clicked.connect(slot)
            toolbar.addWidget(b)
        toolbar.addStretch()
        layout.addLayout(toolbar)

        # --- Find & Replace ---
        fr = QHBoxLayout()
        fr.addWidget(QLabel("Find:"))
        self.tag_find_input = QLineEdit()
        self.tag_find_input.setPlaceholderText("Find in tags...")
        fr.addWidget(self.tag_find_input)
        fr.addWidget(QLabel("Replace:"))
        self.tag_replace_input = QLineEdit()
        self.tag_replace_input.setPlaceholderText("Replace with...")
        fr.addWidget(self.tag_replace_input)
        self.tag_regex_cb = QCheckBox("Regex")
        fr.addWidget(self.tag_regex_cb)
        fr.addWidget(QLabel("Case:"))
        self.tag_case_cb = QCheckBox()
        fr.addWidget(self.tag_case_cb)
        rep_btn = QPushButton("Replace All")
        rep_btn.setToolTip("Find and replace tag text across all files")
        rep_btn.clicked.connect(self._find_replace_tags)
        fr.addWidget(rep_btn)
        clear_btn = QPushButton("Clear Tags")
        clear_btn.setToolTip("Remove all tags from selected file")
        clear_btn.clicked.connect(self._clear_selected_tags)
        fr.addWidget(clear_btn)
        layout.addLayout(fr)

        # --- MusicBrainz row ---
        mb = QHBoxLayout()
        mb.addWidget(QLabel("MusicBrainz:"))
        self.mb_artist_input = QLineEdit()
        self.mb_artist_input.setPlaceholderText("Artist")
        mb.addWidget(self.mb_artist_input)
        self.mb_album_input = QLineEdit()
        self.mb_album_input.setPlaceholderText("Album")
        mb.addWidget(self.mb_album_input)
        mb_search_btn = QPushButton("Lookup")
        mb_search_btn.setToolTip("Search MusicBrainz for releases")
        mb_search_btn.clicked.connect(self._mb_search)
        mb.addWidget(mb_search_btn)
        self.mb_results = QComboBox()
        self.mb_results.setMinimumWidth(300)
        mb.addWidget(self.mb_results)
        self.mb_apply_btn = QPushButton("Apply to Files")
        self.mb_apply_btn.setToolTip("Write album/track tags to all files")
        self.mb_apply_btn.clicked.connect(self._mb_apply)
        mb.addWidget(self.mb_apply_btn)
        self.mb_cover_btn = QPushButton("Download Cover")
        self.mb_cover_btn.setToolTip("Download and embed cover art in all files")
        self.mb_cover_btn.clicked.connect(self._mb_download_cover)
        mb.addWidget(self.mb_cover_btn)
        self.mb_cover_btn.setEnabled(False)
        layout.addLayout(mb)

        # --- Table ---
        self.table = QTableWidget()
        self.table.setColumnCount(7)
        self.table.setHorizontalHeaderLabels(['File', 'Title', 'Artist', 'Album', 'Genre', 'Year', 'Track#'])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.cellChanged.connect(self.on_cell_changed)
        self.table.itemSelectionChanged.connect(self._on_tagger_selection_changed)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._table_context_menu)
        layout.addWidget(self.table, stretch=3)

        # --- Tag Panel (puddletag-style detail panel) ---
        self.tag_panel_group = QGroupBox("Tags")
        panel = QVBoxLayout(self.tag_panel_group)

        def _mkrow(labels_keys):
            h = QHBoxLayout()
            widgets = {}
            for label, key in labels_keys:
                h.addWidget(QLabel(label))
                inp = QLineEdit()
                inp.setPlaceholderText(key)
                inp.editingFinished.connect(self._on_tag_panel_field_edit)
                h.addWidget(inp)
                widgets[key] = inp
            panel.addLayout(h)
            return widgets

        self.tp_widgets = {}
        self.tp_widgets.update(_mkrow([
            ("Title:", "title"), ("Artist:", "artist"), ("Album:", "album"),
        ]))
        self.tp_widgets.update(_mkrow([
            ("Genre:", "genre"), ("Year:", "year"), ("Track#:", "tracknumber"),
        ]))
        self.tp_widgets.update(_mkrow([
            ("Composer:", "composer"), ("Album Artist:", "albumartist"),
            ("Disc#:", "discnumber"), ("BPM:", "bpm"),
        ]))
        self.tp_widgets.update(_mkrow([
            ("ISRC:", "isrc"), ("Publisher:", "publisher"),
            ("Grouping:", "grouping"),
        ]))
        self.tp_widgets.update(_mkrow([
            ("Performer:", "performer"), ("Lyricist:", "lyricist"),
            ("EncodedBy:", "encodedby"), ("Copyright:", "copyright"),
        ]))
        comment_row = QHBoxLayout()
        comment_row.addWidget(QLabel("Comment:"))
        self.tp_comment = QLineEdit()
        self.tp_comment.editingFinished.connect(self._on_tag_panel_field_edit)
        comment_row.addWidget(self.tp_comment)
        panel.addLayout(comment_row)
        self.tp_widgets["comment"] = self.tp_comment

        layout.addWidget(self.tag_panel_group, stretch=2)

        # --- Cover art label ---
        cover_row = QHBoxLayout()
        self.tp_cover_label = QLabel()
        self.tp_cover_label.setFixedSize(120, 120)
        self.tp_cover_label.setStyleSheet("border: 1px solid #888; background: #222;")
        self.tp_cover_label.setScaledContents(True)
        cover_row.addWidget(self.tp_cover_label)
        cover_row.addStretch()
        panel.addLayout(cover_row)

        # --- Progress + Status ---
        self.tagger_progress_bar = QProgressBar()
        self.tagger_progress_bar.setRange(0, 100)
        self.tagger_progress_bar.setValue(0)
        layout.addWidget(self.tagger_progress_bar)

        self.tagger_status_label = QLabel("Ready")
        layout.addWidget(self.tagger_status_label)

        self.lyrics_progress_signal.connect(self._on_lyrics_progress)
        self.lyrics_done_signal.connect(self._on_lyrics_done)
        self.tagger_widget.setLayout(layout)

    def create_p2p_tab(self):
        self.p2p_widget = QWidget()
        self.tabs.addTab(self.p2p_widget, "P2P (Soulseek)")
        layout = QVBoxLayout()

        # --- Connection bar (Nicotine+/SoulseekQt style) ---
        conn = QHBoxLayout()
        self.p2p_status_label = QLabel("● Disconnected")
        self.p2p_status_label.setStyleSheet("color: #e74c3c; font-weight: bold;")
        conn.addWidget(self.p2p_status_label)
        self.p2p_user_label = QLabel("")
        conn.addWidget(self.p2p_user_label)
        conn.addStretch()
        self.connect_btn = QPushButton("Connect")
        self.connect_btn.setToolTip("Connect or disconnect from Soulseek server")
        self.connect_btn.clicked.connect(self.toggle_p2p_connection)
        conn.addWidget(self.connect_btn)
        layout.addLayout(conn)

        # --- Search bar ---
        search_row = QHBoxLayout()
        self.p2p_search = QLineEdit()
        self.p2p_search.setPlaceholderText("Search the Soulseek network…")
        self.p2p_search.setToolTip("Enter search terms (Return to search)")
        self.p2p_search.returnPressed.connect(self.p2p_search_files)
        search_row.addWidget(self.p2p_search, stretch=1)
        search_btn = QPushButton("Search")
        search_btn.setToolTip("Search for files (double-click a result to download)")
        search_btn.clicked.connect(self.p2p_search_files)
        search_row.addWidget(search_btn)
        clear_btn = QPushButton("Clear")
        clear_btn.setToolTip("Clear search results")
        clear_btn.clicked.connect(self._p2p_clear_results)
        search_row.addWidget(clear_btn)
        layout.addLayout(search_row)

        self.p2p_result_count = QLabel("0 results")
        layout.addWidget(self.p2p_result_count)

        # --- Splitter: search results (top) + transfers (bottom) ---
        splitter = QSplitter(Qt.Orientation.Vertical)

        results_group = QGroupBox("Search Results")
        results_layout = QVBoxLayout(results_group)
        self.p2p_results = QTableWidget()
        self.p2p_results.setColumnCount(6)
        self.p2p_results.setHorizontalHeaderLabels(
            ['User', 'Filename', 'Size', 'Bitrate', 'Length', 'Ext'])
        rh = self.p2p_results.horizontalHeader()
        rh.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        rh.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        for col in range(2, 6):
            rh.setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)
        self.p2p_results.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.p2p_results.setSortingEnabled(True)
        self.p2p_results.setAlternatingRowColors(True)
        self.p2p_results.doubleClicked.connect(self._p2p_download_selected_row)
        self.p2p_results.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.p2p_results.customContextMenuRequested.connect(self._p2p_results_context_menu)
        results_layout.addWidget(self.p2p_results)
        splitter.addWidget(results_group)

        transfers_group = QGroupBox("Transfers")
        transfers_layout = QVBoxLayout(transfers_group)
        self.p2p_transfers = QTableWidget()
        self.p2p_transfers.setColumnCount(5)
        self.p2p_transfers.setHorizontalHeaderLabels(
            ['User', 'Filename', 'Status', 'Progress', 'Path'])
        th = self.p2p_transfers.horizontalHeader()
        th.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        th.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        th.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        th.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        th.setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        self.p2p_transfers.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.p2p_transfers.setAlternatingRowColors(True)
        transfers_layout.addWidget(self.p2p_transfers)
        splitter.addWidget(transfers_group)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 1)
        layout.addWidget(splitter, stretch=1)

        # --- Log panel ---
        log_group = QGroupBox("Log")
        log_layout = QVBoxLayout(log_group)
        self.p2p_status = QTextEdit()
        self.p2p_status.setMaximumHeight(80)
        self.p2p_status.setReadOnly(True)
        log_layout.addWidget(self.p2p_status)
        layout.addWidget(log_group)

        self.p2p_widget.setLayout(layout)
        self._p2p_transfer_rows = {}
        self._p2p_expected_sizes = {}
        self.p2p_status_signal.connect(self._on_p2p_status)
        self.p2p_search_signal.connect(self._on_p2p_search_result)
        self.p2p_download_signal.connect(self._on_p2p_download)
        self.p2p_transfer_signal.connect(self._on_p2p_transfer)
        self.p2p_connected_signal.connect(self._on_p2p_connection_result)
        self.p2p_error_signal.connect(self._on_p2p_error)

    def create_settings_tab(self):
        self.settings_widget = QWidget()
        self.tabs.addTab(self.settings_widget, "Settings")
        layout = QVBoxLayout()
        layout.addWidget(QLabel("Spotify API: Pre-configured and secured (embedded)"))
        layout.addWidget(QLabel("Soulseek P2P: Configure your account below"))
        layout.addWidget(QLabel("How to get a Soulseek account:"))
        info_text = QTextEdit()
        info_text.setReadOnly(True)
        info_text.setMaximumHeight(100)
        info_text.setPlainText(
            "1. Visit https://www.slsknet.org/news/register.php\n"
            "2. Fill in username, password, and email\n"
            "3. Wait for email confirmation (check spam folder)\n"
            "4. Login with your new account in the P2P tab"
        )
        layout.addWidget(info_text)
        layout.addWidget(QLabel("Appearance:"))
        theme_layout = QHBoxLayout()
        theme_layout.addWidget(QLabel("Theme:"))
        self.settings_theme = QComboBox()
        self.settings_theme.addItems(get_theme_names())
        self.settings_theme.setCurrentText(self.current_theme)
        theme_layout.addWidget(self.settings_theme)
        apply_theme_btn = QPushButton("Apply Theme")
        apply_theme_btn.clicked.connect(self.apply_selected_theme)
        theme_layout.addWidget(apply_theme_btn)
        theme_layout.addStretch()
        layout.addLayout(theme_layout)
        layout.addWidget(QLabel("Download Settings:"))
        download_layout = QVBoxLayout()
        folder_layout = QHBoxLayout()
        folder_layout.addWidget(QLabel("Download Folder:"))
        self.settings_folder = QLineEdit(config.DOWNLOAD_DIR)
        folder_layout.addWidget(self.settings_folder)
        folder_btn = QPushButton("Browse...")
        folder_btn.clicked.connect(self.browse_folder)
        folder_layout.addWidget(folder_btn)
        download_layout.addLayout(folder_layout)
        cookies_layout = QHBoxLayout()
        cookies_layout.addWidget(QLabel("Cookies Browser:"))
        self.settings_cookies = QComboBox()
        self.settings_cookies.addItems(['firefox', 'chrome', 'chromium', 'brave', 'edge', 'safari', 'none'])
        self.settings_cookies.setCurrentText(config.COOKIES_BROWSER)
        # The Downloader tab has its own copy; keep the two in sync so a
        # change on the Settings page takes effect without a restart.
        self.settings_cookies.currentTextChanged.connect(
            lambda text: self.cookies_combo.setCurrentText(text))
        cookies_layout.addWidget(self.settings_cookies)
        download_layout.addLayout(cookies_layout)
        self.settings_metadata = QCheckBox("Embed metadata (ID3 tags)")
        self.settings_metadata.setChecked(config.EMBED_METADATA)
        self.settings_metadata.setToolTip(
            "Write title/artist/album tags into each downloaded file")
        download_layout.addWidget(self.settings_metadata)
        self.settings_lyrics = QCheckBox("Fetch and embed lyrics")
        self.settings_lyrics.setChecked(config.EMBED_LYRICS)
        self.settings_lyrics.setToolTip(
            "Look up lyrics online and embed them into downloaded files")
        download_layout.addWidget(self.settings_lyrics)

        close_layout = QHBoxLayout()
        close_layout.addWidget(QLabel("When Closing Window:"))
        self.settings_close_action = QComboBox()
        self.settings_close_action.addItems(['ask', 'minimize', 'quit'])
        if config.CLOSE_ACTION in ('ask', 'minimize', 'quit'):
            self.settings_close_action.setCurrentText(config.CLOSE_ACTION)
        self.settings_close_action.setToolTip(
            "ask = prompt, minimize = hide to tray, quit = exit")
        close_layout.addWidget(self.settings_close_action)
        download_layout.addLayout(close_layout)
        layout.addLayout(download_layout)
        layout.addWidget(QLabel("Soulseek P2P Configuration:"))
        p2p_layout = QVBoxLayout()
        p2p_layout.addWidget(QLabel("Don't have an account? See instructions above."))
        p2p_user_layout = QHBoxLayout()
        p2p_user_layout.addWidget(QLabel("Username:"))
        self.settings_p2p_user = QLineEdit(config.P2P_USER)
        p2p_user_layout.addWidget(self.settings_p2p_user)
        p2p_layout.addLayout(p2p_user_layout)
        p2p_pass_layout = QHBoxLayout()
        p2p_pass_layout.addWidget(QLabel("Password:"))
        self.settings_p2p_pass = QLineEdit(config.P2P_PASS)
        self.settings_p2p_pass.setEchoMode(QLineEdit.EchoMode.Password)
        p2p_pass_layout.addWidget(self.settings_p2p_pass)
        p2p_layout.addLayout(p2p_pass_layout)
        p2p_server_layout = QHBoxLayout()
        p2p_server_layout.addWidget(QLabel("Server:"))
        self.settings_p2p_server = QLineEdit(config.P2P_SERVER)
        self.settings_p2p_server.setPlaceholderText('server.slsknet.org')
        p2p_server_layout.addWidget(self.settings_p2p_server)
        p2p_layout.addLayout(p2p_server_layout)
        p2p_port_layout = QHBoxLayout()
        p2p_port_layout.addWidget(QLabel("Port:"))
        self.settings_p2p_port = QLineEdit(str(config.P2P_PORT))
        self.settings_p2p_port.setPlaceholderText('2242')
        self.settings_p2p_port.setMaximumWidth(120)
        p2p_port_layout.addWidget(self.settings_p2p_port)
        p2p_layout.addLayout(p2p_port_layout)
        layout.addLayout(p2p_layout)
        save_btn = QPushButton("Save Settings")
        save_btn.setToolTip("Persist these settings to disk")
        save_btn.clicked.connect(self.save_settings)
        layout.addWidget(save_btn)
        layout.addStretch()
        self.settings_widget.setLayout(layout)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setWidget(self.settings_widget)
        self.settings_scroll = scroll
        self.tabs.removeTab(self.tabs.indexOf(self.settings_widget))
        self.tabs.addTab(scroll, "Settings")

    def apply_theme(self, theme_name):
        self.current_theme = theme_name
        try:
            stylesheet = get_stylesheet(theme_name)
            self.setStyleSheet(stylesheet)
        except Exception as e:
            logger.warning("Theme error: %s", e)

    def apply_selected_theme(self):
        theme_name = self.settings_theme.currentText()
        self.apply_theme(theme_name)
        config.THEME = theme_name

    def update_quality_options(self, format_text):
        self.quality_combo.setEnabled(format_text == 'mp3')

    def fetch_tracks(self):
        url = self.url_input.text().strip()
        platform = self.platform_combo.currentData() or None
        if not url:
            QMessageBox.warning(self, "Error", "Please enter a URL or search term")
            return

        self.fetch_btn.setEnabled(False)
        self.status_label.setText("Fetching track list...")
        self.track_table.setRowCount(0)
        self._resolved_platform = platform
        # Tag this request so a slower earlier fetch cannot overwrite the
        # results of a later one.
        self._fetch_token = getattr(self, '_fetch_token', 0) + 1
        token = self._fetch_token

        def _fetch():
            tracks = self.downloader.get_tracks_from_url(url, platform=platform)
            self.fetch_done_signal.emit(tracks, token)

        threading.Thread(target=_fetch, daemon=True).start()

    def _on_fetch_done(self, tracks, token=None):
        # Drop results from a superseded search.
        if token is not None and token != getattr(self, '_fetch_token', None):
            logger.debug("Ignoring stale fetch result (token %s)", token)
            return
        self._populate_track_table(tracks)
        self.fetch_btn.setEnabled(True)
        if tracks:
            platforms_used = {t.get('platform') for t in tracks if t.get('platform')}
            names = ", ".join(sorted(PLATFORM_LABELS.get(p, p) for p in platforms_used))
            suffix = f" from {names}" if names else ""
            self.status_label.setText(f"Found {len(tracks)} track(s){suffix}")

    def _populate_track_table(self, tracks):
        self._track_rows = [dict(t) for t in tracks]
        self.track_table.blockSignals(True)
        self.track_table.setRowCount(0)
        for i, track in enumerate(tracks):
            row = self.track_table.rowCount()
            self.track_table.insertRow(row)
            cb = QTableWidgetItem()
            cb.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
            cb.setCheckState(Qt.CheckState.Unchecked)
            self.track_table.setItem(row, 0, cb)
            # The title doubles as a selection checkbox so clicking a row
            # highlights it *and* marks it ready to download.
            title_item = QTableWidgetItem(track.get('name', ''))
            title_item.setFlags(
                (title_item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                & ~Qt.ItemFlag.ItemIsEditable
            )
            title_item.setCheckState(Qt.CheckState.Unchecked)
            self.track_table.setItem(row, 1, title_item)
            self.track_table.setItem(row, 2, QTableWidgetItem(track.get('artist', '')))
            self.track_table.setItem(row, 3, QTableWidgetItem(track.get('album', '')))
            self.track_table.setItem(row, 4, QTableWidgetItem(str(i + 1)))
            source = track.get('platform', '')
            self.track_table.setItem(row, 5, QTableWidgetItem(
                PLATFORM_LABELS.get(source, source)))
            self.track_table.item(row, 1).setData(Qt.ItemDataRole.UserRole, i)
        self.track_table.blockSignals(False)

    def on_track_check_changed(self, row, column):
        """Keep the row checkbox and the title checkbox in sync."""
        if column not in (0, 1):
            return
        source = self.track_table.item(row, column)
        if source is None:
            return
        state = source.checkState()
        for col in (0, 1):
            cell = self.track_table.item(row, col)
            if cell is not None and cell is not source:
                cell.setCheckState(state)

    def on_track_item_clicked(self, item):
        """Clicking anywhere in a row selects it and checks it for download.

        Column 0 is a native checkbox and toggles itself; clicking anywhere
        else in the row mirrors that onto it.
        """
        if item is None or item.column() == 0:
            return
        checkbox = self.track_table.item(item.row(), 0)
        if checkbox is None:
            return
        checkbox.setCheckState(
            Qt.CheckState.Unchecked
            if checkbox.checkState() == Qt.CheckState.Checked
            else Qt.CheckState.Checked
        )

    def select_all_tracks(self):
        for row in range(self.track_table.rowCount()):
            for col in (0, 1):
                cell = self.track_table.item(row, col)
                if cell:
                    cell.setCheckState(Qt.CheckState.Checked)

    def deselect_all_tracks(self):
        for row in range(self.track_table.rowCount()):
            for col in (0, 1):
                cell = self.track_table.item(row, col)
                if cell:
                    cell.setCheckState(Qt.CheckState.Unchecked)

    def start_download(self):
        audio_format = self.format_combo.currentText()
        quality = self.quality_combo.currentText()
        cookies = self.cookies_combo.currentText()
        if cookies == 'none':
            cookies = None
        embed_metadata = self.settings_metadata.isChecked()
        embed_lyrics = self.settings_lyrics.isChecked()

        selected = []
        for row in range(self.track_table.rowCount()):
            checkbox = self.track_table.item(row, 0)
            if checkbox is None:
                continue
            if checkbox.checkState() == Qt.CheckState.Checked:
                index = self.track_table.item(row, 1).data(Qt.ItemDataRole.UserRole)
                source = self._track_rows[index] if index is not None else {}
                # Keep resolver metadata (source_url, duration) from the
                # original fetch; the table cells are display-only.
                entry = {
                    'name': source.get('name', self.track_table.item(row, 1).text()),
                    'artist': source.get('artist', self.track_table.item(row, 2).text()),
                    'album': source.get('album', self.track_table.item(row, 3).text()),
                }
                for key in ('source_url', 'duration', 'platform', 'isrc'):
                    if source.get(key):
                        entry[key] = source[key]
                selected.append(entry)

        if not selected:
            QMessageBox.warning(self, "Error", "No tracks selected")
            return

        self.download_btn.setEnabled(False)
        self.cancel_btn.setEnabled(True)
        self.progress_bar.setValue(0)
        self.status_label.setText(f"Queued {len(selected)} track(s)")
        self._cancel_requested = False

        for item in selected:
            self.downloader.queue_track(
                item, config.DOWNLOAD_DIR,
                audio_format, quality, cookies,
                embed_metadata, embed_lyrics
            )
        self.downloader.process_queue()

        self._queue_watch_thread = threading.Thread(target=self._watch_queue_done, daemon=True)
        self._queue_watch_thread.start()

    def _watch_queue_done(self):
        while self.downloader.get_queue_length() > 0 or self.downloader._queue_running:
            threading.Event().wait(0.5)
        self.download_done_signal.emit()

    def _on_download_done(self):
        self.download_btn.setEnabled(True)
        self.cancel_btn.setEnabled(False)
        if self._cancel_requested:
            # cancel_all() empties the queue mid-transfer, so the watcher
            # thread exits and reports 'done'. Don't claim success.
            self.status_label.setText("Cancelled")
        else:
            self.status_label.setText("Download complete!")
        self.load_files()

    def cancel_download(self):
        self._cancel_requested = True
        self.downloader.cancel_all()
        self._dl_cancel_all()  # also resets the Downloading tab bookkeeping
        self.status_label.setText("Cancelled")
        self.download_btn.setEnabled(True)
        self.cancel_btn.setEnabled(False)
        self.progress_bar.setValue(0)

    def _on_progress(self, value):
        self._on_dl_progress(value)

    def _on_queue_progress(self, current, total):
        self.status_label.setText(f"[{current}/{total}] Downloading...")

    def _on_status(self, message):
        self.status_label.setText(message)

    def load_files(self):
        self.table.blockSignals(True)
        self.table.setRowCount(0)
        self._tag_panel_filepath = None
        self.tag_panel_group.setTitle("Tags")
        for inp in self.tp_widgets.values():
            inp.blockSignals(True)
            inp.clear()
            inp.blockSignals(False)
        self.tp_cover_label.clear()
        if not os.path.isdir(config.DOWNLOAD_DIR):
            os.makedirs(config.DOWNLOAD_DIR, exist_ok=True)
            self.table.blockSignals(False)
            return
        files = Tagger.get_supported_files(config.DOWNLOAD_DIR)
        for filepath in files:
            tags = Tagger.read_tags(filepath)
            row = self.table.rowCount()
            self.table.insertRow(row)
            self.table.setItem(row, 0, QTableWidgetItem(tags.get('filename', '')))
            self.table.setItem(row, 1, QTableWidgetItem(tags.get('title', '')))
            self.table.setItem(row, 2, QTableWidgetItem(tags.get('artist', '')))
            self.table.setItem(row, 3, QTableWidgetItem(tags.get('album', '')))
            self.table.setItem(row, 4, QTableWidgetItem(tags.get('genre', '')))
            self.table.setItem(row, 5, QTableWidgetItem(tags.get('year', '')))
            self.table.setItem(row, 6, QTableWidgetItem(tags.get('tracknumber', '')))
            self.table.item(row, 0).setData(Qt.ItemDataRole.UserRole, filepath)
        self.table.blockSignals(False)

    def on_cell_changed(self, row, column):
        if column < 1:
            return
        filepath = self.table.item(row, 0).data(Qt.ItemDataRole.UserRole)
        if not filepath:
            return
        tag_map = {1: 'title', 2: 'artist', 3: 'album', 4: 'genre', 5: 'year', 6: 'tracknumber'}
        tag_name = tag_map.get(column)
        if not tag_name:
            return
        new_value = self.table.item(row, column).text()
        tags = {tag_name: new_value}
        if not Tagger.write_tags(filepath, tags):
            self.tagger_status_label.setText(
                f"Tag write failed for {os.path.basename(filepath)}")

    def _find_replace_tags(self):
        find_text = self.tag_find_input.text()
        if not find_text:
            return
        replace_text = self.tag_replace_input.text()
        use_regex = self.tag_regex_cb.isChecked()
        changed = 0
        try:
            pattern = re.compile(find_text) if use_regex else None
        except re.error as e:
            QMessageBox.warning(self, "Regex Error", f"Invalid regex: {e}")
            return
        # Block cellChanged while rewriting the table: otherwise every
        # setText triggers on_cell_changed and each tag is written twice.
        self.table.blockSignals(True)
        try:
            for row in range(self.table.rowCount()):
                filepath = self.table.item(row, 0).data(Qt.ItemDataRole.UserRole)
                if not filepath:
                    continue
                tags = Tagger.read_tags(filepath)
                modified = {}
                for key in ('title', 'artist', 'album', 'genre', 'year', 'tracknumber'):
                    val = tags.get(key, '')
                    if not val:
                        continue
                    new_val = pattern.sub(replace_text, val) if pattern else val.replace(find_text, replace_text)
                    if new_val != val:
                        modified[key] = new_val
                        col_map = {'title': 1, 'artist': 2, 'album': 3, 'genre': 4, 'year': 5, 'tracknumber': 6}
                        if key in col_map:
                            c = col_map[key]
                            item = self.table.item(row, c)
                            if item:
                                item.setText(new_val)
                if modified:
                    Tagger.write_tags(filepath, modified)
                    changed += 1
        finally:
            self.table.blockSignals(False)
        self.tagger_status_label.setText(f"Replaced in {changed} file(s)")

    def _mb_search(self):
        artist = self.mb_artist_input.text().strip()
        album = self.mb_album_input.text().strip()
        if not artist and not album:
            QMessageBox.warning(self, "Error", "Enter artist or album name")
            return
        self.mb_status_signal.emit("Searching MusicBrainZ...")
        self.mb_results.clear()
        self.mb_cover_btn.setEnabled(False)

        def _search():
            releases = search_release(artist, album)
            self.mb_search_signal.emit(releases)
            self.mb_status_signal.emit(f"Found {len(releases)} release(s)")

        threading.Thread(target=_search, daemon=True).start()

    def _on_mb_search_result(self, releases):
        self.mb_results.clear()
        for r in releases:
            label = f"{r['artist']} — {r['title']} ({r.get('date', '')[:4]}) [{r['track_count']} tracks]"
            self.mb_results.addItem(label, r["id"])
        self.mb_cover_btn.setEnabled(len(releases) > 0)

    def _on_mb_status(self, msg):
        self.tagger_status_label.setText(msg)

    def _mb_apply(self):
        release_id = self.mb_results.currentData()
        if not release_id:
            QMessageBox.warning(self, "Error", "No MusicBrainZ release selected")
            return
        filepaths = []
        for row in range(self.table.rowCount()):
            fp = self.table.item(row, 0).data(Qt.ItemDataRole.UserRole)
            if fp:
                filepaths.append(fp)
        if not filepaths:
            QMessageBox.warning(self, "Error", "No files in tag table")
            return
        self.mb_status_signal.emit("Fetching release details...")
        rid = release_id

        def _work(fps, rid_):
            album_info, tracks = get_release_details(rid_)
            if not album_info or not tracks:
                self.mb_status_signal.emit("Failed to fetch release details")
                return
            matched = apply_release_to_files(fps, album_info, tracks)
            self.mb_apply_signal.emit(matched)

        threading.Thread(target=_work, args=(filepaths, rid), daemon=True).start()

    def _on_mb_apply_done(self, matched):
        self.load_files()
        self.tagger_status_label.setText(f"Applied tags to {matched} file(s)")

    def _mb_download_cover(self):
        release_id = self.mb_results.currentData()
        if not release_id:
            rows = self.table.selectedItems()
            if rows:
                row = rows[0].row()
                artist = self.table.item(row, 2).text() if self.table.item(row, 2) else ''
                album = self.table.item(row, 3).text() if self.table.item(row, 3) else ''
                if artist and album:
                    self.mb_artist_input.setText(artist)
                    self.mb_album_input.setText(album)
                    self._mb_search()
                    self.tagger_status_label.setText("Searching first, then retry Download Cover")
                    return
            QMessageBox.warning(self, "Error",
                "Select a MusicBrainZ release from the dropdown, or select a file with artist/album tags")
            return
        self.mb_status_signal.emit("Downloading cover art...")
        self.tagger_progress_bar.setValue(0)
        filepaths = []
        for row in range(self.table.rowCount()):
            fp = self.table.item(row, 0).data(Qt.ItemDataRole.UserRole)
            if fp:
                filepaths.append(fp)

        def _work(fps):
            image_data = fetch_cover_art(release_id)
            if not image_data:
                self.mb_status_signal.emit("Cover art not found")
                return
            mime = "image/jpeg"
            if image_data[:4] == b'\x89PNG':
                mime = "image/png"
            embedded = 0
            for fp in fps:
                if embed_cover_art(fp, image_data, mime):
                    embedded += 1
                self.mb_cover_progress_signal.emit(embedded, len(fps), os.path.basename(fp))
            self.mb_cover_signal.emit(embedded)

        threading.Thread(target=_work, args=(filepaths,), daemon=True).start()

    def _on_mb_cover_progress(self, current, total, label):
        pct = int(current / total * 100) if total else 0
        self.tagger_progress_bar.setValue(pct)
        self.tagger_status_label.setText(f"[{current}/{total}] Cover art: {label}")

    def _on_mb_cover_done(self, embedded):
        self.tagger_progress_bar.setValue(100)
        self.tagger_status_label.setText(f"Cover art embedded in {embedded} file(s)")
        self._update_tag_panel()

    def _delete_selected(self):
        rows = self.table.selectedItems()
        if not rows:
            QMessageBox.warning(self, "Error", "Select a file in the tag table first")
            return
        row = rows[0].row()
        filepath = self.table.item(row, 0).data(Qt.ItemDataRole.UserRole)
        if not filepath:
            return
        reply = QMessageBox.question(self, "Delete File",
            f"Permanently delete {os.path.basename(filepath)}?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if reply == QMessageBox.StandardButton.Yes:
            try:
                os.remove(filepath)
                self.tagger_status_label.setText(f"Deleted: {os.path.basename(filepath)}")
                self.load_files()
            except OSError as e:
                QMessageBox.warning(self, "Error", f"Delete failed: {e}")

    def _clear_selected_tags(self):
        rows = self.table.selectedItems()
        if not rows:
            QMessageBox.warning(self, "Error", "Select a file in the tag table first")
            return
        row = rows[0].row()
        filepath = self.table.item(row, 0).data(Qt.ItemDataRole.UserRole)
        if not filepath:
            return
        reply = QMessageBox.question(self, "Clear Tags",
            f"Remove all tags from {os.path.basename(filepath)}?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if reply == QMessageBox.StandardButton.Yes:
            if Tagger.delete_all_tags(filepath):
                self._update_tag_panel()
                self.load_files()
                self.tagger_status_label.setText(f"Cleared tags: {os.path.basename(filepath)}")
            else:
                self.tagger_status_label.setText("Failed to clear tags")

    def _on_tag_panel_field_edit(self):
        fp = self._tag_panel_filepath
        if not fp or not os.path.exists(fp):
            return
        # Blank values are included on purpose: write_tags treats them as a
        # request to remove the tag, so emptying a field clears it.
        changed = {key: inp.text().strip()
                   for key, inp in self.tp_widgets.items()}
        if changed:
            Tagger.write_tags(fp, changed)
            self._sync_table_from_tag_panel(changed)
            self.tagger_status_label.setText(f"Saved: {os.path.basename(fp)}")

    def _sync_table_from_tag_panel(self, changed):
        self.table.blockSignals(True)
        col_map = {'title': 1, 'artist': 2, 'album': 3, 'genre': 4, 'year': 5, 'tracknumber': 6}
        for row in range(self.table.rowCount()):
            fp = self.table.item(row, 0).data(Qt.ItemDataRole.UserRole)
            if fp == self._tag_panel_filepath:
                for key, val in changed.items():
                    col = col_map.get(key)
                    if col is not None:
                        self.table.item(row, col).setText(val)
                break
        self.table.blockSignals(False)

    def _update_tag_panel(self, filepath=None):
        if filepath is None:
            rows = self.table.selectedItems()
            if not rows:
                self.tag_panel_group.setTitle("Tags — (no selection)")
                for inp in self.tp_widgets.values():
                    inp.blockSignals(True)
                    inp.clear()
                    inp.blockSignals(False)
                self.tp_cover_label.clear()
                return
            filepath = self.table.item(rows[0].row(), 0).data(Qt.ItemDataRole.UserRole)
        if not filepath or not os.path.exists(filepath):
            return

        self._tag_panel_filepath = filepath
        self.tag_panel_group.setTitle(f"Tags — {os.path.basename(filepath)}")
        tags = Tagger.read_tags(filepath)

        for inp in self.tp_widgets.values():
            inp.blockSignals(True)
        for key, inp in self.tp_widgets.items():
            inp.setText(tags.get(key, ''))
        for inp in self.tp_widgets.values():
            inp.blockSignals(False)

        try:
            cover = self._extract_cover(filepath)
            if cover:
                from PyQt6.QtGui import QPixmap
                from PyQt6.QtCore import QByteArray
                pix = QPixmap()
                pix.loadFromData(QByteArray(cover))
                self.tp_cover_label.setPixmap(pix)
            else:
                self.tp_cover_label.clear()
        except Exception:
            self.tp_cover_label.clear()

    @staticmethod
    def _extract_cover(filepath):
        ext = os.path.splitext(filepath)[1].lower()
        try:
            if ext == '.mp3':
                from mutagen.id3 import ID3
                audio = ID3(filepath)
                for tag in audio.getall('APIC'):
                    return tag.data
            elif ext == '.flac':
                audio = FLAC(filepath)
                pics = audio.pictures
                if pics:
                    return pics[0].data
            elif ext == '.ogg':
                import base64
                from mutagen.flac import Picture
                audio = OggVorbis(filepath)
                raw = audio.get('metadata_block_picture', [])
                if raw:
                    pic = Picture(base64.b64decode(raw[0]))
                    return pic.data
            elif ext == '.m4a':
                audio = MP4(filepath)
                covr = audio.get('covr', [])
                if covr:
                    return covr[0]
        except Exception:
            pass
        return None

    def _on_tagger_selection_changed(self):
        self._update_tag_panel()
        rows = self.table.selectedItems()
        if not rows:
            return
        row = rows[0].row()
        artist_item = self.table.item(row, 2)
        album_item = self.table.item(row, 3)
        if artist_item and artist_item.text():
            self.mb_artist_input.setText(artist_item.text())
        if album_item and album_item.text():
            self.mb_album_input.setText(album_item.text())

    def tray_available(self):
        return getattr(self, '_tray_icon', None) is not None

    def _setup_tray(self):
        self._tray_icon = None
        try:
            if not QSystemTrayIcon.isSystemTrayAvailable():
                return
            icon_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "nexusaudio.png")
            tray_icon = QSystemTrayIcon(self)
            if os.path.exists(icon_path):
                tray_icon.setIcon(QIcon(icon_path))
            tray_icon.setToolTip("NexusAudio")
            tray_menu = QMenu()
            show_action = tray_menu.addAction("Show/Hide")
            show_action.triggered.connect(self._toggle_visible)
            tray_menu.addSeparator()
            quit_action = tray_menu.addAction("Quit")
            quit_action.triggered.connect(self._quit_app)
            tray_icon.setContextMenu(tray_menu)
            tray_icon.activated.connect(self._tray_activated)
            tray_icon.show()
            self._tray_icon = tray_icon
        except Exception as e:
            logger.debug("Tray icon not available: %s", e)
            self._tray_icon = None

    def _toggle_visible(self):
        if self.isVisible():
            self.hide()
        else:
            self.show()
            self.raise_()
            self.activateWindow()

    def _tray_activated(self, reason):
        if reason == 2:
            self._toggle_visible()

    def _quit_app(self):
        if self._tray_icon:
            self._tray_icon.hide()
        QApplication.quit()

    def _show_close_dialog(self):
        dlg = QMessageBox(self)
        dlg.setWindowTitle("NexusAudio")
        dlg.setIcon(QMessageBox.Icon.Question)
        dlg.setText("Close NexusAudio?")
        dlg.setInformativeText(
            "Keep NexusAudio running in the system tray, or quit completely."
        )
        minimize_btn = dlg.addButton("Minimize to Tray", QMessageBox.ButtonRole.AcceptRole)
        quit_btn = dlg.addButton("Quit", QMessageBox.ButtonRole.DestructiveRole)
        cancel_btn = dlg.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
        remember = QCheckBox("Remember my choice")
        dlg.setCheckBox(remember)
        dlg.exec()
        clicked = dlg.clickedButton()
        if clicked == cancel_btn:
            return None
        action = 'minimize' if clicked == minimize_btn else 'quit'
        if remember.isChecked():
            config.CLOSE_ACTION = action
            try:
                config.save()
            except Exception as e:
                logger.warning("Failed to save close preference: %s", e)
        return action

    def closeEvent(self, event):
        if not self.tray_available():
            event.accept()
            return

        action = config.CLOSE_ACTION
        if action == 'ask':
            action = self._show_close_dialog()
            if action is None:
                event.ignore()
                return
        elif action not in ('minimize', 'quit'):
            action = self._show_close_dialog()
            if action is None:
                event.ignore()
                return

        if action == 'minimize':
            self.hide()
            event.ignore()
        else:
            self._quit_app()
            event.accept()

    def rename_files(self):
        renamed = 0
        skipped = 0
        for row in range(self.table.rowCount()):
            filepath = self.table.item(row, 0).data(Qt.ItemDataRole.UserRole)
            if filepath:
                result = Tagger.rename_to_artist_title(filepath)
                if result:
                    renamed += 1
                else:
                    skipped += 1
        self.load_files()
        if renamed:
            self.tagger_status_label.setText(f"Renamed {renamed} file(s)" + (f", {skipped} skipped" if skipped else ""))

    def fetch_lyrics_for_all(self):
        rows = self.table.rowCount()
        if rows == 0:
            QMessageBox.warning(self, "Error", "No files in tag editor")
            return
        # Snapshot the table on the GUI thread: the worker must not touch
        # widgets, and load_files() (F5 / after a download) can clear the
        # table mid-run and hand the worker None items.
        work = []
        for row in range(rows):
            title_item = self.table.item(row, 1)
            artist_item = self.table.item(row, 2)
            path_item = self.table.item(row, 0)
            if not (title_item and artist_item and path_item):
                continue
            title, artist = title_item.text(), artist_item.text()
            filepath = path_item.data(Qt.ItemDataRole.UserRole)
            if title and artist and filepath:
                work.append((row + 1, title, artist, filepath))
        total = len(work)
        if total == 0:
            self.tagger_status_label.setText("Nothing to fetch")
            return
        self.tagger_progress_bar.setValue(0)
        self.tagger_status_label.setText(f"Fetching lyrics for {total} file(s)...")

        def _work():
            success = 0
            for display_row, title, artist, filepath in work:
                lyrics = fetch_lyrics(title, artist)
                if lyrics and embed_lyrics(filepath, lyrics):
                    success += 1
                self.lyrics_progress_signal.emit(display_row, total, f"{title} - {artist}")
            self.lyrics_done_signal.emit(success, total)

        threading.Thread(target=_work, daemon=True).start()

    def _on_lyrics_progress(self, current, total, label):
        pct = int(current / total * 100) if total else 0
        self.tagger_progress_bar.setValue(pct)
        self.tagger_status_label.setText(f"[{current}/{total}] Lyrics: {label}")

    def _on_lyrics_done(self, success, total):
        self.tagger_progress_bar.setValue(100 if total > 0 else 0)
        self.tagger_status_label.setText(f"Lyrics embedded in {success}/{total} file(s)")

    def _table_context_menu(self, pos):
        item = self.table.itemAt(pos)
        if not item:
            return
        row = item.row()
        filepath = self.table.item(row, 0).data(Qt.ItemDataRole.UserRole)
        title = self.table.item(row, 1).text() if self.table.item(row, 1) else ''
        artist = self.table.item(row, 2).text() if self.table.item(row, 2) else ''

        menu = QMenu(self)
        mb_action = menu.addAction("Lookup on MusicBrainz")
        menu.addSeparator()
        copy_title = menu.addAction("Copy Title")
        copy_artist = menu.addAction("Copy Artist")
        copy_album = menu.addAction("Copy Album")
        menu.addSeparator()
        open_loc = menu.addAction("Open File Location")
        menu.addSeparator()
        clear_tags = menu.addAction("Clear All Tags")
        delete_file = menu.addAction("Delete File")

        action = menu.exec(self.table.viewport().mapToGlobal(pos))

        if action == mb_action:
            self.mb_artist_input.setText(artist)
            album_text = self.table.item(row, 3).text() if self.table.item(row, 3) else ''
            self.mb_album_input.setText(album_text)
            self._mb_search()
        elif action == copy_title:
            QApplication.clipboard().setText(title)
        elif action == copy_artist:
            QApplication.clipboard().setText(artist)
        elif action == copy_album:
            item3 = self.table.item(row, 3)
            val = item3.text() if item3 else ''
            QApplication.clipboard().setText(val)
        elif action == open_loc:
            if filepath and os.path.exists(filepath):
                subprocess.run(['xdg-open', os.path.dirname(filepath)], check=False)
        elif action == clear_tags:
            if filepath:
                reply = QMessageBox.question(self, "Clear Tags",
                    f"Remove all tags from {os.path.basename(filepath)}?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
                if reply == QMessageBox.StandardButton.Yes:
                    if Tagger.delete_all_tags(filepath):
                        self._update_tag_panel()
                        self.tagger_status_label.setText(f"Cleared tags: {os.path.basename(filepath)}")
                        self.load_files()
                    else:
                        self.tagger_status_label.setText("Failed to clear tags")
        elif action == delete_file:
            if filepath:
                reply = QMessageBox.question(self, "Delete File",
                    f"Permanently delete {os.path.basename(filepath)}?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
                if reply == QMessageBox.StandardButton.Yes:
                    try:
                        os.remove(filepath)
                        self.tagger_status_label.setText(f"Deleted: {os.path.basename(filepath)}")
                        self.load_files()
                    except OSError as e:
                        QMessageBox.warning(self, "Error", f"Delete failed: {e}")

    def _show_extended_tags(self):
        rows = self.table.selectedItems()
        if not rows:
            QMessageBox.warning(self, "Error", "Select a file in the tag table first")
            return
        row = rows[0].row()
        filepath = self.table.item(row, 0).data(Qt.ItemDataRole.UserRole)
        if not filepath:
            return

        tags = Tagger.read_tags(filepath)
        ext_keys = [k for k in EXTENDED_KEYS if k in tags]
        basic_keys = ['title', 'artist', 'album', 'genre', 'year', 'tracknumber']
        all_fields = {k: tags.get(k, '') for k in basic_keys + ext_keys}

        dialog = QDialog(self)
        dialog.setWindowTitle(f"Extended Tags — {os.path.basename(filepath)}")
        dialog.setMinimumWidth(450)
        dlayout = QVBoxLayout(dialog)

        scroll = QListWidget()
        widgets = {}
        for key in all_fields:
            w = QWidget()
            wl = QHBoxLayout(w)
            wl.setContentsMargins(0, 0, 0, 0)
            label = QLabel(key)
            label.setMinimumWidth(100)
            label.setMaximumWidth(120)
            wl.addWidget(label)
            inp = QLineEdit(all_fields[key])
            wl.addWidget(inp)
            item = QListWidgetItem(scroll)
            item.setSizeHint(w.sizeHint())
            scroll.addItem(item)
            scroll.setItemWidget(item, w)
            widgets[key] = inp

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        dlayout.addWidget(scroll)
        dlayout.addWidget(buttons)

        if dialog.exec() == QDialog.DialogCode.Accepted:
            changed = {}
            for key, inp in widgets.items():
                new_val = inp.text()
                if new_val != all_fields[key]:
                    changed[key] = new_val
            if changed:
                if Tagger.write_tags(filepath, changed):
                    self.load_files()
                    self.tagger_status_label.setText(f"Updated {len(changed)} field(s)")
                else:
                    QMessageBox.warning(self, "Error", "Failed to write extended tags")

    def toggle_p2p_connection(self):
        if not self.p2p_manager:
            from core.nicotine_integration import P2PManager
            self.p2p_manager = P2PManager(
                status_callback=lambda msg: self.p2p_status_signal.emit(msg),
                search_callback=lambda r: self.p2p_search_signal.emit(r),
                download_callback=lambda u, f, t: self.p2p_download_signal.emit(u, f, t),
                error_callback=lambda msg: self.p2p_error_signal.emit(msg),
                connection_callback=lambda ok: self.p2p_connected_signal.emit(ok),
                transfer_callback=lambda t, b, p, s: self.p2p_transfer_signal.emit(t, b, p, s),
            )
        if not self.p2p_manager.is_connected:
            user = self.settings_p2p_user.text()
            pwd = self.settings_p2p_pass.text()
            server = self.settings_p2p_server.text()
            try:
                port = int(self.settings_p2p_port.text())
            except (ValueError, TypeError):
                port = 2242
            if not user or not pwd:
                QMessageBox.warning(self, "Error", "Please enter Soulseek username and password in Settings")
                return
            self.connect_btn.setEnabled(False)
            self.connect_btn.setText("Connecting...")
            self.p2p_manager.connect(user, pwd, server, port)
        else:
            self.p2p_manager.disconnect()
            self.connect_btn.setText("Connect")
            self.p2p_status_label.setText("● Disconnected")
            self.p2p_status_label.setStyleSheet("color: #e74c3c; font-weight: bold;")
            self.p2p_user_label.setText("")

    def _on_p2p_connection_result(self, connected):
        if connected:
            self.connect_btn.setText("Disconnect")
            user = self.settings_p2p_user.text() if hasattr(self, 'settings_p2p_user') else ''
            self.p2p_user_label.setText(f"({user})" if user else "")
            self.p2p_status_label.setText("● Connected")
            self.p2p_status_label.setStyleSheet("color: #2ecc71; font-weight: bold;")
        else:
            self.connect_btn.setText("Connect")
            self.p2p_user_label.setText("")
            self.p2p_status_label.setText("● Disconnected")
            self.p2p_status_label.setStyleSheet("color: #e74c3c; font-weight: bold;")
        self.connect_btn.setEnabled(True)

    def p2p_search_files(self):
        query = self.p2p_search.text().strip()
        if not query:
            return
        if not self.p2p_manager or not self.p2p_manager.is_connected:
            QMessageBox.warning(self, "Error", "Please connect to Soulseek first")
            return
        self.p2p_results.setSortingEnabled(False)
        self.p2p_results.setRowCount(0)
        self.p2p_result_count.setText("Searching…")
        self.p2p_manager.search_files(query)
        # Guarantees sorting is restored even when no results ever arrive.
        self._p2p_sort_timer.start()

    def _p2p_clear_results(self):
        self._p2p_sort_timer.stop()
        self.p2p_results.setRowCount(0)
        self.p2p_results.setSortingEnabled(True)
        self.p2p_result_count.setText("0 results")

    def _on_p2p_status(self, message):
        self.p2p_status.append(message)

    def _on_p2p_search_result(self, result):
        row = self.p2p_results.rowCount()
        self.p2p_results.insertRow(row)
        user = result.get('user', '')
        filename = result.get('filename', '')
        size = result.get('size', 0)
        bitrate = result.get('bitrate', 0)
        length = result.get('length', 0)
        ext = result.get('extension', '')
        self.p2p_results.setItem(row, 0, QTableWidgetItem(user))
        self.p2p_results.setItem(row, 1, QTableWidgetItem(filename))
        size_item = QTableWidgetItem()
        size_item.setData(Qt.ItemDataRole.DisplayRole, size)
        size_item.setText(self._format_size(size))
        self.p2p_results.setItem(row, 2, size_item)
        br_item = QTableWidgetItem(str(bitrate) if bitrate else '')
        self.p2p_results.setItem(row, 3, br_item)
        len_item = QTableWidgetItem(self._format_duration(length) if length else '')
        self.p2p_results.setItem(row, 4, len_item)
        self.p2p_results.setItem(row, 5, QTableWidgetItem(ext))
        self.p2p_results.item(row, 0).setData(Qt.ItemDataRole.UserRole, result)
        self.p2p_result_count.setText(f"{self.p2p_results.rowCount()} results")
        # Re-enable sorting only once results stop arriving. Doing it per
        # result lets Qt re-sort mid-batch, so the row we just wrote is
        # no longer at `row` and UserRole ends up on the wrong row.
        self._p2p_sort_timer.start()

    def _p2p_download_selected_row(self, index):
        row = index.row()
        item = self.p2p_results.item(row, 0)
        if not item:
            return
        result = item.data(Qt.ItemDataRole.UserRole)
        if result:
            self._p2p_start_download(result)

    def _p2p_start_download(self, result):
        username = result.get('user', '')
        filename = result.get('filename', '')
        if not username or not filename:
            return
        if not self.p2p_manager or not self.p2p_manager.is_connected:
            QMessageBox.warning(self, "Error", "Please connect to Soulseek first")
            return
        self.p2p_manager.download_file(
            username, filename, config.DOWNLOAD_DIR, size=result.get('size', 0))

    def _p2p_results_context_menu(self, pos):
        row = self.p2p_results.rowAt(pos.y())
        if row < 0:
            return
        item = self.p2p_results.item(row, 0)
        if not item:
            return
        menu = QMenu(self)
        dl_action = menu.addAction("Download")
        copy_user = menu.addAction("Copy Username")
        action = menu.exec(self.p2p_results.viewport().mapToGlobal(pos))
        result = item.data(Qt.ItemDataRole.UserRole) or {}
        if action == dl_action:
            self._p2p_start_download(result)
        elif action == copy_user:
            QApplication.clipboard().setText(result.get('user', ''))

    def _on_p2p_download(self, username, filename, ticket):
        row = self.p2p_transfers.rowCount()
        self.p2p_transfers.insertRow(row)
        self.p2p_transfers.setItem(row, 0, QTableWidgetItem(username))
        self.p2p_transfers.setItem(row, 1, QTableWidgetItem(os.path.basename(filename)))
        self.p2p_transfers.setItem(row, 2, QTableWidgetItem("Queued"))
        self.p2p_transfers.setItem(row, 3, QTableWidgetItem(""))
        self.p2p_transfers.setItem(row, 4, QTableWidgetItem(filename))
        self._p2p_transfer_rows[ticket] = row
        self._p2p_expected_sizes[ticket] = self._p2p_transfer_size(ticket)
        self._dl_add_p2p_row(username, filename, ticket)

    def _on_p2p_transfer(self, ticket, downloaded, path, status):
        self._dl_update_p2p_row(ticket, downloaded, path, status)
        row = self._p2p_transfer_rows.get(ticket)
        if row is None:
            return
        status_item = self.p2p_transfers.item(row, 2)
        prog_item = self.p2p_transfers.item(row, 3)
        path_item = self.p2p_transfers.item(row, 4)
        if status_item:
            status_item.setText(status.capitalize())
        if prog_item and downloaded:
            total = self._p2p_expected_sizes.get(ticket, 0)
            if total:
                percent = min(100, int(downloaded * 100 / total))
                prog_item.setText(f"{percent}% ({self._format_size(downloaded)})")
            else:
                prog_item.setText(self._format_size(downloaded))
        if path_item and path:
            path_item.setText(path)
        if status.lower() in ('complete', 'failed', 'cancelled'):
            # Drop the bookkeeping for finished transfers so these dicts do
            # not grow for the whole session.
            self._p2p_transfer_rows.pop(ticket, None)
            self._p2p_expected_sizes.pop(ticket, None)

    def _p2p_transfer_size(self, ticket):
        """Look up the expected size for a transfer ticket."""
        if self.p2p_manager:
            for entry in self.p2p_manager.downloads:
                if entry.get("ticket") == ticket:
                    return entry.get("size", 0)
        return 0

    _P2P_DISCONNECT_ERRORS = (
        "Connection lost",
        "Logged in",
        "Connection failed",
        "Failed to connect",
        "Login rejected",
    )

    def _on_p2p_error(self, message):
        self.p2p_status.append(f"ERROR: {message}")
        # A failed initial connect never reaches on_connection(False), so the
        # UI must be reset here or Connect stays disabled until restart.
        if any(text in message for text in self._P2P_DISCONNECT_ERRORS):
            self.p2p_status_label.setText("● Disconnected")
            self.p2p_status_label.setStyleSheet("color: #e74c3c; font-weight: bold;")
            self.connect_btn.setText("Connect")
            self.connect_btn.setEnabled(True)
            self.p2p_user_label.setText("")

    @staticmethod
    def _format_duration(seconds):
        if seconds <= 0:
            return ''
        mins, secs = divmod(int(seconds), 60)
        return f"{mins}:{secs:02d}"

    @staticmethod
    def _format_size(bytes_val):
        if bytes_val >= 1_000_000_000:
            return f"{bytes_val / 1_000_000_000:.1f} GB"
        if bytes_val >= 1_000_000:
            return f"{bytes_val / 1_000_000:.1f} MB"
        if bytes_val >= 1_000:
            return f"{bytes_val / 1_000:.1f} KB"
        return f"{bytes_val} B"

    def browse_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Select Download Folder", config.DOWNLOAD_DIR)
        if folder:
            self.settings_folder.setText(folder)

    def save_settings(self):
        """Validate and persist settings. Returns True on success."""
        folder = self.settings_folder.text().strip()
        if not folder:
            QMessageBox.warning(self, "Error", "Download folder cannot be empty")
            return False
        if not os.path.isabs(os.path.expanduser(folder)):
            folder = os.path.join(os.path.expanduser('~'), folder)
        try:
            os.makedirs(folder, exist_ok=True)
        except OSError as e:
            QMessageBox.warning(self, "Error", f"Cannot use download folder:\n{e}")
            return False
        if not os.access(folder, os.W_OK):
            QMessageBox.warning(self, "Error", "Download folder is not writable")
            return False

        port_text = self.settings_p2p_port.text().strip()
        try:
            port = int(port_text)
            if not 1 <= port <= 65535:
                raise ValueError
        except (ValueError, TypeError):
            QMessageBox.warning(
                self, "Error",
                f"'{port_text}' is not a valid port number (1-65535).\nKeeping {config.P2P_PORT}.")
            self.settings_p2p_port.setText(str(config.P2P_PORT))
            port = config.P2P_PORT

        server = self.settings_p2p_server.text().strip()
        if self.settings_p2p_user.text().strip() and not server:
            QMessageBox.warning(self, "Error", "Soulseek server cannot be empty when a username is set")
            return False

        theme = self.settings_theme.currentText()
        if theme not in get_theme_names():
            QMessageBox.warning(self, "Error", f"Unknown theme '{theme}'")
            return False

        config.DOWNLOAD_DIR = folder
        config.COOKIES_BROWSER = self.settings_cookies.currentText()
        self.cookies_combo.setCurrentText(config.COOKIES_BROWSER)
        config.THEME = theme
        config.EMBED_METADATA = self.settings_metadata.isChecked()
        config.EMBED_LYRICS = self.settings_lyrics.isChecked()
        config.CLOSE_ACTION = self.settings_close_action.currentText()
        config.P2P_USER = self.settings_p2p_user.text().strip()
        config.P2P_PASS = self.settings_p2p_pass.text()
        config.P2P_SERVER = server or 'server.slsknet.org'
        config.P2P_PORT = port

        try:
            config.save()
        except OSError as e:
            QMessageBox.warning(self, "Error", f"Failed to save settings: {e}")
            return False

        # Keep the running app consistent with what was just saved.
        self.settings_folder.setText(folder)
        self.apply_theme(theme)
        self._dl_refresh_summary()
        logger.info("Settings saved to %s", folder)
        QMessageBox.information(self, "Settings", "Settings saved successfully!")
        return True
