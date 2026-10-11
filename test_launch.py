#!/usr/bin/env python3
import sys
import os

os.environ['QT_QPA_PLATFORM'] = 'xcb'

from PyQt6.QtWidgets import QApplication, QLabel, QMainWindow

app = QApplication(sys.argv)
print('QApplication created')

window = QMainWindow()
window.setWindowTitle("Test Window")
label = QLabel("If you see this, PyQt6 works!")
window.setCentralWidget(label)
print('Window created')

window.show()
print('Window shown - you should see it now')

app.exec()
print('Event loop ended')
