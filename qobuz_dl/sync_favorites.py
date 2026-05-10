"""
Synchronize local folder with Qobuz favorites tracks.

Fetches user's favorited tracks from Qobuz, downloads missing ones,
and optionally syncs star status to Navidrome via Subsonic API.

Uses the same Yubal-inspired folder structure as playlist sync.
"""

import logging
import os
import time
from mutagen.flac import FLAC
from mutagen.id3 import ID3
from difflib import SequenceMatcher

from qobuz_dl.color import CYAN, GREEN, RED, YELLOW, OFF, MAGENTA
from qobuz_dl.constants import DEFAULT_PLAYLIST_FOLDER_FORMAT, DEFAULT_PLAYLIST_TRACK_FORMAT
from qobuz_dl.sync_playlist import (
    _scan_local_tracks,
    _sanitize_filename,
    _clean_empty_dirs,
    _format_path,
)
from qobuz_dl.navidrome_api import NavidromeClient

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _read_track_tags(fpath):
    """
    Read ISRC and title/artist from an audio file's tags.
    Returns a dict with 'isrc', 'title', 'artist', 'album_artist'.
    """
    tags = {"isrc": "", "title": "", "artist": "", "album_artist": ""}

    try:
        if fpath.lower().endswith(('.flac', '.ogg', '.opus', '.wav')):
            try:
                from mutagen.flac import FLAC as FLACFmt
                audio = FLACFmt(fpath)
                tags["isrc"] = audio.get("ISRC", [None])[0] or ""
                tags["title"] = audio.get("TITLE", [None])[0] or ""
                tags["artist"] = audio.get("ARTIST", [None])[0] or ""
                tags["album_artist"] = audio.get("ALBUMARTIST", [None])[0] or ""
            except Exception:
                try:
                    from mutagen.oggopus import OggOpus
                    audio = OggOpus(fpath)
                    tags["isrc"] = audio.get("isrc", [None])[0] or ""
                    tags["title"] = audio.get("title", [None])[0] or ""
                    tags["artist"] = audio.get("artist", [None])[0] or ""
                    tags["album_artist"] = audio.get("albumartist", [None])[0] or ""
                except Exception:
                    pass
        elif fpath.lower().endswith('.mp3'):
            try:
                audio = ID3(fpath)
                ti = audio.get("TCON")  # ISRC in TXXX
                txxx = audio.get("TXXX:ISRC")
                if txxx:
                    tags["isrc"] = txxx.text[0]
            except Exception:
                pass
    except Exception:
        pass

    return tags


def _fetch_favorites_tracks(client):
    """
    Fetch all favorited tracks from Qobuz via paginated API.

    Qobuz API response structure for favorite/getUserFavorites with fav_type="tracks":
    {
        "tracks": {
            "items": [ { "id": ..., "title": ..., "performer": {...}, ... }, ... ],
            "total": N
        }
    }

    Returns list of track dicts from Qobuz.
    """
    fav_type = "tracks"
    all_items = []
    offset = 0
    limit = 100

    while True:
        result = client.get_favorites(fav_type=fav_type, limit=limit, offset=offset)
        if not result:
            break

        # The API returns { fav_type: { "items": [...], "total": N } }
        # So for fav_type="tracks", it's { "tracks": { "items": [...], "total": N } }
        fav_data = result.get(fav_type, {})
        items = fav_data.get("items", [])
        total = fav_data.get("total", 0)

        logger.debug(f"      API page: {len(items)} items (total: {total})")

        if not items:
            break

        all_items.extend(items)

        if offset + limit >= total:
            break
        offset += limit

    return all_items


