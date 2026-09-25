FROM mcr.microsoft.com/devcontainers/python:1-3.12-bookworm

# System packages useful for AI / audio / media workflows.
# Base image ships a yarn apt source with a rotated/missing GPG key; drop it
# before apt-get update (Node feature installs yarn without that apt repo).
RUN rm -f /etc/apt/sources.list.d/yarn.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends \
        ffmpeg \
        libsndfile1 \
        curl \
        jq \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Keep pip/tooling current inside the image
RUN python -m pip install --upgrade pip setuptools wheel
