#!/usr/bin/env bash
# Starts the Streamlit app with its browser security protections left on.
#
# Streamlit only accepts WebSocket connections from origins it recognises. A
# Codespaces preview serves the app from https://<codespace>-8501.<domain>,
# which is a different origin from the container's localhost, so allow exactly
# that origin instead of disabling CORS and XSRF protection.
set -euo pipefail

cd "$(dirname "$0")/.."

streamlit_args=()

if [ -n "${CODESPACE_NAME:-}" ] && [ -n "${GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN:-}" ]; then
  streamlit_args+=(
    "--server.corsAllowedOrigins=https://${CODESPACE_NAME}-8501.${GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN}"
  )
fi

exec python3 -m streamlit run app.py "${streamlit_args[@]}"