def _find_navidrome_track(nd_client, qobuz_item, local_tags=None):
    """
    Find the Navidrome song ID for a Qobuz track.

    Matching strategy (in order of reliability):
    1. ISRC exact match (most reliable)
    2. Title + Artist exact search
    3. Fuzzy match by title + artist + duration

    Args:
        nd_client: NavidromeClient instance
        qobuz_item: Qobuz track dict (has 'id', 'title', 'performer', 'album', 'duration')
        local_tags: Optional dict from _read_track_tags() with local file tags

    Returns:
        Navidrome song_id string, or None if not found
    """
    q_title = qobuz_item.get("title", "")
    performer = qobuz_item.get("performer", {}).get("name", "")
    q_artist = performer or ""
    q_duration = qobuz_item.get("duration", 0)

    # Use local tags ISRC if available (more reliable than Qobuz metadata)
    isrc = ""
    if local_tags and local_tags.get("isrc"):
        isrc = local_tags["isrc"]
    else:
        # Try to get ISRC from Qobuz track info
        isrc = qobuz_item.get("isrc", "")

    # --- Strategy 1: ISRC search ---
    if isrc:
        logger.debug(f"    Searching Navidrome by ISRC: {isrc}")
        results = nd_client.search_track(isrc, limit=10)
        for r in results:
            if r.get("isrc", "").upper() == isrc.upper():
                return r["id"]

    # --- Strategy 2: Title + Artist search ---
    search_query = f"{q_title}"
    if q_artist:
        search_query = f"{q_artist} {q_title}"

    results = nd_client.search_track(search_query, limit=20)
    if not results:
        return None

    # Exact match first
    for r in results:
        if (r["title"].lower() == q_title.lower() and
                r["artist"].lower() == q_artist.lower()):
            # Also check duration if available
            if q_duration and r.get("duration", 0):
                if abs(r["duration"] - q_duration) > 3:
                    continue
            return r["id"]

    # --- Strategy 3: Fuzzy match ---
    best_id = None
    best_score = 0.0

    for r in results:
        title_ratio = SequenceMatcher(None, q_title.lower(), r["title"].lower()).ratio()
        artist_ratio = SequenceMatcher(None, q_artist.lower(), r["artist"].lower()).ratio()

        # Weight title more heavily
        combined = title_ratio * 0.7 + artist_ratio * 0.3

        # Bonus for duration match
        dur_bonus = 0.0
        if q_duration and r.get("duration", 0):
            dur_diff = abs(r["duration"] - q_duration)
            if dur_diff <= 2:
                dur_bonus = 0.1
            elif dur_diff > 10:
                continue  # Skip way-off durations

        score = combined + dur_bonus

        if score > best_score:
            best_score = score
            best_id = r["id"]

    # Accept if score is high enough
    if best_score >= 0.75:
        return best_id

    return None


# ---------------------------------------------------------------------------
# Main sync function
# ---------------------------------------------------------------------------

