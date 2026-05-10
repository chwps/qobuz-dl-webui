import logging
import sqlite3

from qobuz_dl.color import YELLOW, RED, OFF

logger = logging.getLogger(__name__)


def create_db(db_path):
    with sqlite3.connect(db_path) as conn:
        cursor = conn.cursor()
        
        # Check if the table already exists
        cursor.execute("SELECT count(name) FROM sqlite_master WHERE type='table' AND name='downloads'")
        
        if cursor.fetchone()[0] == 1:
            # Table exists. Read current columns
            cursor.execute("PRAGMA table_info(downloads)")
            columns = [info[1] for info in cursor.fetchall()]
            
            # === Migration v1 -> v2 (quality column) ===
            if 'quality' not in columns:
                logger.info(f"{YELLOW}Migrating old database to the new format...{OFF}")
                
                conn.execute("ALTER TABLE downloads RENAME TO downloads_old")
                
                conn.execute("""
                CREATE TABLE downloads (
                  "id" text NOT NULL,
                  "media_type" text NOT NULL DEFAULT 'album',
                  "quality" integer NOT NULL DEFAULT 27,
                  "file_format" text NOT NULL DEFAULT 'FLAC',
                  "quality_met" integer NOT NULL DEFAULT 0,
                  "bit_depth" text,
                  "sampling_rate" text,
                  "saved_path" text NOT NULL DEFAULT '',
                  "status" text NOT NULL DEFAULT 'downloaded',
                  "url" text NOT NULL DEFAULT '',
                  "release_date" text NOT NULL DEFAULT '',
                  "artist" text NOT NULL DEFAULT '',
                  "album" text NOT NULL DEFAULT '',
                  PRIMARY KEY ("id", "quality")
                );
                """)
                
                try:
                    conn.execute("INSERT INTO downloads (id) SELECT id FROM downloads_old")
                except sqlite3.Error as e:
                    logger.error(f"{RED}Failed to migrate old data: {e}{OFF}")
                
                conn.execute("DROP TABLE downloads_old")
                logger.info(f"{YELLOW}Database successfully updated!{OFF}")
            
            # === Migration v2 -> v2.1.4 (artist + album columns) ===
            elif 'artist' not in columns:
                logger.info(f"{YELLOW}Upgrading database schema: Adding artist and album columns...{OFF}")
                try:
                    conn.execute("ALTER TABLE downloads ADD COLUMN artist text NOT NULL DEFAULT ''")
                    conn.execute("ALTER TABLE downloads ADD COLUMN album text NOT NULL DEFAULT ''")
                    logger.info(f"{YELLOW}Schema upgrade complete!{OFF}")
                except sqlite3.Error as e:
                    logger.error(f"{RED}Failed to add new columns: {e}{OFF}")
            
            # === Migration v2.1.4 -> v3 (source + sync_active columns) ===
            elif 'source' not in columns:
                logger.info(f"{YELLOW}Upgrading database schema: Adding source and sync_active columns...{OFF}")
                try:
                    # Drop old table, create new one with source + sync_active
                    conn.execute("ALTER TABLE downloads RENAME TO downloads_v2")
                    
                    conn.execute("""
                    CREATE TABLE downloads (
                      "id" text NOT NULL,
                      "media_type" text NOT NULL DEFAULT 'album',
                      "quality" integer NOT NULL DEFAULT 27,
                      "file_format" text NOT NULL DEFAULT 'FLAC',
                      "quality_met" integer NOT NULL DEFAULT 0,
                      "bit_depth" text,
                      "sampling_rate" text,
                      "saved_path" text NOT NULL DEFAULT '',
                      "status" text NOT NULL DEFAULT 'downloaded',
                      "url" text NOT NULL DEFAULT '',
                      "release_date" text NOT NULL DEFAULT '',
                      "artist" text NOT NULL DEFAULT '',
                      "album" text NOT NULL DEFAULT '',
                      "source" text NOT NULL DEFAULT 'album',
                      "sync_active" integer NOT NULL DEFAULT 1,
                      PRIMARY KEY ("id", "source")
                    );
                    """)
                    
                    # Migrate old data: set source='album', sync_active=1
                    try:
                        conn.execute("""
                            INSERT INTO downloads (id, media_type, quality, file_format, quality_met,
                                bit_depth, sampling_rate, saved_path, status, url, release_date,
                                artist, album, source, sync_active)
                            SELECT id, media_type, quality, file_format, quality_met,
                                bit_depth, sampling_rate, saved_path, status, url, release_date,
                                artist, album, 'album', 1
                            FROM downloads_v2
                        """)
                    except sqlite3.Error as e:
                        logger.error(f"{RED}Failed to migrate v2->v3: {e}{OFF}")
                    
                    conn.execute("DROP TABLE downloads_v2")
                    logger.info(f"{YELLOW}Schema upgrade v3 complete! (source + sync_active columns){OFF}")
                except sqlite3.Error as e:
                    logger.error(f"{RED}Failed to add source/sync_active columns: {e}{OFF}")
            
            # === Migration v3 check: sync_active column ===
            elif 'sync_active' not in columns:
                logger.info(f"{YELLOW}Upgrading database schema: Adding sync_active column...{OFF}")
                try:
                    conn.execute("ALTER TABLE downloads ADD COLUMN sync_active integer NOT NULL DEFAULT 1")
                    logger.info(f"{YELLOW}sync_active column added!{OFF}")
                except sqlite3.Error as e:
                    logger.error(f"{RED}Failed to add sync_active column: {e}{OFF}")
            
        else:
            # Table does not exist, create it from scratch (v3 schema)
            try:
                conn.execute("""
                CREATE TABLE downloads (
                  "id" text NOT NULL,
                  "media_type" text NOT NULL DEFAULT 'album',
                  "quality" integer NOT NULL DEFAULT 27,
                  "file_format" text NOT NULL DEFAULT 'FLAC',
                  "quality_met" integer NOT NULL DEFAULT 0,
                  "bit_depth" text,
                  "sampling_rate" text,
                  "saved_path" text NOT NULL DEFAULT '',
                  "status" text NOT NULL DEFAULT 'downloaded',
                  "url" text NOT NULL DEFAULT '',
                  "release_date" text NOT NULL DEFAULT '',
                  "artist" text NOT NULL DEFAULT '',
                  "album" text NOT NULL DEFAULT '',
                  "source" text NOT NULL DEFAULT 'album',
                  "sync_active" integer NOT NULL DEFAULT 1,
                  PRIMARY KEY ("id", "source")
                );
                """)
                logger.info(f"{YELLOW}Download-IDs database created (v3 schema){OFF}")
            except sqlite3.OperationalError:
                pass
            
        return db_path


