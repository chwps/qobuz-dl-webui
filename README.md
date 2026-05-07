# Qobuz-DL WebUI

Interface web pour contrôler **qobuz-dl** — téléchargez des albums, tracks et playlists Qobuz depuis un navigateur.

## Features

- **Dashboard complet** avec toutes les commandes `qobuz-dl` organisées par catégorie
- **Streaming SSE** — logs en temps réel pendant les téléchargements
- **Prévisualisation** des commandes avant exécution
- **Thème dark** avec design audiophile
- **Pas de build JS** — vanilla HTML/CSS/JS, zéro dépendance frontend

## Commandes supportées

| Section | Commandes |
|---------|-----------|
| **Download** | `download <URL>` avec options qualité, format, sortie, tags |
| **Album** | `info`, `bestquality`, `download` |
| **Track** | `info`, `download` |
| **Playlist** | `info`, `download` |
| **Search** | `search --keyword` |
| **Sync** | `sync-artist`, `sync-playlist`, `sync-album` |
| **Lucky** | `lucky <QUERY>` — recherche rapide + download |
| **Lyrics** | `lyrics <DIRECTORY>` — sync les paroles |
| **Config** | `show`, `download`, `edit` |
| **Qobuz App** | `extract`, `user`, `refresh`, `clear` |
| **Sync-DB** | `sync-db` — sync la base de données |

## Installation

### Méthode 1 — Python direct

```bash
# Cloner le repo
git clone https://github.com/Sei969/qobuz-dl.git
cd qobuz-dl-webui

# Installer les dépendances
bash start.sh
```

### Méthode 2 — Docker

```bash
docker build -t qobuz-dl-webui .
docker run -p 8080:8080 \
  -v ~/.config/qobuz-dl:/root/.config/qobuz-dl \
  -v ./downloads:/app/downloads \
  qobuz-dl-webui
```

### Variables d'environnement

| Variable | Défaut | Description |
|----------|--------|-------------|
| `PORT` | `8080` | Port HTTP du serveur |
| `QOBUZ_DL_BIN` | `.venv/bin/qobuz-dl` | Chemin vers le binaire qobuz-dl |
| `QOBUZ_DL_DIR` | `.` | Working directory pour les commandes |

## Usage

1. Ouvrez `http://localhost:8080`
2. Naviguez entre les sections via la sidebar
3. Remplissez les champs et cliquez pour exécuter
4. Les résultats s'affichent en streaming en temps réel

### Sections détaillées

#### ⬇️ Download
Téléchargez depuis une URL Qobuz. Options de qualité (SQR à Hi-Res), format (FLAC, MP3, AAC, etc.), gestion des tags.

#### 💿 Album
Infos détaillées, meilleure qualité disponible, téléchargement par ID.

#### 🎵 Track
Infos et téléchargement d'une piste individuelle.

#### 📃 Playlist
Infos sur la playlist, liste des pistes, téléchargement complet.

#### 🔍 Search
Recherche Qobuz par mot-clé avec limite de résultats.

#### 🔄 Sync
- **sync-artist** : sync la discographie complète d'un artiste avec filtrage smart
- **sync-playlist** : sync incrémental d'une playlist locale
- **sync-album** : sync un album avec mise à jour incrémentale

#### 🍀 Lucky
Recherche rapide et téléchargement du meilleur résultat. Pratique pour obtenir un album/track sans connaître l'ID.

#### 🎤 Lyrics
Sync les paroles (lyrics) pour les fichiers audio existants dans un répertoire.

#### ⚙️ Config
Voir, télécharger ou éditer la configuration de qobuz-dl.

#### 🔑 Qobuz App
Gérer les credentials : extraction, infos utilisateur, refresh, clear.

#### 💾 Sync-DB
Synchroniser la base de données locale avec les données Qobuz.

## Architecture

```
┌─────────────┐    HTTP/SSE     ┌──────────────────┐
│   Browser    │ ◄─────────────► │   FastAPI Server  │
│              │                 │   (main.py)       │
│  index.html  │                 └────────┬─────────┘
└─────────────┘                          │ subprocess
                                         ▼
                                  ┌──────────────┐
                                  │  qobuz-dl CLI │
                                  └──────────────┘
```

## Licence

Voir le repo qobuz-dl principal.