def sync_favorites(qobuz_dl, folder, auto_confirm=False,
                   folder_format=None, track_format=None,
                   navidrome_url=None, navidrome_user=None, navidrome_password=None,
                   star_to_navidrome=True):
    """
    Main entry point for favorites sync.

    Steps:
    1. Fetch favorites from Qobuz
    2. Scan local folder for existing tracks
    3. Compute diff (download missing, note removed)
    4. Download missing tracks
    5. Sync star status to Navidrome (optional)

    Args:
        qobuz_dl: The QobuzDL instance
        folder: Base download directory
        auto_confirm: Skip confirmation prompt
        folder_format: Override folder format
        track_format: Override track format
        navidrome_url: Navidrome server URL
        navidrome_user: Navidrome username
        navidrome_password: Navidrome password
        star_to_navidrome: Whether to sync stars to Navidrome
    """
    from qobuz_dl.utils import make_m3u_playlist

    pl_folder_format = folder_format or DEFAULT_PLAYLIST_FOLDER_FORMAT
    pl_track_format = track_format or DEFAULT_PLAYLIST_TRACK_FORMAT

    base_directory = folder

    logger.info(f"\n{YELLOW}{'='*50}{OFF}")
    logger.info(f"{YELLOW}  QOBUZ FAVORITES SYNC{OFF}")
    logger.info(f"{YELLOW}{'='*50}{OFF}\n")

    # --- 1. Fetch remote favorites ---
    logger.info(f"{CYAN}[1/6] Fetching favorites from Qobuz...{OFF}")
    try:
        remote_items = _fetch_favorites_tracks(qobuz_dl.client)
    except Exception as e:
        logger.error(f"{RED}[-] Failed to fetch favorites: {e}{OFF}")
        return

    remote_ids = {str(item["id"]): item for item in remote_items}
    logger.info(f"{CYAN}      Found {len(remote_ids)} favorited tracks on Qobuz.{OFF}")

    if not remote_ids:
        logger.info(f"{YELLOW}No favorites on Qobuz. Nothing to sync.{OFF}")
        return

    # --- 2. Scan local tracks ---
    logger.info(f"\n{CYAN}[2/6] Scanning local folder...{OFF}")
    local_tracks, untagged = _scan_local_tracks(base_directory)
    logger.info(f"{CYAN}      Found {len(local_tracks)} tagged tracks locally.{OFF}")
    if untagged:
        logger.info(
            f"{YELLOW}      {len(untagged)} files have no QOBUZTRACKID tag and will be ignored.{OFF}"
        )

    # --- 3. Compute diff ---
    local_id_set = set(local_tracks.keys())
    remote_id_set = set(remote_ids.keys())

    to_download_ids = remote_id_set - local_id_set
    to_delete_ids = local_id_set - remote_id_set
    already_synced = local_id_set & remote_id_set

    logger.info(f"\n{CYAN}[3/6] Sync summary:{OFF}")
    logger.info(f"  {GREEN}↓ To download : {len(to_download_ids)} tracks{OFF}")
    logger.info(f"  {RED}✕ To delete   : {len(to_delete_ids)} files{OFF}")
    logger.info(f"  Already synced: {len(already_synced)} tracks{OFF}")

    if not to_download_ids and not to_delete_ids:
        logger.info(f"\n{GREEN}Folder is already in sync with your favorites!{OFF}")
        # Still update M3U and sync stars
        _build_favorites_m3u(base_directory, remote_items)
    else:
        # Print details
        if to_delete_ids:
            logger.info(f"\n{RED}Files to DELETE (no longer favorited):{OFF}")
            for tid in sorted(to_delete_ids):
                logger.info(f"  {RED}✕ {os.path.basename(local_tracks[tid])}{OFF}")

        if to_download_ids:
            logger.info(f"\n{GREEN}Tracks to DOWNLOAD (new favorites):{OFF}")
            for tid in sorted(to_download_ids):
                item = remote_ids[tid]
                album_artist = item.get("album", {}).get("artist", {}).get("name")
                performer_name = item.get("performer", {}).get("name", "Unknown")
                artist = performer_name if album_artist in [None, "Various Artists"] else album_artist
                title = item.get("title", "Unknown")
                logger.info(f"  {GREEN}↓ {artist} — {title}{OFF}")

    # --- Confirmation ---
    if not auto_confirm and (to_download_ids or to_delete_ids):
        try:
            answer = input(f"\n{YELLOW}Proceed with favorites sync? [y/N]: {OFF}").strip().lower()
            if answer != 'y':
                logger.info(f"{YELLOW}Sync cancelled by user.{OFF}")
                return
        except (KeyboardInterrupt, EOFError):
            logger.info(f"\n{YELLOW}Sync cancelled.{OFF}")
            return

    # --- 4. Execute sync ---
    logger.info(f"\n{CYAN}[4/6] Executing sync...{OFF}")

    # 4a. Delete stale files (no longer in favorites)
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

    _clean_empty_dirs(base_directory, exclude_dirs={"_Playlists"})

    # 4b. Download missing tracks
    original_folder_format = qobuz_dl.folder_format
    original_track_format = qobuz_dl.track_format if hasattr(qobuz_dl, 'track_format') else None
    original_multi_disc = qobuz_dl.settings.multiple_disc_one_dir

    qobuz_dl.folder_format = pl_folder_format
    qobuz_dl.track_format = pl_track_format
    qobuz_dl.settings.multiple_disc_one_dir = True

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
    if original_track_format is not None and hasattr(qobuz_dl, 'track_format'):
        qobuz_dl.track_format = original_track_format
    qobuz_dl.settings.multiple_disc_one_dir = original_multi_disc

    # --- 5. Generate M3U ---
    logger.info(f"\n{CYAN}[5/6] Generating favorites M3U...{OFF}")
    _build_favorites_m3u(base_directory, remote_items)

    # --- 6. Sync stars to Navidrome ---
    logger.info(f"\n{CYAN}[6/6] Syncing stars to Navidrome...{OFF}")
    _sync_stars_to_navidrome(
        nd_url=navidrome_url,
        nd_user=navidrome_user,
        nd_pass=navidrome_password,
        remote_ids=remote_ids,
        local_tracks=local_tracks,
        base_directory=base_directory,
        enable_sync=star_to_navidrome,
    )

    # --- Final summary ---
    logger.info(f"\n{GREEN}{'='*50}{OFF}")
    logger.info(f"{GREEN}  FAVORITES SYNC COMPLETE{OFF}")
    logger.info(f"{GREEN}{'='*50}{OFF}")
    logger.info(f"  {GREEN}↓ Downloaded : {downloaded_count} tracks{OFF}")
    logger.info(f"  {RED}✕ Deleted    : {deleted_count} files{OFF}")
    logger.info(f"  {GREEN}✓ Total now  : {len(remote_ids)} tracks{OFF}\n")


