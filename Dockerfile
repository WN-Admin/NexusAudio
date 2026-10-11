# syntax=docker/dockerfile:1
FROM python:3.12-slim AS base

RUN apt-get update -qq && apt-get install -y -qq --no-install-recommends \
    ffmpeg \
    libxcb-cursor0 \
    libxcb-shape0 \
    libxcb-icccm4 \
    libxcb-image0 \
    libxcb-keysyms1 \
    libxcb-randr0 \
    libxcb-render-util0 \
    libxcb-xinerama0 \
    libxcb-xfixes0 \
    libxkbcommon-x11-0 \
    libegl1 \
    libgl1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY . .

RUN pip install --no-cache-dir -e .

ENTRYPOINT ["nexusaudio"]
