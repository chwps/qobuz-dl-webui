# Usa una versione ufficiale e leggera di Python 3.11
FROM python:3.11-slim

# Installa FFmpeg (fondamentale per la tua Ultimate Edition)
RUN apt-get update && \
    apt-get install -y ffmpeg && \
    rm -rf /var/lib/apt/lists/*

# Imposta la cartella di lavoro dentro il contenitore
WORKDIR /app

# Default environment variables for sync-watch mode
# SYNC_INTERVAL: seconds between sync runs (default: 21600 = 6 hours)
# SYNC_PLAYLISTS: Qobuz playlist URLs separated by ';'
# SYNC_DIR: override download directory
# SYNC_YES: skip confirmation prompts (true/false)
ENV SYNC_INTERVAL=21600
ENV SYNC_PLAYLISTS=
ENV SYNC_DIR=
ENV SYNC_YES=false

# Copia tutti i file del tuo progetto dentro il contenitore
COPY . .

# Installa le dipendenze e il programma stesso
RUN pip install --no-cache-dir -r requirements.txt || pip install --no-cache-dir .

# Dichiara il comando di base (cosi l'utente deve solo passare gli argomenti come 'dl' o '--sync-db')
ENTRYPOINT ["python", "-m", "qobuz_dl"]

# Healthcheck for long-running sync-watch containers
HEALTHCHECK --interval=60m --timeout=5m --start-period=10s --retries=3 \
    CMD python -c "import os; assert int(os.environ.get('SYNC_INTERVAL', '0')) > 0 or os.environ.get('SYNC_PLAYLISTS', '') == '', 'Config check'" || exit 0
