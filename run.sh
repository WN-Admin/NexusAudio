#!/bin/bash
set -e
cd "$(dirname "$0")"

# --- Docker mode ---
if [ "$1" = "--docker" ] || [ "$1" = "-d" ]; then
    if command -v docker &>/dev/null; then
        echo "Building Docker image…"
        docker compose build --quiet 2>/dev/null || docker compose build
        exec docker compose up
    else
        echo "Docker not found. Install Docker or run without --docker." >&2
        exit 1
    fi
fi

# --- Foreground mode (blocks terminal; default detaches) ---
FOREGROUND=false
if [ "$1" = "--foreground" ] || [ "$1" = "-f" ]; then
    FOREGROUND=true
    shift
elif [ "$1" = "--background" ] || [ "$1" = "-b" ]; then
    shift
fi

# --- Auto-venv ---
if [ ! -d venv ]; then
    echo "Creating virtual environment…"
    python3 -m venv venv
fi
source venv/bin/activate

if [ ! -f venv/ok ]; then
    echo "Installing dependencies…"
    pip install -e . 1>/dev/null
    touch venv/ok
fi

# --- Platform detection ---
if [ -z "$DISPLAY" ] || [ "$XDG_SESSION_TYPE" = "wayland" ]; then
    export QT_QPA_PLATFORM=xcb
fi

if [ "$FOREGROUND" = true ]; then
    exec python main.py "$@"
fi

LOGFILE="logs/nexusaudio.log"
mkdir -p logs
nohup python main.py "$@" >> "$LOGFILE" 2>&1 &
PID=$!
echo "$PID" > logs/nexusaudio.pid
echo "NexusAudio started (PID $PID)"
echo "Log: $LOGFILE"
echo "Stop: kill \$(cat logs/nexusaudio.pid)"
