"""
Navidrome Subsonic REST API client.
Uses ONLY the Subsonic /rest/* endpoints — no native /api/ calls.
Handles track search, star/unstar operations, starred tracks listing,
library indexing, and playlist lookup for favorites synchronization.
"""

import logging
import re
import requests
import xml.etree.ElementTree as ET

from qobuz_dl.color import GREEN, RED, YELLOW, OFF

logger = logging.getLogger(__name__)


def _strip_namespace(xml_bytes):
    """Remove XML namespace prefixes so ElementTree can query tags simply."""
    text = xml_bytes.decode("utf-8", errors="replace")
    # Remove xmlns:ns0="..." declarations
    text = re.sub(r'\sxmlns(?:\w*):?\w*="[^"]*"', "", text)
    # Remove ns0: prefixes from tags
    text = re.sub(r'<(/?)(\w+:)([^>]*>)', r'<\1\3', text)
    return text.encode("utf-8")


class NavidromeClient:
    """Client for Navidrome's Subsonic-compatible REST API."""

    def __init__(self, server_url, username, password, verify_ssl=True):
        """
        Args:
            server_url: Base URL (e.g. 'http://192.168.1.22:4533')
            username: Navidrome username
            password: Navidrome password
            verify_ssl: Whether to verify SSL certificates (default: True)
        """
        self.server_url = server_url.rstrip("/")
        self.api_url = f"{self.server_url}/rest"
        self.username = username
        self.password = password
        self.verify_ssl = verify_ssl

    # ------------------------------------------------------------------ #
    #  Core HTTP helper — all Subsonic /rest/ calls go through here
    # ------------------------------------------------------------------ #

    def _api_call(self, endpoint, params=None):
        """
        Make an authenticated Subsonic REST call.
        Every request carries v, c, u, p via requests.params (URL-encoded).

        Returns the parsed XML root element (namespaces stripped), or None on failure.
        """
        qs = {
            "v": "1.16.1",
            "c": "qobuz-dl",
            "u": self.username,
            "p": self.password,
        }
        if params:
            qs.update(params)

        try:
            url = f"{self.api_url}/{endpoint}"
            resp = requests.get(url, params=qs, timeout=15, verify=self.verify_ssl)
            resp.raise_for_status()

            # Strip namespaces so we can query tags without {namespace} prefix
            clean_xml = _strip_namespace(resp.content)
            root = ET.fromstring(clean_xml)

            status = root.get("status")
            if status != "ok":
                error = root.find("error")
                msg = error.text if error is not None else "unknown"
                code = error.get("code", "?") if error is not None else "?"
                logger.error(
                    f"{RED}[-] Navidrome API {endpoint} error {code}: {msg}"
                    f" (full: {resp.text[:200]}){OFF}"
                )
                return None

            return root
        except requests.RequestException as e:
            logger.error(f"{RED}[-] Navidrome API error ({endpoint}): {e}{OFF}")
            return None
        except ET.ParseError as e:
            logger.error(
                f"{RED}[-] Navidrome XML parse error ({endpoint}): {e}"
                f" (body: {resp.text[:200]}){OFF}"
            )
            return None

    # ------------------------------------------------------------------ #
    #  Connection test
    # ------------------------------------------------------------------ #

    def test_connection(self):
        """Ping the server. Returns True if reachable."""
        root = self._api_call("ping")
        if root is not None:
            logger.info(f"{GREEN}[+] Navidrome connection OK: {self.server_url}{OFF}")
            return True
        logger.error(f"{RED}[-] Navidrome unreachable at {self.server_url}{OFF}")
        return False

    # ------------------------------------------------------------------ #
    #  Song parsing
    # ------------------------------------------------------------------ #

    @staticmethod
    def _parse_song(song_elem):
        """Extract a flat dict from a <song> XML element (no namespace needed)."""
        track = {}
        for key in ["id", "title", "artist", "album", "albumId", "coverArt", "size"]:
            el = song_elem.find(key)
            track[key] = el.text if el is not None and el.text else ""

        dur_el = song_elem.find("durationSec")
        track["duration"] = int(dur_el.text) if dur_el is not None and dur_el.text else 0

        isrc_el = song_elem.find("isrc")
        track["isrc"] = isrc_el.text if isrc_el is not None and isrc_el.text else ""

        return track

    # ------------------------------------------------------------------ #
    #  Search
    # ------------------------------------------------------------------ #

    def search_track(self, query, artist=None, limit=20):
        """
        Search for tracks via search3 (Subsonic v1.16.1 / OpenSubsonic).

        Args:
            query: Search query (title, ISRC, or filename)
            artist: Optional artist filter
            limit: Maximum results (default 20)

        Returns:
            List of track dicts.
        """
        if artist:
            query = f"{artist} {query}"

        root = self._api_call("search3", {"query": query, "limit": str(limit)})
        if root is None:
            return []

        container = root.find("searchResult3")
        if container is None:
            return []

        return [self._parse_song(s) for s in container.findall("song")]

    # ------------------------------------------------------------------ #
    #  Star / Unstar
    # ------------------------------------------------------------------ #

    def star_track(self, song_id):
        """Star (favorite) a track. Returns True on success."""
        root = self._api_call("star", {"id": song_id})
        return root is not None

    def unstar_track(self, song_id):
        """Remove star (unfavorite) a track. Returns True on success."""
        root = self._api_call("unstar", {"id": song_id})
        return root is not None

    # ------------------------------------------------------------------ #
    #  Starred list (getStarred2)
    # ------------------------------------------------------------------ #

    def get_starred_tracks(self, offset=0, size=500):
        """
        Get all starred (favorited) tracks from Navidrome.
        Returns list of dicts with at least {'id', 'title', 'artist', 'album'}.
        """
        all_tracks = []
        cur = offset

        while True:
            root = self._api_call("getStarred2", {
                "type": "song",
                "offset": str(cur),
                "size": str(size),
            })
            if root is None:
                break

            starred_list = root.find("starred2List")
            if starred_list is None:
                break

            entries = starred_list.findall("entry")
            if not entries:
                break

            for entry in entries:
                ss = entry.find("starredSong")
                if ss is None:
                    continue
                track = {"id": ss.get("id", "")}
                for key in ["title", "artist", "album"]:
                    el = ss.find(key)
                    track[key] = el.text if el is not None and el.text else ""
                all_tracks.append(track)

            if len(entries) < size:
                break
            cur += size

        return all_tracks

    # ------------------------------------------------------------------ #
    #  Single song lookup
    # ------------------------------------------------------------------ #

    def get_song_by_id(self, song_id):
        """Get a single song's details by Navidrome ID. Returns dict or None."""
        root = self._api_call("getSong", {"id": song_id})
        if root is None:
            return None
        song = root.find("song")
        if song is None:
            return None
        return self._parse_song(song)

    # ------------------------------------------------------------------ #
    #  Playlist lookup for a song (Subsonic REST only)
    # ------------------------------------------------------------------ #

    def get_playlists_for_song(self, song_id):
        """
        Get the list of playlists containing a given song.
        Uses Subsonic REST getPlaylists endpoint.

        Returns list of playlist names, or empty list.
        """
        root = self._api_call("getPlaylists")
        if root is None:
            return []

        playlists_elem = root.find("playlists")
        if playlists_elem is None:
            return []

        matching = []
        for pl in playlists_elem.findall("playlist"):
            entries = pl.findall("entry")
            for entry in entries:
                eid = entry.get("songId", "")
                if eid == song_id:
                    name_el = pl.find("name")
                    name = name_el.text if name_el is not None and name_el.text else "Unknown"
                    matching.append(name)
                    break

        return matching

    # ------------------------------------------------------------------ #
    #  Get all playlists / Get playlist by name
    # ------------------------------------------------------------------ #

    def get_playlists(self):
        """
        Get all playlists with their entries.
        Uses Subsonic REST getPlaylists endpoint.

        Returns list of dicts:
            [{'id': playlist_id, 'name': playlist_name,
              'entries': [{'songId': song_id, 'songTitle': ..., 'songAlbum': ..., 'songArtist': ...}, ...]}, ...]
        """
        root = self._api_call("getPlaylists")
        if root is None:
            return []

        playlists_elem = root.find("playlists")
        if playlists_elem is None:
            return []

        result = []
        for pl in playlists_elem.findall("playlist"):
            name_el = pl.find("name")
            pl_name = name_el.text if name_el is not None and name_el.text else "Unknown"
            pl_id = pl.get("id", "")

            entries = []
            for entry in pl.findall("entry"):
                entries.append({
                    "songId": entry.get("songId", ""),
                    "songTitle": entry.get("songTitle", ""),
                    "songAlbum": entry.get("songAlbum", ""),
                    "songArtist": entry.get("songArtist", ""),
                })

            result.append({
                "id": pl_id,
                "name": pl_name,
                "entries": entries,
            })

        return result

    def get_playlist_by_name(self, name_pattern):
        """
        Find a playlist by name (partial match) and return its song IDs.

        Args:
            name_pattern: String to match against playlist names (case-insensitive partial match)

        Returns:
            List of song IDs, or empty list if not found.
        """
        playlists = self.get_playlists()
        pattern_lower = name_pattern.lower()

        for pl in playlists:
            if pattern_lower in pl["name"].lower():
                logger.info(
                    f"  Found playlist '{pl['name']}' with {len(pl['entries'])} tracks (id={pl['id']})"
                )
                return [e["songId"] for e in pl["entries"] if e.get("songId")]

        logger.warning(f"  Playlist matching '{name_pattern}' not found in Navidrome")
        if playlists:
            names = [p["name"] for p in playlists[:10]]
            logger.info(f"  Available playlists: {names}")
        return []

    # ------------------------------------------------------------------ #
    #  Library index — Subsonic REST only
    # ------------------------------------------------------------------ #

    def get_library_index(self):
        """
        Build an in-memory index of ALL songs in Navidrome's library
        using ONLY Subsonic REST API:
          1. getMusicFolders -> get media folder IDs
          2. getChildren (recursive) -> collect all songs

        Returns a list of dicts with keys:
            id, title, artist, album, duration, isrc
        """
        all_songs = []

        # Step 1: Get music folders
        root = self._api_call("getMusicFolders")
        if root is None:
            logger.warning("  Subsonic: getMusicFolders returned no data")
            return []

        music_folders = root.findall(".//musicFolder")
        if not music_folders:
            raw = ET.tostring(root, encoding="unicode")
            logger.warning(
                f"  Subsonic: getMusicFolders returned OK but no <musicFolder> elements. "
                f"Raw response: {raw[:300]}"
            )
            return []

        logger.info(f"  Subsonic: found {len(music_folders)} music folder(s)")

        # Step 2: For each folder, enumerate children recursively
        for mf in music_folders:
            mf_id = mf.get("id")
            mf_name = mf.get("name", "unknown")
            if not mf_id:
                continue
            songs = self._get_children_songs(mf_id, depth=0)
            logger.info(f"    Folder '{mf_name}' ({mf_id}): {len(songs)} songs")
            all_songs.extend(songs)

        logger.info(
            f"  Subsonic: built index with {len(all_songs)} songs "
            f"from {len(music_folders)} folder(s)"
        )
        return all_songs

    def _get_children_songs(self, parent_id, depth=0):
        """
        Recursively get all songs under a folder/artist/album.
        Returns list of song dicts.
        """
        if depth > 10:
            return []

        all_songs = []
        page_size = 500
        offset = 0

        while True:
            root = self._api_call(
                "getChildren",
                {"id": parent_id, "offset": str(offset), "size": str(page_size)},
            )
            if root is None:
                break

            children = root.findall(".//child")
            if not children:
                break

            for child in children:
                child_type = child.get("type")
                child_id = child.get("id")
                if child_type == "song":
                    song_data = self._parse_song(child)
                    song_data["id"] = child_id
                    all_songs.append(song_data)
                elif child_type in ("album", "artist"):
                    sub_songs = self._get_children_songs(child_id, depth + 1)
                    all_songs.extend(sub_songs)
                # Ignore directories and playlists

            if len(children) < page_size:
                break
            offset += page_size

        return all_songs

    # ------------------------------------------------------------------ #
    #  Deprecated — alias kept for backward compat
    # ------------------------------------------------------------------ #

    def get_library_index_subsonic(self):
        """Alias for get_library_index (Subsonic-only)."""
        return self.get_library_index()
