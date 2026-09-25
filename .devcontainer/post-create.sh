#!/usr/bin/env bash
set -euo pipefail

echo "==> Dev container post-create"

# Install Python deps (prefer uv when available)
if command -v uv >/dev/null 2>&1; then
  if [[ -f "uv.lock" ]]; then
    echo "==> Syncing Python deps with uv"
    uv sync
  elif [[ -f "pyproject.toml" ]]; then
    echo "==> Syncing Python project with uv"
    uv sync
  elif [[ -f "requirements.txt" ]]; then
    echo "==> Installing Python requirements with uv"
    uv pip install -r requirements.txt
  fi
elif [[ -f "requirements.txt" ]]; then
  echo "==> Installing Python requirements"
  pip install -r requirements.txt
elif [[ -f "pyproject.toml" ]]; then
  echo "==> Installing Python project (editable)"
  pip install -e ".[dev]" 2>/dev/null || pip install -e .
fi

# Install Node deps when a package manifest appears
if [[ -f "package-lock.json" ]]; then
  echo "==> Installing npm dependencies (ci)"
  npm ci
elif [[ -f "package.json" ]]; then
  echo "==> Installing npm dependencies"
  npm install
elif [[ -f "pnpm-lock.yaml" ]]; then
  echo "==> Installing pnpm dependencies"
  corepack enable
  pnpm install --frozen-lockfile
elif [[ -f "yarn.lock" ]]; then
  echo "==> Installing yarn dependencies"
  corepack enable
  yarn install --frozen-lockfile
fi

echo "==> Dev container ready"
