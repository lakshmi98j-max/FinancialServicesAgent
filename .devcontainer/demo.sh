#!/usr/bin/env bash
# Starts the demo in a codespace and prints the address to open. The localhost, network and external URLs that
# Streamlit prints are internal to the codespace and do not open from your browser.
set -euo pipefail
cd "$(dirname "$0")/.."
if [ -n "${CODESPACE_NAME:-}" ]; then
  echo
  echo "Open the demo at: https://${CODESPACE_NAME}-8501.${GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN:-app.github.dev}"
  echo "You can also open it from the Ports tab. Ignore the URLs Streamlit prints below."
  echo
fi
exec python3 -m streamlit run ui/app.py --server.headless true --server.enableCORS false --server.enableXsrfProtection false
