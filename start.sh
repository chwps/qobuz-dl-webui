#!/bin/bash
set -e

cd /root/workspace/qobuz-dl-webui

# Create venv if needed
if [ ! -d ".venv" ]; then
  uv venv .venv
fi

source .venv/bin/activate

# Install dependencies
uv pip install -r requirements.txt

# Install qobuz-dl if not already installed
if ! .venv/bin/qobuz-dl --version &>/dev/null; then
  echo "Installing qobuz-dl..."
  uv pip install -e /root/workspace/qobuz-dl
fi

echo "Starting Qobuz-DL WebUI..."
echo "URL: http://localhost:8080"
echo ""

# Start the server
uvicorn main:app --host 0.0.0.0 --port 8080
