"""
Bidirectional sync between a local folder and a Qobuz playlist.
Uses a Yubal-inspired structure: artist/year - album/NN - track.
Playlist M3U and cover files go into {directory}/_Playlists/.
"""

import os
import re
import logging
from pathlib import Path
from mutagen.flac import FLAC
from mutagen.id3 import ID3

from qobuz_dl.color import CYAN, GREEN, RED, YELLOW, OFF
from qobuz_dl.constants import DEFAULT_PLAYLIST_FOLDER_FORMAT, DEFAULT_PLAYLIST_TRACK_FORMAT
from qobuz_dl.utils import get_url_info

logger = logging.getLogger(__name__)


def _scan_local_tracks(base_directory, exclude_dirs=None):
    """
    Walk the base directory and build a dict of {qobuz_track_id: file_path}
    by reading the QOBUZTRACKID tag from each audio file.
    Excludes _Playlists/ and any dirs in exclude_dirs.
    """
    local_tracks = {}
    untagged_files = []
    exclude = set(exclude_dirs or [])
    exclude.add("_Playlists")

    for root, dirs, files in os.walk(base_directory):
        # Prune excluded directories
        dirs[:] = [d for d in dirs if d not in exclude]

        for fname in files:
            if not fname.lower().endswith(('.flac', '.mp3', '.wav', '.ogg')):
                continue

            fpath = os.path.join(root, fname)
            track_id = None

            try:
                if fpath.lower().endswith('.flac'):
                    audio = FLAC(fpath)
                    track_id = audio.get("QOBUZTRACKID", [None])[0]
                else:
                    try:
                        audio = ID3(fpath)
                        txxx = audio.get("TXXX:QOBUZTRACKID")
                        if txxx:
                            track_id = txxx.text[0]
                    except Exception:
                        pass
            except Exception as e:
                logger.debug(f"Failed to read tags from {fpath}: {e}")

            if track_id:
                local_tracks[str(track_id)] = fpath
            else:
                untagged_files.append(fpath)

    return local_tracks, untagged_files


def _fetch_remote_tracks(client, playlist_id):
    """
    Fetch all tracks from a Qobuz playlist via the paginated API.
    Returns a tuple: (playlist_name, all_items, cover_url)
    """
    all_items = []
    playlist_name = "Unknown Playlist"
    cover_url = None
    for chunk in client.get_plist_meta(playlist_id):
        if "name" in chunk and playlist_name == "Unknown Playlist":
            playlist_name = chunk.get("name")
        if "image" in chunk and not cover_url:
            img = chunk.get("image", {})
            cover_url = img.get("large", img.get("code", ""))
        items = chunk.get("tracks", {}).get("items", [])
        all_items.extend(items)
    return playlist_name, all_items, cover_url


def _sanitize_filename(name):
    """Remove invalid characters for OS filenames and paths."""
    invalid_chars = '<>:"/\\|*?'
    for char in invalid_chars:
        name = name.replace(char, '_')
    return name.strip()


def _clean_empty_dirs(base_directory, exclude_dirs=None):
    """
    Remove empty directories after file deletion.
    Walk bottom-up to remove nested empty dirs.
    Excludes _Playlists/ and any dirs in exclude_dirs.
    """
    exclude = set(exclude_dirs or [])
    exclude.add("_Playlists")

    for root, dirs, files in os.walk(base_directory, topdown=False):
        for d in dirs:
            dir_path = os.path.join(root, d)
            try:
                if d in exclude:
                    continue
                if not os.listdir(dir_path):
                    os.rmdir(dir_path)
                    rel = os.path.relpath(dir_path, base_directory)
                    logger.info(f"  {RED}[-] Removed empty dir: {rel}{OFF}")
            except OSError:
                pass


def _format_path(pattern, context, base_dir):
    """
    Format a path using the given pattern and context dict.
    Sanitizes each path component.
    Returns a full path string.
    """
    from qobuz_dl.utils import PartialFormatter

    fmt = PartialFormatter()
    formatted = fmt.format(pattern, **context)

    # Split by / and sanitize each component
    parts = []
    for part in formatted.split("/"):
        part = _sanitize_filename(part.strip())
        if part:
            parts.append(part)

    if not parts:
        parts = ["Unknown"]

    return os.path.join(base_dir, *parts)


