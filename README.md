# Qobuz-DL WebUI

Web interface for [qobuz-dl](https://github.com/Sei969/qobuz-dl) — control all CLI commands from your browser with real-time streaming output.

![Build & Push](https://github.com/chwps/qobuz-dl-webui/actions/workflows/docker-publish.yml/badge.svg)

## Features

- 🎵 **12 command sections** — Download, Album, Track, Playlist, Search, Sync, Lucky, Lyrics, Config, Qobuz App, Sync-DB, Logs
- ⚡ **SSE streaming** — real-time command output directly in the browser
- 🌙 **Dark audiophile theme** — responsive design with a clean, modern interface
- 🐳 **Docker ready** — multi-arch images on GHCR (amd64 + arm64)

## Quick Start

### Docker (recommended)

```bash
docker pull ghcr.io/chwps/qobuz-dl-webui:latest
docker run -d \
  --name qobuz-dl-webui \
  -p 8080:8080 \
  -v ~/.config/qobuz-dl:/root/.config/qobuz-dl \
  -v ~/Downloads/qobuz:/app/downloads \
  ghcr.io/chwps/qobuz-dl-webui:latest
```

Open **http://localhost:8080** in your browser.

### Local

```bash
# Install qobuz-dl
pip install git+https://github.com/Sei969/qobuz-dl.git

# Install WebUI deps
pip install -r requirements.txt

# Run
python main.py
# or
uvicorn main:app --host 0.0.0.0 --port 8080
```

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `PORT` | `8080` | Server port |
| `QOBUZ_DL_BIN` | `qobuz-dl` | Path to qobuz-dl binary |
| `QOBUZ_DL_DIR` | `.` | Working directory for commands |

## Project Structure

```
├── main.py              # FastAPI backend with SSE streaming
├── index.html           # Frontend (vanilla HTML/CSS/JS)
├── Dockerfile           # Multi-stage Docker build
├── requirements.txt     # Python dependencies
├── start.sh             # Local startup script
└── .github/
    └── workflows/
        └── docker-publish.yml  # CI/CD for GHCR
```

## Docker Tags

| Tag | Description |
|-----|-------------|
| `latest` | Latest commit on master |
| `master` | Branch-based tag |
| `<commit-sha>` | Specific commit |

## Docker Compose

```yaml
services:
  qobuz-dl-webui:
    image: ghcr.io/chwps/qobuz-dl-webui:latest
    container_name: qobuz-dl-webui
    ports:
      - "8080:8080"
    volumes:
      - ~/.config/qobuz-dl:/root/.config/qobuz-dl
      - ~/Downloads/qobuz:/app/downloads
    restart: unless-stopped
```

## License

Same as [qobuz-dl](https://github.com/Sei969/qobuz-dl) (GPL-3.0)
