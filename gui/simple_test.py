#!/usr/bin/env python3
import sys
from PyQt6.QtWidgets import QApplication, QMainWindow, QLabel, QTabWidget, QWidget, QVBoxLayout

class SimpleWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("NexusAudio Test")
        self.setGeometry(100, 100, 800, 600)
        
        tabs = QTabWidget()
        self.setCentralWidget(tabs)
        
        tab1 = QWidget()
        layout1 = QVBoxLayout()
        layout1.addWidget(QLabel("Downloader Tab"))
        tab1.setLayout(layout1)
        tabs.addTab(tab1, "Downloader")
        
        tab2 = QWidget()
        layout2 = QVBoxLayout()
        layout2.addWidget(QLabel("Tag Editor Tab"))
        tab2.setLayout(layout2)
        tabs.addTab(tab2, "Tag Editor")
        
        print("Window created successfully!")

if __name__ == '__main__':
    app = QApplication(sys.argv)
    window = SimpleWindow()
    window.show()
    print("Window shown - starting event loop")
    sys.exit(app.exec())
