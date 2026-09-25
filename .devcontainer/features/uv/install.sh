#!/usr/bin/env bash
set -euo pipefail

VERSION="${VERSION:-latest}"
INSTALL_DIR="/usr/local/bin"

echo "Installing uv (version: ${VERSION})..."

# Ensure curl and ca-certificates are available
if ! command -v curl >/dev/null 2>&1 || [[ ! -r /etc/ssl/certs/ca-certificates.crt ]]; then
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -y
  apt-get install -y --no-install-recommends curl ca-certificates
  apt-get clean
  rm -rf /var/lib/apt/lists/*
fi

# Unmanaged install: place binaries in PATH without modifying shell profiles
if [[ "${VERSION}" == "latest" ]]; then
  curl -LsSf https://astral.sh/uv/install.sh \
    | env UV_UNMANAGED_INSTALL="${INSTALL_DIR}" sh
else
  curl -LsSf "https://astral.sh/uv/${VERSION}/install.sh" \
    | env UV_UNMANAGED_INSTALL="${INSTALL_DIR}" sh
fi

chmod 755 "${INSTALL_DIR}/uv" "${INSTALL_DIR}/uvx"

uv --version
echo "uv installed to ${INSTALL_DIR}"
