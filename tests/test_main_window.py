import os
import sys
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PyQt6.QtWidgets import QApplication, QProgressBar, QMessageBox
from PyQt6.QtCore import Qt, QEventLoop, QTimer

_app = None


def setUpModule():
    global _app
    _app = QApplication.instance() or QApplication(sys.argv)


def _make_window():
    from gui.main_window import MainWindow
    return MainWindow()


def _pump(ms=0):
    """Spin the event loop briefly so queued signals are delivered."""
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


class TestDownloadProgressSignal(unittest.TestCase):
    """progress_signal must carry the float percentages the downloader emits.

    PyQt6 does not coerce float->int for an int signal; it forwards an
    uninitialized value, so an int signal yielded garbage like 428980688.
    """

    def setUp(self):
        self.win = _make_window()

    def tearDown(self):
        self.win.dl_table.setRowCount(0)
        self.win.deleteLater()

    def _emit(self, event, name='Song', artist='Artist', platform='youtube'):
        self.win.track_event_signal.emit(event, 1, 1, name, artist, platform)
        _pump()

    def test_signal_is_float(self):
        from gui.main_window import MainWindow
        self.assertEqual(MainWindow.progress_signal, MainWindow.progress_signal)
        # Emitting a float must arrive intact, not as a truncated int.
        got = []
        self.win.progress_signal.connect(got.append)
        self.win.progress_signal.emit(55.7)
        self.assertEqual(got[-1], 55.7)

    def test_progress_updates_per_track_bar(self):
        self._emit('queued')
        self._emit('start')
        row = self.win._dl_active_row
        self.assertIsNotNone(row)
        for pct in (0.0, 12.5, 50.0, 99.0):
            self.win.progress_signal.emit(pct)
            _pump()
            bar = self.win.dl_table.cellWidget(row, 4)
            self.assertIsInstance(bar, QProgressBar)
            self.assertEqual(bar.value(), int(pct))
            self.assertEqual(bar.value(), int(bar.value()))

    def test_progress_out_of_range_is_clamped(self):
        self._emit('queued')
        self._emit('start')
        row = self.win._dl_active_row
        self.win.progress_signal.emit(140.0)
        _pump()
        bar = self.win.dl_table.cellWidget(row, 4)
        self.assertLessEqual(bar.value(), 100)
        self.assertGreaterEqual(bar.value(), 0)

    def test_bar_value_is_never_huge_garbage(self):
        """Guards the original regression: an int signal produced garbage."""
        self._emit('queued')
        self._emit('start')
        row = self.win._dl_active_row
        self.win.progress_signal.emit(42.9)
        _pump()
        value = self.win.dl_table.cellWidget(row, 4).value()
        self.assertLessEqual(value, 100, "progress bar got an unconverted value")
        self.assertGreaterEqual(value, 40)


class TestTrackEventGuards(unittest.TestCase):
    def setUp(self):
        self.win = _make_window()

    def tearDown(self):
        self.win.dl_table.setRowCount(0)
        self.win.deleteLater()

    def test_start_without_pending_rows_does_not_crash(self):
        """A 'start' with no active row used to index item(None, 1)."""
        self.win._dl_pending_rows = []
        self.win._dl_active_row = None
        self.win.track_event_signal.emit('start', 1, 1, 'S', 'A', 'youtube')
        _pump()

    def test_done_without_active_row_does_not_crash(self):
        self.win._dl_active_row = None
        self.win.track_event_signal.emit('done', 1, 1, 'S', 'A', 'youtube')
        _pump()

    def test_queued_then_start_marks_downloading(self):
        self.win.track_event_signal.emit('queued', 1, 1, 'S', 'A', 'youtube')
        self.win.track_event_signal.emit('start', 1, 1, 'S', 'A', 'youtube')
        _pump()
        row = self.win._dl_active_row
        self.assertEqual(self.win.dl_table.item(row, 5).text(), 'Downloading')


class TestCancelledRowsAreTerminal(unittest.TestCase):
    def setUp(self):
        self.win = _make_window()

    def tearDown(self):
        self.win.dl_table.setRowCount(0)
        self.win.deleteLater()

    def test_cancelled_is_not_counted_active(self):
        row = self.win._dl_new_row('T', 'A', 'YT', status='Queued')
        self.win._dl_set_row(row, status='Cancelled')
        self.win._dl_refresh_summary()
        self.assertNotIn('active', self.win.dl_summary.text())
        self.assertIn('1 finished', self.win.dl_summary.text())
        self.assertFalse(self.win.dl_cancel_btn.isEnabled())

    def test_clear_finished_removes_cancelled(self):
        keep = self.win._dl_new_row('Keep', 'A', 'YT', status='Queued')
        gone = self.win._dl_new_row('Gone', 'B', 'YT', status='Cancelled')
        self.win._dl_clear_finished()
        self.assertEqual(self.win.dl_table.rowCount(), 1)
        self.assertEqual(self.win.dl_table.item(0, 1).text(), 'Keep')


