import sqlite3
import os

DB_PATH = os.path.join(os.environ.get("APPDATA") if os.name == "nt" else os.path.join(os.environ["HOME"], ".config"), "qobuz-dl", "qobuz_dl.db")

def print_stats():
    try:
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT DISTINCT artist FROM downloads WHERE artist != '' ORDER BY artist ASC")
            artists = cursor.fetchall()
            
            print(f"\n--- QOBUZ-DL ULTIMATE STATISTICS ---")
            print(f"Total Unique Artists Downloaded: {len(artists)}\n")
            for artist in artists:
                print(f" - {artist[0]}")
    except Exception as e:
        print(f"Error reading database: {e}")

if __name__ == "__main__":
    print_stats()