def _download_playlist_cover(base_directory, playlist_name, playlist_id, cover_url):
    """Download playlist cover and save in _Playlists/."""
    import urllib.request
    from urllib.error import HTTPError, URLError

    if not cover_url:
        return None

    playlists_dir = os.path.join(base_directory, "_Playlists")
    os.makedirs(playlists_dir, exist_ok=True)

    safe_name = _sanitize_filename(playlist_name)
    id_suffix = playlist_id[-8:] if len(playlist_id) > 8 else playlist_id
    cover_filename = f"{safe_name} [{id_suffix}].jpg"
    cover_path = os.path.join(playlists_dir, cover_filename)

    try:
        req = urllib.request.Request(
            cover_url,
            headers={"User-Agent": "qobuz-dl/1.0"}
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = resp.read()
        with open(cover_path, "wb") as f:
            f.write(data)
        logger.info(f"  {CYAN}[+] Cover saved: {cover_path}{OFF}")
        return cover_path
    except (HTTPError, URLError, OSError) as e:
        logger.debug(f"Failed to download cover: {e}")
        return None


def sync_playlist(qobuz_dl, url, folder, auto_confirm=False, folder_format=None, track_format=None):
    """
    Main entry point for playlist sync.

    Structure (Yubal-inspired):
      QobuzDownloads/
        |-- _Playlists/
        |     |-- Playlist Name [abc12345].m3u
        |     |-- Playlist Name [abc12345].jpg
        |-- Artist1/
        |     |-- 2024 - Album1/
        |     |     |-- 01 - Track1.flac
        |-- Artist2/
        |     |-- 2019 - Album2/
        |           |-- 01 - Track1.flac

    Args:
        qobuz_dl: The QobuzDL instance
        url: Qobuz playlist URL
        folder: Base download directory
        auto_confirm: Skip confirmation prompt
        folder_format: Override folder format (Yubal-inspired)
        track_format: Override track format (Yubal-inspired)
    """
    from qobuz_dl.utils import make_m3u_playlist

    # Use provided formats or fallback to defaults
    pl_folder_format = folder_format or DEFAULT_PLAYLIST_FOLDER_FORMAT
    pl_track_format = track_format or DEFAULT_PLAYLIST_TRACK_FORMAT

    # --- 1. Parse and validate URL ---
    try:
        url_type, playlist_id = get_url_info(url)
    except (AttributeError, IndexError):
        logger.error(f"{RED}Invalid URL: {url}{OFF}")
        return

    if url_type != "playlist":
        logger.error(
            f"{RED}URL is not a playlist (detected type: '{url_type}'). "
            f"Use a playlist URL like https://play.qobuz.com/playlist/12345{OFF}"
        )
        return

    logger.info(f"\n{YELLOW}━━━ PLAYLIST SYNC ━━━{OFF}")
    logger.info(f"{YELLOW}URL : {url}{OFF}")

    # --- 2. Fetch remote playlist ---
    logger.info(f"{CYAN}[1/5] Fetching playlist from Qobuz...{OFF}")
    playlist_name, remote_items, cover_url = _fetch_remote_tracks(qobuz_dl.client, playlist_id)
    remote_ids = {str(item["id"]): item for item in remote_items}
    logger.info(f"{CYAN}      Found {len(remote_ids)} tracks in the Qobuz playlist.{OFF}")

    if not remote_ids:
        logger.info(f"{YELLOW}The Qobuz playlist is empty. Nothing to sync.{OFF}")
        return

    # --- Base directory & _Playlists ---
    base_directory = folder  # e.g. /app/QobuzDownloads
    playlists_dir = os.path.join(base_directory, "_Playlists")
    os.makedirs(playlists_dir, exist_ok=True)

    # Playlist filename suffix (like Yubal)
    safe_playlist_name = _sanitize_filename(playlist_name)
    id_suffix = playlist_id[-8:] if len(playlist_id) > 8 else playlist_id
    logger.info(f"{YELLOW}PL  : {safe_playlist_name} [{id_suffix}]{OFF}")
    logger.info(f"{YELLOW}DIR : {base_directory}{OFF}\n")

    # --- 3. Scan local tracks across ENTIRE base directory ---
    logger.info(f"{CYAN}[2/5] Scanning local folder...{OFF}")
    local_tracks, untagged = _scan_local_tracks(base_directory)
    logger.info(f"{CYAN}      Found {len(local_tracks)} tagged tracks locally.{OFF}")
    if untagged:
        logger.info(
            f"{YELLOW}      {len(untagged)} files have no QOBUZTRACKID tag and will be ignored.{OFF}"
        )

    # --- 4. Compute diff ---
    local_id_set = set(local_tracks.keys())
    remote_id_set = set(remote_ids.keys())

    to_download_ids = remote_id_set - local_id_set
    to_delete_ids = local_id_set - remote_id_set
    already_synced = local_id_set & remote_id_set

    logger.info(f"\n{CYAN}[3/5] Sync summary:{OFF}")
    logger.info(f"  {GREEN}↓ To download : {len(to_download_ids)} tracks{OFF}")
    logger.info(f"  {RED}✕ To delete   : {len(to_delete_ids)} files{OFF}")
    logger.info(f"    Already synced: {len(already_synced)} tracks")

    if not to_download_ids and not to_delete_ids:
        logger.info(f"\n{GREEN}✓ Folder is already in sync with the playlist!{OFF}")

        # Update M3U anyway (order may have changed)
        _build_m3u(base_directory, playlist_name, playlist_id, remote_items)
        return

    # Print file-level details
    if to_delete_ids:
        logger.info(f"\n{RED}Files to DELETE:{OFF}")
        for tid in sorted(to_delete_ids):
            logger.info(f"  {RED}✕ {os.path.basename(local_tracks[tid])}{OFF}")

    if to_download_ids:
        logger.info(f"\n{GREEN}Tracks to DOWNLOAD:{OFF}")
        for tid in sorted(to_download_ids):
            item = remote_ids[tid]
            album_artist = item.get("album", {}).get("artist", {}).get("name")
            performer_name = item.get("performer", {}).get("name", "Unknown")
            artist = performer_name if album_artist in [None, "Various Artists"] else album_artist
            title = item.get("title", "Unknown")
            logger.info(f"  {GREEN}↓ {artist} — {title}{OFF}")

    # --- Confirmation prompt ---
    if not auto_confirm:
        try:
            answer = input(f"\n{YELLOW}Proceed with sync? [y/N]: {OFF}").strip().lower()
            if answer != 'y':
                logger.info(f"{YELLOW}Sync cancelled by user.{OFF}")
                return
        except (KeyboardInterrupt, EOFError):
            logger.info(f"\n{YELLOW}Sync cancelled.{OFF}")
            return

    # --- 5. Execute sync ---
    logger.info(f"\n{CYAN}[4/5] Executing sync...{OFF}")

    # 5a. Delete stale files
    deleted_count = 0
    for tid in to_delete_ids:
        fpath = local_tracks[tid]
        try:
            os.remove(fpath)
            deleted_count += 1
            logger.info(f"  {RED}[-] Deleted: {os.path.basename(fpath)}{OFF}")

            lrc_path = os.path.splitext(fpath)[0] + ".lrc"
            if os.path.isfile(lrc_path):
                os.remove(lrc_path)
                logger.info(f"  {RED}[-] Deleted: {os.path.basename(lrc_path)}{OFF}")
        except OSError as e:
            logger.error(f"  {RED}[!] Failed to delete {fpath}: {e}{OFF}")

    # Clean up empty directories after deletion
    _clean_empty_dirs(base_directory, exclude_dirs={"_Playlists"})

    # 5b. Download missing tracks using Yubal-inspired folder structure
    original_folder_format = qobuz_dl.folder_format
    original_track_format = qobuz_dl.track_format if hasattr(qobuz_dl, 'track_format') else None
    original_multi_disc = qobuz_dl.settings.multiple_disc_one_dir

    qobuz_dl.folder_format = pl_folder_format
    qobuz_dl.track_format = pl_track_format
    qobuz_dl.settings.multiple_disc_one_dir = True

    # Build position map for track numbering
    position_map = {}
    for idx, item in enumerate(remote_items, start=1):
        position_map[str(item["id"])] = idx

    downloaded_count = 0
    for tid in to_download_ids:
        playlist_idx = position_map.get(tid, 0)
        try:
            qobuz_dl.download_from_id(
                tid,
                album=False,
                alt_path=base_directory,
                is_playlist=True,
                playlist_index=playlist_idx,
            )
            downloaded_count += 1
        except Exception as e:
            logger.error(f"  {RED}[!] Failed to download track {tid}: {e}{OFF}")

    # Restore original settings
    qobuz_dl.folder_format = original_folder_format
    if original_track_format and hasattr(qobuz_dl, 'track_format'):
        qobuz_dl.track_format = original_track_format
    qobuz_dl.settings.multiple_disc_one_dir = original_multi_disc

    # --- 6. Generate artifacts ---
    logger.info(f"\n{CYAN}[5/5] Generating playlist artifacts...{OFF}")

    # M3U
    _build_m3u(base_directory, playlist_name, playlist_id, remote_items)

    # Cover
    if cover_url:
        _download_playlist_cover(base_directory, playlist_name, playlist_id, cover_url)

    # --- Final summary ---
    logger.info(f"\n{GREEN}━━━ SYNC COMPLETE ━━━{OFF}")
    logger.info(f"  {GREEN}↓ Downloaded : {downloaded_count} tracks{OFF}")
    logger.info(f"  {RED}✕ Deleted    : {deleted_count} files{OFF}")
    logger.info(f"  {GREEN}✓ Total now  : {len(remote_ids)} tracks{OFF}\n")


def _build_m3u(base_directory, playlist_name, playlist_id, remote_items):
    """
    Build M3U file in _Playlists/ with relative paths to tracks.
    """
    from qobuz_dl.utils import make_m3u_playlist

    playlists_dir = os.path.join(base_directory, "_Playlists")
    os.makedirs(playlists_dir, exist_ok=True)

    safe_name = _sanitize_filename(playlist_name)
    id_suffix = playlist_id[-8:] if len(playlist_id) > 8 else playlist_id
    m3u_path = os.path.join(playlists_dir, f"{safe_name} [{id_suffix}].m3u")

    make_m3u_playlist(base_directory, playlists_dir, m3u_path, remote_items)
    logger.info(f"  {CYAN}[+] M3U updated: {m3u_path}{OFF}")