class TestCancelReporting(unittest.TestCase):
    def setUp(self):
        self.win = _make_window()

    def tearDown(self):
        self.win.dl_table.setRowCount(0)
        self.win.deleteLater()

    def test_cancelled_queue_does_not_report_complete(self):
        self.win.download_done_signal.emit()
        self.assertEqual(self.win.status_label.text(), 'Download complete!')

    def test_after_cancel_reports_cancelled(self):
        self.win._dl_new_row('T', 'A', 'YT', status='Queued')
        self.win.cancel_download()
        self.assertTrue(self.win._cancel_requested)
        self.assertEqual(self.win.status_label.text(), 'Cancelled')
        # The queue watcher then fires; it must not claim success.
        self.win.download_done_signal.emit()
        self.assertEqual(self.win.status_label.text(), 'Cancelled')

    def test_cancel_download_clears_pending_rows(self):
        self.win.track_event_signal.emit('queued', 1, 2, 'S', 'A', 'youtube')
        self.win.track_event_signal.emit('queued', 2, 2, 'T', 'B', 'youtube')
        _pump()
        self.assertEqual(len(self.win._dl_pending_rows), 2)
        self.win.cancel_download()
        self.assertEqual(self.win._dl_pending_rows, [])
        self.assertIsNone(self.win._dl_active_row)


class TestP2PSorting(unittest.TestCase):
    def setUp(self):
        self.win = _make_window()

    def tearDown(self):
        self.win.deleteLater()

    def test_sorting_restored_after_results_arrive(self):
        self.win.p2p_results.setSortingEnabled(False)
        self.win._on_p2p_search_result(
            {'user': 'a', 'filename': 'a.mp3', 'size': 1})
        self.assertFalse(self.win.p2p_results.isSortingEnabled())
        self.win._p2p_sort_timer.start()
        _pump(600)
        self.assertTrue(self.win.p2p_results.isSortingEnabled())

    def test_sorting_restored_after_empty_search(self):
        """A search with zero results must not leave sorting dead forever."""
        self.win.p2p_results.setSortingEnabled(True)
        self.win.p2p_results.setSortingEnabled(False)
        self.win.p2p_manager = None  # search_files() returns before network
        self.win._p2p_sort_timer.start()
        _pump(600)
        self.assertTrue(self.win.p2p_results.isSortingEnabled())

    def test_row_payload_matches_displayed_filename(self):
        results = [
            {'user': 'mike', 'filename': 'mike.mp3', 'size': 900},
            {'user': 'alice', 'filename': 'alice.mp3', 'size': 100},
            {'user': 'zoe', 'filename': 'zoe.mp3', 'size': 500},
        ]
        self.win.p2p_results.setSortingEnabled(False)
        for r in results:
            self.win._on_p2p_search_result(r)
        for row in range(self.win.p2p_results.rowCount()):
            item = self.win.p2p_results.item(row, 0)
            payload = item.data(Qt.ItemDataRole.UserRole)
            self.assertEqual(item.text(), payload['user'])
            self.assertEqual(
                self.win.p2p_results.item(row, 1).text(), payload['filename'])


class TestLyricsSnapshot(unittest.TestCase):
    """The lyrics worker must not touch widgets off the GUI thread."""

    def setUp(self):
        self.win = _make_window()

    def tearDown(self):
        self.win.table.setRowCount(0)
        self.win.deleteLater()

    def test_worker_uses_snapshot_not_live_table(self):
        from PyQt6.QtWidgets import QTableWidgetItem
        self.win.table.setRowCount(1)
        self.win.table.setItem(0, 0, QTableWidgetItem('/tmp/x.mp3'))
        self.win.table.item(0, 0).setData(Qt.ItemDataRole.UserRole, '/tmp/x.mp3')
        self.win.table.setItem(0, 1, QTableWidgetItem('Title'))
        self.win.table.setItem(0, 2, QTableWidgetItem('Artist'))

        captured = {}

        def fake_fetch(title, artist):
            captured['title'] = title
            captured['artist'] = artist
            # Simulate load_files() clearing the table mid-run (F5 / download).
            self.win.table.setRowCount(0)
            return ''

        import gui.main_window as mw
        original = mw.fetch_lyrics
        mw.fetch_lyrics = fake_fetch
        try:
            done = []
            self.win.lyrics_done_signal.connect(
                lambda s, t: done.append((s, t)))
            self.win.fetch_lyrics_for_all()
            _pump(80)
        finally:
            mw.fetch_lyrics = original
        # The worker read the snapshot, not the (now cleared) live table.
        self.assertEqual(captured.get('title'), 'Title')
        self.assertEqual(captured.get('artist'), 'Artist')
        self.assertEqual(len(done), 1, "lyrics_done_signal never fired")
        self.assertEqual(done[0][1], 1)

    def test_empty_table_warns_instead_of_crashing(self):
        self.win.table.setRowCount(0)
        with patch.object(QMessageBox, 'warning') as warn:
            self.win.fetch_lyrics_for_all()  # must not raise
        self.assertTrue(warn.called)


if __name__ == '__main__':
    unittest.main()