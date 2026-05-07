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
            
            # Legacy migration (v1 to v2)
            if 'quality' not in columns:
                logger.info(f"{YELLOW}Migrating old database to the new format...{OFF}")
                
                # Rename the old table
                conn.execute("ALTER TABLE downloads RENAME TO downloads_old")
                
                # Create the new table with updated schema including artist and album
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
                
                # Copy old historical IDs
                try:
                    conn.execute("INSERT INTO downloads (id) SELECT id FROM downloads_old")
                except sqlite3.Error as e:
                    logger.error(f"{RED}Failed to migrate old data: {e}{OFF}")
                
                # Drop the temporary old table
                conn.execute("DROP TABLE downloads_old")
                logger.info(f"{YELLOW}Database successfully updated!{OFF}")
                
            # New Migration (v2 to v2.1.4): Add artist and album if missing
            elif 'artist' not in columns:
                logger.info(f"{YELLOW}Upgrading database schema: Adding artist and album columns...{OFF}")
                try:
                    conn.execute("ALTER TABLE downloads ADD COLUMN artist text NOT NULL DEFAULT ''")
                    conn.execute("ALTER TABLE downloads ADD COLUMN album text NOT NULL DEFAULT ''")
                    logger.info(f"{YELLOW}Schema upgrade complete!{OFF}")
                except sqlite3.Error as e:
                    logger.error(f"{RED}Failed to add new columns: {e}{OFF}")
                
        else:
            # Table does not exist, create it from scratch
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
                  PRIMARY KEY ("id", "quality")
                );
                """)
                logger.info(f"{YELLOW}Download-IDs database created{OFF}")
            except sqlite3.OperationalError:
                pass
                
        return db_path


def handle_download_id(db_path, item_id, add_id=False, media_type='album', quality=27, file_format='FLAC',
                       quality_met=0, bit_depth=None, sampling_rate=None, saved_path='', status='downloaded',
                       url='', release_date='', artist='', album=''):
    if not db_path:
        return

    with sqlite3.connect(db_path) as conn:
        if add_id:
            try:
                # Inject artist and album dynamically into the database
                conn.execute(
                    """
                    INSERT INTO downloads (id, media_type, quality, file_format, quality_met, bit_depth, 
                    sampling_rate, saved_path, url, release_date, status, artist, album) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (item_id, media_type, quality, file_format, quality_met, bit_depth, sampling_rate,
                     saved_path, url, release_date, status, artist, album),
                )
                conn.commit()
            except sqlite3.IntegrityError:
                # Provide clean visual feedback instead of an error
                logger.info(f"{YELLOW}[i] Already in database, skipping.{OFF}")
            except sqlite3.Error as e:
                logger.error(f"{RED}Unexpected DB error: {e}{OFF}")
        else:
            return conn.execute(
                "SELECT id FROM downloads WHERE id=? AND quality=?",
                (item_id, quality),
            ).fetchone()