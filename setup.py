from setuptools import setup, find_namespace_packages

setup(
    name="nexusaudio",
    version="1.0",
    description="PyQt6 GUI: Spotify playlist downloader, audio tag editor, Soulseek P2P",
    license="MIT",
    python_requires=">=3.10",
    install_requires=[
        "yt-dlp>=2024.12.0",
        "mutagen>=1.47.0",
        "PyQt6>=6.7.0",
        "requests>=2.32.0",
        "musicbrainzngs>=0.7.1",
    ],
    extras_require={
        "spotify": ["spotipy>=2.25.0"],
    },
    entry_points={
        "console_scripts": [
            "nexusaudio=main:main",
        ],
    },
    packages=find_namespace_packages(include=["core", "core.*", "gui", "gui.*"]),
    py_modules=["config", "main"],
    include_package_data=True,
)
