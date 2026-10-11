#!/bin/bash
set -e

echo "NexusAudio Uninstaller"
echo "====================="

cd "$(dirname "$0")"

# --- pip installed ---
if command -v nexusaudio &>/dev/null; then
    PIP_PATH=$(which nexusaudio)
    echo "[1/3] Removing pip-installed package…"
    pip uninstall nexusaudio -y 2>/dev/null || pip3 uninstall nexusaudio -y 2>/dev/null || true
else
    echo "[1/3] No pip-installed package found."
fi

# --- auto-venv ---
if [ -d venv ]; then
    echo "[2/3] Removing virtual environment…"
    rm -rf venv
else
    echo "[2/3] No virtual environment found."
fi

# --- config ---
CONFIG_DIR="$HOME/.config/nexusaudio"
if [ -d "$CONFIG_DIR" ]; then
    echo -n "[3/3] Remove user config ($CONFIG_DIR)? [y/N] "
    read -r CONFIRM
    if [ "$CONFIRM" = "y" ] || [ "$CONFIRM" = "Y" ]; then
        rm -rf "$CONFIG_DIR"
        echo "      Config removed."
    else
        echo "      Skipped."
    fi
else
    echo "[3/3] No user config found."
fi

echo ""
echo "Done. NexusAudio has been uninstalled."