def _build_favorites_m3u(base_directory, remote_items):
    """Build M3U file for favorites in _Playlists/."""
    from qobuz_dl.utils import make_m3u_playlist

    playlists_dir = os.path.join(base_directory, "_Playlists")
    os.makedirs(playlists_dir, exist_ok=True)

    m3u_path = os.path.join(playlists_dir, "Mes Favoris Qobuz.m3u")
    make_m3u_playlist(base_directory, playlists_dir, m3u_path, remote_items)
    logger.info(f"  {CYAN}[+] M3U updated: {m3u_path}{OFF}")


# ---------------------------------------------------------------------------
# Navidrome star sync
# ---------------------------------------------------------------------------

def _sync_stars_to_navidrome(nd_url, nd_user, nd_pass, remote_ids,
                              local_tracks, base_directory, enable_sync=True):
    """
    Sync Qobuz favorites status to Navidrome star status.

    For each track:
    - If in Qobuz favorites AND found locally → star in Navidrome
    - If NOT in Qobuz favorites AND previously starred → unstar in Navidrome

    Args:
        nd_url: Navidrome server URL
        nd_user: Navidrome username
        nd_pass: Navidrome password
        remote_ids: Dict of {qobuz_track_id: qobuz_item} for favorited tracks
        local_tracks: Dict of {qobuz_track_id: file_path} for local tracks
        base_directory: Base download directory
        enable_sync: Whether Navidrome sync is enabled
    """
    if not enable_sync or not nd_url or not nd_user or not nd_pass:
        logger.info(f"  {YELLOW}[!] Navidrome sync disabled (no URL/user/pass configured){OFF}")
        return

    logger.info(f"  Connecting to Navidrome: {nd_url}...")
    nd = NavidromeClient(nd_url, nd_user, nd_pass)

    if not nd.test_connection():
        logger.error(f"  {RED}[-] Cannot reach Navidrome. Skipping star sync.{OFF}")
        return

    # Get current starred tracks from Navidrome
    logger.info(f"  Fetching current starred tracks from Navidrome...")
    try:
        nd_starred = nd.get_starred_tracks()
    except Exception as e:
        logger.error(f"  {RED}[-] Failed to fetch starred tracks: {e}{OFF}")
        return

    # Build a lookup of currently starred tracks by title+artist (since we don't have qobuz IDs in ND)
    # We'll match via search for each local track
    logger.info(f"  Found {len(nd_starred)} starred tracks in Navidrome.")

    starred_count = 0
    unstarred_count = 0
    matched_count = 0
    not_found_count = 0

    # Process tracks that ARE in favorites (should be starred)
    for qid, item in remote_ids.items():
        title = item.get("title", "")
        performer = item.get("performer", {}).get("name", "")

        # Find local file for this track
        local_path = local_tracks.get(qid)
        local_tags = None
        if local_path:
            local_tags = _read_track_tags(local_path)

        # Find Navidrome song ID
        nd_song_id = _find_navidrome_track(nd, item, local_tags)

        if nd_song_id:
            matched_count += 1
            # Star it
            if nd.star_track(nd_song_id):
                starred_count += 1
            else:
                logger.debug(f"    Failed to star: {title}")
        else:
            not_found_count += 1
            logger.debug(f"    Not found in Navidrome: {performer} - {title}")

    logger.info(f"  {GREEN}  Matched {matched_count} tracks in Navidrome library{OFF}")
    if not_found_count:
        logger.info(f"  {YELLOW}  {not_found_count} tracks not found in Navidrome (not downloaded yet){OFF}")

    # Now handle unstar: tracks that were starred but are NO LONGER in favorites
    # We need to check ND starred tracks that are not in our Qobuz favorites
    # This is trickier — we'd need to match ND starred tracks back to Qobuz IDs
    # For now, skip unstar to avoid accidentally unstuffing user's stars

    if matched_count == 0 and not_found_count > 0:
        logger.info(f"  {YELLOW}[!] No matches found in Navidrome.{OFF}")
        logger.info(f"  {YELLOW}    Make sure your library is scanned and tracks have been synced first.{OFF}")

    logger.info(f"  {GREEN}  Starred {starred_count} tracks in Navidrome{OFF}")
    logger.info(f"  {YELLOW}  Note: Unstar (remove favorites) is disabled to prevent accidental removal.{OFF}")
