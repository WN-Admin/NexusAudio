#!/usr/bin/env python3
import os
import sys
import shutil
import logging
from logging.handlers import RotatingFileHandler
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QApplication

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from gui.main_window import MainWindow

logger = logging.getLogger(__name__)

LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
LOG_FILE = os.path.join(LOG_DIR, "nexusaudio.log")


def _setup_logging():
    root = logging.getLogger()
    root.setLevel(logging.DEBUG)

    fmt = logging.Formatter('%(asctime)s [%(levelname)s] %(name)s: %(message)s')

    console = logging.StreamHandler()
    console.setLevel(logging.INFO)
    console.setFormatter(fmt)
    root.addHandler(console)

    # A read-only install directory must not stop the app from starting;
    # fall back to console-only logging.
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        file_handler = RotatingFileHandler(
            LOG_FILE, maxBytes=5*1024*1024, backupCount=3, delay=True)
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(fmt)
        root.addHandler(file_handler)
    except OSError as e:
        logger.warning("File logging disabled (%s): %s", LOG_DIR, e)
        return

    logger.info("Logging to %s", LOG_FILE)


def _check_ffmpeg():
    if shutil.which('ffmpeg'):
        return
    logger.warning("FFmpeg not found on PATH. Install it for audio conversion.")


def main():
    _setup_logging()
    _check_ffmpeg()

    app = QApplication(sys.argv)
    app.setApplicationName("NexusAudio")
    app.setApplicationVersion("1.0")
    icon_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "nexusaudio.png")
    if os.path.exists(icon_path):
        app_icon = QIcon(icon_path)
        app.setWindowIcon(app_icon)

    logger.info("Creating MainWindow...")
    try:
        window = MainWindow()
    except Exception as e:
        logger.exception("Failed to start NexusAudio")
        from PyQt6.QtWidgets import QMessageBox
        QMessageBox.critical(None, "NexusAudio", f"Failed to start:\n\n{e}")
        sys.exit(1)
    if window.tray_available():
        app.setQuitOnLastWindowClosed(False)
    window.show()
    window.raise_()
    window.activateWindow()

    logger.info("Started")
    ret = app.exec()
    logger.info("Event loop ended")
    sys.exit(ret)


if __name__ == '__main__':
    main()
