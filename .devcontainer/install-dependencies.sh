#!/usr/bin/env bash
# Installs the system and Python dependencies for this dev container.
#
# devcontainer.json runs this script with `bash`, and `set -e` stops at the
# first failing command, so a failed install cannot be hidden by a later one.
set -euo pipefail

cd "$(dirname "$0")/.."

if [ -f packages.txt ]; then
  echo "Installing system packages from packages.txt..."
  sudo apt-get update
  sudo xargs -r apt-get install -y --no-install-recommends < packages.txt
fi

if [ -f requirements.txt ]; then
  echo "Installing Python packages from requirements.txt..."
  python3 -m pip install --user -r requirements.txt
fi

echo "✅ Dependencies installed"