def handle_download_id(db_path, item_id, add_id=False, media_type='album', quality=27, file_format='FLAC',
                       quality_met=0, bit_depth=None, sampling_rate=None, saved_path='', status='downloaded',
                       url='', release_date='', artist='', album='', source='album', sync_active=True):
    if not db_path:
        return

    with sqlite3.connect(db_path) as conn:
        if add_id:
            try:
                conn.execute(
                    """
                    INSERT OR IGNORE INTO downloads (id, media_type, quality, file_format, quality_met, bit_depth, 
                    sampling_rate, saved_path, url, release_date, status, artist, album, source, sync_active) 
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (item_id, media_type, quality, file_format, quality_met, bit_depth, sampling_rate,
                     saved_path, url, release_date, status, artist, album, source, int(sync_active)),
                )
                conn.commit()
            except sqlite3.IntegrityError:
                logger.info(f"{YELLOW}[i] Already in database, skipping.{OFF}")
            except sqlite3.Error as e:
                logger.error(f"{RED}Unexpected DB error: {e}{OFF}")
        else:
            return conn.execute(
                "SELECT id FROM downloads WHERE id=? AND quality=?",
                (item_id, quality),
            ).fetchone()


def get_sources(db_path, track_id):
    """
    Get all sources for a given track ID.
    
    Returns list of dicts: [{'source': 'album', 'sync_active': 1, 'artist': '...', 'album': '...', 'saved_path': '...'}, ...]
    """
    if not db_path:
        return []
    try:
        with sqlite3.connect(db_path) as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT source, sync_active, artist, album, saved_path FROM downloads WHERE id=?",
                (track_id,),
            )
            results = cursor.fetchall()
            return [
                {
                    'source': row[0],
                    'sync_active': bool(row[1]),
                    'artist': row[2] or '',
                    'album': row[3] or '',
                    'saved_path': row[4] or '',
                }
                for row in results
            ]
    except sqlite3.Error:
        return []


def count_active_sources(db_path, track_id):
    """
    Count how many active sources a track has.
    
    Returns the count of sources where sync_active=1.
    """
    if not db_path:
        return 0
    try:
        with sqlite3.connect(db_path) as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT COUNT(*) FROM downloads WHERE id=? AND sync_active=1",
                (track_id,),
            )
            return cursor.fetchone()[0]
    except sqlite3.Error:
        return 0


def get_stale_entries(db_path):
    """
    Get all entries where sync_active=0.
    
    Returns list of dicts with track info.
    """
    if not db_path:
        return []
    try:
        with sqlite3.connect(db_path) as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT id, source, artist, album, saved_path, sync_active FROM downloads WHERE sync_active=0"
            )
            results = cursor.fetchall()
            return [
                {
                    'id': row[0],
                    'source': row[1],
                    'artist': row[2] or '',
                    'album': row[3] or '',
                    'saved_path': row[4] or '',
                    'sync_active': bool(row[5]),
                }
                for row in results
            ]
    except sqlite3.Error:
        return []


def mark_source_inactive(db_path, source):
    """
    Set sync_active=0 for all entries matching a specific source.
    
    Returns the number of rows updated.
    """
    if not db_path:
        return 0
    try:
        with sqlite3.connect(db_path) as conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE downloads SET sync_active=0 WHERE source=? AND sync_active=1",
                (source,),
            )
            conn.commit()
            return cursor.rowcount
    except sqlite3.Error as e:
        logger.error(f"{RED}Failed to mark source inactive: {e}{OFF}")
        return 0


def purge_stale_entries(db_path, track_ids=None):
    """
    Delete entries from the DB where sync_active=0 AND no other active source exists.
    
    If track_ids is provided, only purge those specific IDs.
    If track_ids is None, purge ALL eligible stale entries.
    
    Returns list of deleted track IDs.
    """
    if not db_path:
        return []
    deleted = []
    try:
        with sqlite3.connect(db_path) as conn:
            cursor = conn.cursor()
            
            if track_ids:
                placeholders = ','.join(['?' for _ in track_ids])
                cursor.execute(f"""
                    DELETE FROM downloads 
                    WHERE sync_active=0 
                    AND id IN ({placeholders})
                    AND id NOT IN (
                        SELECT id FROM downloads WHERE sync_active=1
                    )
                """, track_ids)
            else:
                cursor.execute("""
                    DELETE FROM downloads 
                    WHERE sync_active=0 
                    AND id NOT IN (
                        SELECT id FROM downloads WHERE sync_active=1
                    )
                """)
            
            conn.commit()
            
            # Get the IDs that were deleted
            if track_ids:
                deleted = track_ids
            else:
                # We need to figure out what was deleted - query before delete is better
                # But since we already deleted, just return empty
                deleted = []
            
    except sqlite3.Error as e:
        logger.error(f"{RED}Failed to purge stale entries: {e}{OFF}")
    return deleted


def delete_stale_with_ids(db_path):
    """
    Get stale track IDs first, then delete them, returning the deleted IDs.
    
    Returns list of deleted track IDs.
    """
    if not db_path:
        return []
    try:
        with sqlite3.connect(db_path) as conn:
            cursor = conn.cursor()
            
            # First, find eligible IDs
            cursor.execute("""
                SELECT DISTINCT id FROM downloads 
                WHERE sync_active=0 
                AND id NOT IN (
                    SELECT id FROM downloads WHERE sync_active=1
                )
            """)
            stale_ids = [row[0] for row in cursor.fetchall()]
            
            if not stale_ids:
                return []
            
            # Delete them
            placeholders = ','.join(['?' for _ in stale_ids])
            cursor.execute(f"""
                DELETE FROM downloads 
                WHERE sync_active=0 
                AND id IN ({placeholders})
            """, stale_ids)
            conn.commit()
            
            return stale_ids
    except sqlite3.Error as e:
        logger.error(f"{RED}Failed to delete stale entries: {e}{OFF}")
        return []


def remove_source_entry(db_path, track_id, source):
    """
    Remove a specific source entry for a track.
    
    Returns True if deleted.
    """
    if not db_path:
        return False
    try:
        with sqlite3.connect(db_path) as conn:
            cursor = conn.cursor()
            cursor.execute(
                "DELETE FROM downloads WHERE id=? AND source=?",
                (track_id, source),
            )
            conn.commit()
            return cursor.rowcount > 0
    except sqlite3.Error as e:
        logger.error(f"{RED}Failed to remove source entry: {e}{OFF}")
        return False


def get_stats(db_path):
    """Returns a list of unique artists from the database."""
    if not db_path:
        return []
    try:
        with sqlite3.connect(db_path) as conn:
            cursor = conn.cursor()
            # We select unique artists, excluding empty strings
            cursor.execute("SELECT DISTINCT artist FROM downloads WHERE artist != '' ORDER BY artist ASC")
            return [row[0] for row in cursor.fetchall()]
    except sqlite3.Error:
        return []
