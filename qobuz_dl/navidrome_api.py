"""
Navidrome Subsonic API client.
Handles track search, star/unstar operations, and starred tracks listing
for favorites synchronization.

Uses standard Subsonic auth: username + password sent with every request.
"""

import logging
import requests
import xml.etree.ElementTree as ET

from qobuz_dl.color import GREEN, RED, YELLOW, OFF

logger = logging.getLogger(__name__)


class NavidromeClient:
    """Client for Navidrome's Subsonic-compatible API."""

    def __init__(self, server_url, username, password):
        """
        Initialize the Navidrome client.

        Args:
            server_url: Base URL of Navidrome (e.g. 'http://localhost:4533')
            username: Navidrome username
            password: Navidrome password
        """
        self.server_url = server_url.rstrip("/")
        self.api_url = f"{self.server_url}/rest"
        self.username = username
        self.password = password

    # ------------------------------------------------------------------ #
    #  Core HTTP helper
    # ------------------------------------------------------------------ #

    def _api_call(self, endpoint, params=None):
        """
        Make an authenticated API call to Navidrome.

        Every request carries v, c, u, p (Subsonic standard).

        Returns the parsed XML root element, or None on failure.
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
            resp = requests.get(f"{self.api_url}/{endpoint}", params=qs, timeout=15)
            resp.raise_for_status()
            root = ET.fromstring(resp.content)

            # Check for Subsonic error wrapper
            status = root.get("status")
            if status != "ok":
                error = root.find("error")
                msg = error.text if error is not None else "unknown"
                code = error.get("code", "?") if error is not None else "?"
                logger.debug(f"Navidrome API {endpoint} returned error {code}: {msg}")
                return None

            return root
        except requests.RequestException as e:
            logger.error(f"{RED}[-] Navidrome API error ({endpoint}): {e}{OFF}")
            return None
        except ET.ParseError as e:
            logger.error(f"{RED}[-] Navidrome XML parse error ({endpoint}): {e}{OFF}")
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
    #  Search
    # ------------------------------------------------------------------ #

    @staticmethod
    def _parse_song(song_elem, ns=""):
        """Extract a flat dict from a <song> XML element."""
        track = {}
        for key in ["id", "title", "artist", "album", "albumId", "coverArt", "size"]:
            el = song_elem.find(f"{ns}{key}")
            track[key] = el.text if el is not None and el.text else ""

        # Numeric duration
        dur_el = song_elem.find(f"{ns}durationSec")
        track["duration"] = int(dur_el.text) if dur_el is not None and dur_el.text else 0

        # ISRC (often present)
        isrc_el = song_elem.find(f"{ns}isrc")
        track["isrc"] = isrc_el.text if isrc_el is not None and isrc_el.text else ""

        return track

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

        # Use search3 endpoint (Navidrome 0.61.x uses search3, not getSearch3)
        root = self._api_call("search3", {"query": query, "limit": str(limit)})
        if root is None:
            return []

        # Navidrome XML uses namespace http://subsonic.org/restapi
        ns = root.tag.split("}")[0].rstrip("{") if "}" in root.tag else ""
        if ns:
            ns = f"{{{ns}}}"

        container = root.find(f"{ns}searchResult3")
        if container is None:
            return []

        return [self._parse_song(s, ns=ns) for s in container.findall(f"{ns}song")]

    # ------------------------------------------------------------------ #
    #  Star / Unstar
    # ------------------------------------------------------------------ #

    def star_track(self, song_id):
        """Star (favorite) a track. Returns True on success."""
        root = self._api_call("star", {"id": song_id})
        if root is not None:
            return True
        return False

    def unstar_track(self, song_id):
        """Remove star (unfavorite) a track. Returns True on success."""
        root = self._api_call("unstar", {"id": song_id})
        if root is not None:
            return True
        return False

    # ------------------------------------------------------------------ #
    #  Starred list
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
    #  Playlist lookup for a song
    # ------------------------------------------------------------------ #

    def get_playlists_for_song(self, song_id):
        """
        Get the list of playlists containing a given song.

        Uses the native Navidrome API /api/song/{id} which returns
        playlist associations. Falls back to Subsonic API if needed.

        Returns list of playlist names, or empty list.
        """
        # Try native Navidrome API first
        try:
            import requests as req

            # Get token via login
            login_url = f"{self.server_url}/api/login"
            login_resp = req.post(login_url, json={
                "username": self.username,
                "password": self.password,
            }, timeout=10)

            if login_resp.status_code != 200:
                logger.debug(f"Native API login failed: {login_resp.status_code}")
                return []

            token = login_resp.json().get("token", "")
            if not token:
                return []

            # Get song details including playlists
            song_url = f"{self.server_url}/api/song/{song_id}"
            resp = req.get(song_url, headers={"Authorization": f"Bearer {token}"}, timeout=10)

            if resp.status_code != 200:
                logger.debug(f"Native API getSong failed: {resp.status_code}")
                return []

            data = resp.json().get("data", {})
            playlists = data.get("playlists", [])
            return [pl.get("name", "Unknown") for pl in playlists]

        except Exception as e:
            logger.debug(f"Failed to get playlists for song {song_id}: {e}")
            return []

    # ------------------------------------------------------------------ #
    #  Library index (native API)
    # ------------------------------------------------------------------ #

    def _get_native_token(self):
        """Authenticate via native API and return Bearer token, or None."""
        try:
            import requests as req
            login_url = f"{self.server_url}/api/login"
            resp = req.post(login_url, json={
                "username": self.username,
                "password": self.password,
            }, timeout=10)
            if resp.status_code == 200:
                return resp.json().get("token", "")
        except Exception as e:
            logger.debug(f"Native API login error: {e}")
        return None

    def get_library_index(self):
        """
        Build an in-memory index of ALL songs in Navidrome's library
        using the native API (/api/folders + /api/album/{id}).

        Strategy:
        1. GET /api/folders to get the folder tree (includes album IDs)
        2. Recursively walk the tree collecting all album IDs
        3. GET /api/album/{id} for each album to get its songs

        Returns a list of dicts with keys:
            id, title, artist, album, duration, isrc
        """
        import requests as req

        token = self._get_native_token()
        if not token:
            logger.debug("Cannot authenticate with native Navidrome API for library index")
            return []

        headers = {"Authorization": f"Bearer {token}"}
        all_songs = []
        seen_album_ids = set()

        # --- Step 1: Fetch folder tree and collect album IDs ---
        try:
            resp = req.get(f"{self.server_url}/api/folders", headers=headers, timeout=30)
            if resp.status_code != 200:
                logger.debug(f"Native API /api/folders failed: {resp.status_code}")
                return []

            folders = resp.json().get("data", [])

            # Recursive function to walk folder tree and collect album IDs
            def collect_album_ids(node, depth=0):
                if depth > 20:
                    return
                for child in node.get("children", []):
                    child_type = child.get("type", "")
                    child_id = child.get("id", "")
                    if child_type == "album" and child_id:
                        seen_album_ids.add(child_id)
                    elif child_type == "folder":
                        collect_album_ids(child, depth + 1)

            for folder in folders:
                collect_album_ids(folder)

            logger.info(f"  Native API: found {len(folders)} media folder(s), {len(seen_album_ids)} album(s)")

        except Exception as e:
            logger.debug(f"Error collecting album IDs from folder tree: {e}")
            return []

        # --- Step 2: Fetch each album's songs ---
        errors = 0
        for album_id in seen_album_ids:
            try:
                album_url = f"{self.server_url}/api/album/{album_id}"
                aresp = req.get(album_url, headers=headers, timeout=15)
                if aresp.status_code != 200:
                    errors += 1
                    if errors < 5:
                        logger.debug(f"  Album {album_id}: HTTP {aresp.status_code}")
                    continue

                album_data = aresp.json().get("data", {})
                album_name = album_data.get("name", "")
                songs = album_data.get("songs", [])

                for s in songs:
                    all_songs.append({
                        "id": s.get("id", ""),
                        "title": s.get("title", ""),
                        "artist": s.get("artistName", s.get("artist", "")),
                        "album": album_name or s.get("albumName", ""),
                        "duration": int(s.get("duration", 0)) if s.get("duration") else 0,
                        "isrc": s.get("isrc", ""),
                    })

            except Exception as e:
                errors += 1
                if errors <= 3:
                    logger.debug(f"  Error fetching album {album_id}: {e}")

        logger.info(f"  Native API: built index with {len(all_songs)} songs from {len(seen_album_ids)} albums")
        return all_songs
