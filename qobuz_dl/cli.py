import sys
import difflib
import string
import configparser
import logging
import glob
import os
import getpass
import hashlib
import signal
import requests

from qobuz_dl.bundle import Bundle
from qobuz_dl.color import GREEN, RED, YELLOW, OFF, CYAN, MAGENTA
from qobuz_dl.commands import qobuz_dl_args
from qobuz_dl.core import QobuzDL
from qobuz_dl.downloader import DEFAULT_FOLDER, DEFAULT_TRACK
from qobuz_dl.settings import QobuzDLSettings

logging.basicConfig(
    level=logging.INFO,
    format="%(message)s",
)

if os.name == "nt":
    OS_CONFIG = os.environ.get("APPDATA")
else:
    OS_CONFIG = os.path.join(os.environ["HOME"], ".config")

CONFIG_PATH = os.path.join(OS_CONFIG, "qobuz-dl")
CONFIG_FILE = os.path.join(CONFIG_PATH, "config.ini")
QOBUZ_DB = os.path.join(CONFIG_PATH, "qobuz_dl.db")


def validate_config_formats(formats_to_check):
    """
    Scans the configuration format strings for unknown variables to prevent
    silent KeyErrors during the download process. Includes typo suggestions.
    """
    VALID_KEYS = {
        "artist", "album", "album_id", "album_url", "album_title", 
        "album_title_base", "album_artist", "album_genre", "album_composer", 
        "label", "copyright", "upc", "barcode", "release_date", "year", 
        "media_type", "format", "bit_depth", "sampling_rate", "album_version", 
        "version_tag", "disc_count", "track_count", "ExplicitFlag", "explicit", 
        "release_type", "tracktitle", "track_title", "track_title_base", 
        "track_id", "track_artist", "track_composer", "track_number", 
        "isrc", "version", "disc_number"
    }

    has_errors = False
    
    # Define color strings locally to ensure they print correctly in terminal
    C_RED = '\033[91m'
    C_YEL = '\033[93m'
    C_GRE = '\033[92m'
    C_OFF = '\033[0m'

    for config_name, format_string in formats_to_check.items():
        if not format_string:
            continue
            
        try:
            parsed_vars = [tup[1] for tup in string.Formatter().parse(str(format_string)) if tup[1] is not None]
            
            for var in parsed_vars:
                base_var = var.split(':')[0].split('!')[0]
                
                if base_var not in VALID_KEYS:
                    print(f"{C_YEL}[!] Config Warning: Unknown variable '{{{base_var}}}' detected in '{config_name}'.{C_OFF}")
                    
                    # --- NEW: 'Did you mean' logic using difflib ---
                    similar_keys = difflib.get_close_matches(base_var, VALID_KEYS, n=1, cutoff=0.6)
                    if similar_keys:
                        print(f"    {C_GRE}-> Did you mean '{{{similar_keys[0]}}}'?{C_OFF}")
                    # -----------------------------------------------
                    
                    print(f"    {C_RED}-> This will cause the entire format string to be discarded during download.{C_OFF}")
                    has_errors = True
                    
        except ValueError as e:
            print(f"{C_RED}[!] Config Error: Syntax error in '{config_name}' -> {e}{C_OFF}")
            has_errors = True

    if has_errors:
        print(f"\n{C_YEL}[*] Tip: Please check your config.ini file or your command line arguments and fix any typos before downloading.{C_OFF}\n")
        # Abort the process immediately
        sys.exit(1)


def _reset_config(config_file):
    logging.info(f"\n{YELLOW}--- QOBUZ-DL CONFIGURATION WIZARD (2026 Update) ---{OFF}")
    config = configparser.ConfigParser(interpolation=None)
    
    config["qobuz"] = {}
    
    email = input("Enter your Qobuz email:\n- ").strip()
    config["qobuz"]["email"] = email
    
    print(f"\n{YELLOW}[!] ATTENTION: Qobuz API blocked direct password login for 3rd party apps.{OFF}")
    print(f"{YELLOW}[!] You must use your browser Auth Token (F12 > Storage > Local Storage > localuser > token).{OFF}")
    
    auth_token = input("Paste your browser token here:\n- ").strip()
    
    config["qobuz"]["password"] = ""
    config["qobuz"]["auth_token"] = auth_token

    fetch_lyrics = input("Do you want to automatically download and inject lyrics? (yes/no) [Default: yes]\n- ").strip().lower()
    config["qobuz"]["fetch_lyrics"] = "false" if fetch_lyrics in ['no', 'n', 'false'] else "true"
    
    genius_token = ""
    if config["qobuz"]["fetch_lyrics"] == "true":
        print(f"{YELLOW}[!] To use Genius as a fallback, enter your API Token. Leave blank to only use LRCLIB (Free/No API).{OFF}")
        genius_token = input("Genius API Token:\n- ").strip()
    config["qobuz"]["genius_token"] = genius_token

    config["qobuz"]["directory"] = (
        input("Download folder (press Enter for 'Qobuz Downloads')\n- ")
        or "Qobuz Downloads"
    )
    
    # FIX: Use correct prompt and key for folder formatting
    config["qobuz"]["folder_format"] = (
        input(f"Folder format (press Enter for '{DEFAULT_FOLDER}')\n- ")
        or DEFAULT_FOLDER
    )
    
    config["qobuz"]["default_quality"] = (
        input("Download quality (5:MP3, 6:FLAC, 7:24b<96, 27:24b>96) [Default 27]\n- ")
        or "27"
    )
    
    config["qobuz"]["default_limit"] = "500"
    config["qobuz"]["no_m3u"] = "false"
    config["qobuz"]["albums_only"] = "false"
    config["qobuz"]["no_fallback"] = "false"
    config["qobuz"]["og_cover"] = "true" 
    config["qobuz"]["embed_art"] = "true"
    config["qobuz"]["no_cover"] = "false"
    config["qobuz"]["no_database"] = "false"
    config["qobuz"]["no_lrc_files"] = "false"
    config["qobuz"]["legacy_charmap"] = "false"
    config["qobuz"]["blacklist"] = "blacklist.txt"

    logging.info(f"{YELLOW}Getting tokens. Please wait...{OFF}")
    bundle = Bundle()
    config["qobuz"]["app_id"] = str(bundle.get_app_id())
    config["qobuz"]["secrets"] = ",".join(bundle.get_secrets().values())

    # Removed old folder_format override that caused custom format resets
    config["qobuz"]["track_format"] = "{track_number:02d} - {track_title}"
    config["qobuz"]["fallback_folder_format"] = "{artist} - {album}"
    config["qobuz"]["smart_discography"] = "false"

    config["qobuz"]["no_album_artist_tag"] = "false"
    config["qobuz"]["no_album_title_tag"] = "false"
    config["qobuz"]["no_track_artist_tag"] = "false"
    config["qobuz"]["no_track_title_tag"] = "false"
    config["qobuz"]["no_release_date_tag"] = "false"
    config["qobuz"]["no_media_type_tag"] = "false"
    config["qobuz"]["no_genre_tag"] = "false"
    config["qobuz"]["no_track_number_tag"] = "false"
    config["qobuz"]["no_track_total_tag"] = "false"
    config["qobuz"]["no_disc_number_tag"] = "false"
    config["qobuz"]["no_disc_total_tag"] = "false"
    config["qobuz"]["no_composer_tag"] = "false"
    
    config["qobuz"]["no_explicit_tag"] = "false"
    config["qobuz"]["no_copyright_tag"] = "false"
    config["qobuz"]["no_label_tag"] = "false"
    
    config["qobuz"]["no_credits"] = "false"
    
    config["qobuz"]["no_upc_tag"] = "false"
    config["qobuz"]["no_isrc_tag"] = "false"
          
    config["qobuz"]["embedded_art_size"] = "600"
    config["qobuz"]["saved_art_size"] = "org"
    
    config["qobuz"]["multiple_disc_prefix"] = "CD"
    config["qobuz"]["multiple_disc_one_dir"] = "false"
    config["qobuz"]["multiple_disc_track_format"] = "{disc_number:02d}.{track_number:02d} - {track_title}"
    
    config["qobuz"]["max_workers"] = "3"
    config["qobuz"]["user_auth_token"] = ""

    # Playlist sync format configuration
    config["qobuz"]["playlist_folder_format"] = (
        input(f"Playlist folder format (press Enter for '{{album_artist}}/{{year}} - {{album_title}}')\n- ")
        or "{album_artist}/{year} - {album_title}"
    )
    config["qobuz"]["playlist_track_format"] = (
        input(f"Playlist track format (press Enter for '{{track_number:02d}} - {{track_title}}')\n- ")
        or "{track_number:02d} - {track_title}"
    )

    # Favorites sync format configuration
    config["qobuz"]["favorites_folder_format"] = (
        input(f"Favorites folder format (press Enter for '{{album_artist}}/{{year}} - {{album_title}}')\n- ")
        or "{album_artist}/{year} - {album_title}"
    )
    config["qobuz"]["favorites_track_format"] = (
        input(f"Favorites track format (press Enter for '{{track_number:02d}} - {{track_title}}')\n- ")
        or "{track_number:02d} - {track_title}"
    )

    # Navidrome integration
    print(f"\n{YELLOW}[!] Optional: Navidrome integration for auto-star sync{OFF}")
    print(f"    Leave blank to skip.\n")
    config["qobuz"]["navidrome_url"] = input("Navidrome URL (e.g. http://localhost:4533)\n- ").strip()
    config["qobuz"]["navidrome_user"] = input("Navidrome username\n- ").strip()
    config["qobuz"]["navidrome_password"] = getpass.getpass("Navidrome password\n- ").strip()

    with open(config_file, "w") as configfile:
        config.write(configfile)
        
    logging.info(f"\n{GREEN}[+] Configuration successfully saved in {config_file}!{OFF}")
    

def _remove_leftovers(directory):
    directory = os.path.join(directory, "**", ".*.tmp")
    for i in glob.glob(directory, recursive=True):
        try:
            os.remove(i)
        except:  # noqa
            pass


def _handle_commands(qobuz, arguments):
    def sigint_handler(sig, frame):
        print(f"\n\n\033[91m[!] Download forcibly interrupted by the user.\033[0m")
        print(f"\033[93mPartially downloaded files will be ignored or overwritten on the next run.\033[0m")
        try:
            _remove_leftovers(qobuz.directory)
        except Exception:
            pass
        sys.exit(1)
        
    signal.signal(signal.SIGINT, sigint_handler)

    try:
        if arguments.command == "dl":
            qobuz.download_list_of_urls(arguments.SOURCE)
        elif arguments.command in ("sync-playlist", "sp"):
            from qobuz_dl.sync_playlist import sync_playlist
            sync_playlist(
                qobuz,
                arguments.URL,
                qobuz.directory,
                auto_confirm=arguments.yes,
                folder_format=qobuz.settings.playlist_folder_format,
                track_format=qobuz.settings.playlist_track_format,
            )
        elif arguments.command in ("sync-favorites", "sf"):
            from qobuz_dl.sync_favorites import sync_favorites
            sync_favorites(
                qobuz,
                qobuz.directory,
                auto_confirm=arguments.yes,
                folder_format=qobuz.settings.favorites_folder_format,
                track_format=qobuz.settings.favorites_track_format,
                navidrome_url=qobuz.settings.navidrome_url,
                navidrome_user=qobuz.settings.navidrome_user,
                navidrome_password=qobuz.settings.navidrome_password,
                star_to_navidrome=not getattr(arguments, 'no_navidrome', False),
            )
        elif arguments.command == "lucky":
            query = " ".join(arguments.QUERY)
            qobuz.lucky_type = arguments.type
            qobuz.lucky_limit = arguments.number
            qobuz.lucky_mode(query)
        else:
            qobuz.interactive_limit = arguments.limit
            qobuz.interactive()

    except KeyboardInterrupt:
        pass
    finally:
        _remove_leftovers(qobuz.directory)


def _initial_checks():
    if not os.path.isdir(CONFIG_PATH) or not os.path.isfile(CONFIG_FILE):
        os.makedirs(CONFIG_PATH, exist_ok=True)
        _reset_config(CONFIG_FILE)

    if len(sys.argv) < 2:
        sys.exit(qobuz_dl_args().print_help())

def check_for_updates():
    try:
        from qobuz_dl import __version__
        
        url = "https://api.github.com/repos/Sei969/qobuz-dl/releases/latest"
        response = requests.get(url, timeout=2)
        response.raise_for_status()
        
        latest_version_str = response.json().get("tag_name", "").replace("v", "")
        current_version_str = __version__
        
        latest_tuple = tuple(map(int, latest_version_str.split(".")))
        current_tuple = tuple(map(int, current_version_str.split(".")))
        
        if latest_tuple > current_tuple:
            print(f"\n{YELLOW}[*] UPDATE AVAILABLE: Ultimate Edition v{latest_version_str} is out!{OFF}")
            print(f"{YELLOW}    - PyPI: run 'pip install -U qobuz-dl-ultimate'{OFF}")
            print(f"{YELLOW}    - Docker: pull the latest image{OFF}")
            print(f"{YELLOW}    - Standalone: download the new release from GitHub{OFF}\n")
            
    except Exception:
        pass


def _run_sync_watch():
    """
    Periodic sync loop: reads playlists from SYNC_PLAYLISTS env var,
    runs sync_playlist at SYNC_INTERVAL seconds intervals.
    Designed for Docker long-running containers.
    """
    import time

    playlists_raw = os.environ.get("SYNC_PLAYLISTS", "").strip()
    if not playlists_raw:
        logging.error(f"{RED}[!] SYNC_PLAYLISTS environment variable is not set.{OFF}")
        logging.error(f"{YELLOW}Set it to one or more Qobuz playlist URLs separated by ';'{OFF}")
        sys.exit(1)

    playlists = [url.strip() for url in playlists_raw.split(";") if url.strip()]
    interval = int(os.environ.get("SYNC_INTERVAL", "21600"))
    sync_dir = os.environ.get("SYNC_DIR", None)
    auto_yes = os.environ.get("SYNC_YES", "false").lower() in ("true", "1", "yes")
    sync_favorites_enabled = os.environ.get("SYNC_FAVORITES", "false").lower() in ("true", "1", "yes")
    navidrome_url = os.environ.get("SYNC_NAVI_URL", "").strip()
    navidrome_user = os.environ.get("SYNC_NAVI_USER", "").strip()
    navidrome_password = os.environ.get("SYNC_NAVI_PASS", "").strip()

    logging.info(f"\n{CYAN}{'='*50}{OFF}")
    logging.info(f"{CYAN}  Qobuz-DL Sync Watch Mode{OFF}")
    logging.info(f"{CYAN}{'='*50}{OFF}")
    logging.info(f"  Playlists  : {len(playlists)}")
    logging.info(f"  Favorites  : {'enabled' if sync_favorites_enabled else 'disabled'}")
    logging.info(f"  Interval   : {interval}s ({interval//3600}h {interval%3600//60}m)")
    logging.info(f"  Auto-yes   : {auto_yes}")
    if sync_dir:
        logging.info(f"  Download dir : {sync_dir}")
    logging.info(f"{CYAN}{'='*50}\n")

    def graceful_shutdown(sig, frame):
        logging.info(f"\n\n{YELLOW}[!] Sync Watch received stop signal. Shutting down gracefully...{OFF}")
        sys.exit(0)

    signal.signal(signal.SIGINT, graceful_shutdown)
    signal.signal(signal.SIGTERM, graceful_shutdown)

    config = configparser.ConfigParser(interpolation=None)
    config.read(CONFIG_FILE)

    section = "qobuz" if config.has_section("qobuz") else "DEFAULT"

    email = config.get(section, "email")
    token = config.get(section, "auth_token", fallback="")
    password = token if token else config.get(section, "password")
    app_id = config.get(section, "app_id")
    secrets = [s for s in config.get(section, "secrets").split(",") if s]

    directory_val = config.get(section, "directory", fallback=None)
    default_folder = sync_dir if sync_dir else (directory_val or "Qobuz Downloads")
    default_folder = os.path.expanduser(default_folder)

    default_quality = int(config.get(section, "default_quality", fallback="6"))
    default_limit = int(config.get(section, "default_limit", fallback="500"))
    folder_format = config.get(section, "folder_format", fallback=DEFAULT_FOLDER)
    track_format = config.get(section, "track_format", fallback=DEFAULT_TRACK)
    force_english = not config.getboolean(section, "native_lang", fallback=False)

    arguments = qobuz_dl_args(default_quality, default_limit, default_folder).parse_args(['sync-watch'])

    settings = QobuzDLSettings.from_arguments_configparser(arguments, config)

    # Allow env var overrides for playlist formats
    env_playlist_folder = os.environ.get("SYNC_PLAYLIST_FOLDER_FORMAT", "").strip()
    env_playlist_track = os.environ.get("SYNC_PLAYLIST_TRACK_FORMAT", "").strip()
    if env_playlist_folder:
        settings.playlist_folder_format = env_playlist_folder
    if env_playlist_track:
        settings.playlist_track_format = env_playlist_track

    # Allow env var overrides for favorites formats
    env_favorites_folder = os.environ.get("SYNC_FAVORITES_FOLDER_FORMAT", "").strip()
    env_favorites_track = os.environ.get("SYNC_FAVORITES_TRACK_FORMAT", "").strip()
    if env_favorites_folder:
        settings.favorites_folder_format = env_favorites_folder
    if env_favorites_track:
        settings.favorites_track_format = env_favorites_track

    # Override Navidrome settings from env vars (Docker)
    if navidrome_url:
        settings.navidrome_url = navidrome_url
    if navidrome_user:
        settings.navidrome_user = navidrome_user
    if navidrome_password:
        settings.navidrome_password = navidrome_password

    logging.info(f"  Playlist fmt   : {settings.playlist_folder_format}")
    logging.info(f"  Track fmt      : {settings.playlist_track_format}")
    logging.info(f"  Favorites fmt  : {settings.favorites_folder_format}")

    qobuz = QobuzDL(
        default_folder,
        arguments.quality,
        config.getboolean(section, "embed_art", fallback=True),
        ignore_singles_eps=config.getboolean(section, "albums_only", fallback=False),
        no_m3u_for_playlists=config.getboolean(section, "no_m3u", fallback=False),
        quality_fallback=not config.getboolean(section, "no_fallback", fallback=False),
        cover_og_quality=config.getboolean(section, "og_cover", fallback=True),
        no_cover=config.getboolean(section, "no_cover", fallback=False),
        downloads_db=None if config.getboolean(section, "no_database", fallback=False) else QOBUZ_DB,
        folder_format=folder_format,
        track_format=track_format,
        smart_discography=config.getboolean(section, "smart_discography", fallback=False),
        fetch_lyrics=config.getboolean(section, "fetch_lyrics", fallback=False),
        no_lrc_files=config.getboolean(section, "no_lrc_files", fallback=False),
        genius_token=config.get(section, "genius_token", fallback=None),
        force_english=force_english,
        no_credits=config.getboolean(section, "no_credits", fallback=False),
        settings=settings,
        blacklist=config.get(section, "blacklist", fallback="blacklist.txt"),
    )

    qobuz.initialize_client(email, password, app_id, secrets)

    from qobuz_dl.sync_playlist import sync_playlist

    run = 0
    while True:
        run += 1
        logging.info(f"{CYAN}[Sync Watch] Run #{run} - {time.strftime('%Y-%m-%d %H:%M:%S')}{OFF}")

        for idx, playlist_url in enumerate(playlists, 1):
            logging.info(f"{GREEN}  [{idx}/{len(playlists)}] Syncing: {playlist_url}{OFF}")
            try:
                sync_playlist(
                    qobuz,
                    playlist_url,
                    default_folder,
                    auto_confirm=auto_yes,
                    folder_format=settings.playlist_folder_format,
                    track_format=settings.playlist_track_format,
                )
                logging.info(f"{GREEN}  [{idx}/{len(playlists)}] Done.{OFF}")
            except Exception as e:
                logging.error(f"{RED}  [{idx}/{len(playlists)}] Error: {e}{OFF}")

        # --- Sync favorites if enabled ---
        if sync_favorites_enabled:
            logging.info(f"{MAGENTA}[Sync Watch] Syncing Qobuz favorites...{OFF}")
            try:
                from qobuz_dl.sync_favorites import sync_favorites
                sync_favorites(
                    qobuz,
                    default_folder,
                    auto_confirm=auto_yes,
                    folder_format=settings.favorites_folder_format,
                    track_format=settings.favorites_track_format,
                    navidrome_url=settings.navidrome_url,
                    navidrome_user=settings.navidrome_user,
                    navidrome_password=settings.navidrome_password,
                    star_to_navidrome=bool(settings.navidrome_url),
                )
                logging.info(f"{MAGENTA}[Sync Watch] Favorites synced.{OFF}")
            except Exception as e:
                logging.error(f"{RED}[Sync Watch] Favorites sync error: {e}{OFF}")

        logging.info(f"\n{YELLOW}[Sync Watch] All playlists synced. Sleeping {interval}s ({interval//3600}h {interval%3600//60}m)...{OFF}")

        sleep_end = time.time() + interval
        while time.time() < sleep_end:
            remaining = sleep_end - time.time()
            if remaining > 0:
                time.sleep(min(remaining, 60))

    logging.info(f"{GREEN}[Sync Watch] Stopped.{OFF}")


def main():
    _initial_checks()
    check_for_updates()

    # --- RADAR FEATURE (Standalone Intercept) ---
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "radar":
        from qobuz_dl.radar import run_radar
        
        try:
            run_radar()
        except KeyboardInterrupt:
            print("\n\n\033[91m[!] Radar manually interrupted by the user (CTRL+C).\033[0m")
        sys.exit(0)
    # --------------------------------------------

    # --- SYNC-WATCH MODE (Docker periodic sync loop) ---
    if len(sys.argv) > 1 and sys.argv[1] in ("sync-watch", "sw"):
        _run_sync_watch()
    # --------------------------------------------------

    # --- NEW: STATS COMMAND INTEGRATION ---
    if len(sys.argv) > 1 and sys.argv[1] == "stats":
        from qobuz_dl.db import get_stats
        
        # QOBUZ_DB è già definito all'inizio di cli.py, lo usiamo direttamente
        artists = get_stats(QOBUZ_DB)
        
        print(f"\n{CYAN}--- QOBUZ-DL ULTIMATE STATISTICS ---{OFF}")
        if not artists:
            print(f"{YELLOW}No artist data found yet. Start downloading to populate your stats!{OFF}")
        else:
            print(f"Total Unique Artists Downloaded: {len(artists)}\n")
            for artist in artists:
                print(f" - {artist}")
        print(f"{CYAN}-------------------------------------{OFF}\n")
        sys.exit(0) # Esce immediatamente dopo aver stampato le statistiche
    # -------------------------------------------------

    config = configparser.ConfigParser(interpolation=None)
    config.read(CONFIG_FILE)

    try:
        section = "qobuz" if config.has_section("qobuz") else "DEFAULT"
        
        email = config.get(section, "email")
        token = config.get(section, "auth_token", fallback="")
        password = token if token else config.get(section, "password")
        
        fetch_lyrics = config.getboolean(section, "fetch_lyrics", fallback=False)
        genius_token = config.get(section, "genius_token", fallback=None)
        
        # --- FIX: Backward compatibility for default_folder ---
        directory_val = config.get(section, "directory", fallback=None)
        if directory_val is not None:
            default_folder = directory_val
        else:
            legacy_val = config.get(section, "default_folder", fallback=None)
            if legacy_val is not None:
                # If the legacy key is used, accept it but print a yellow warning
                print(f"\033[93m[!] Notice: 'default_folder' in config.ini is deprecated. Please rename it to 'directory' for future updates.\033[0m")
                default_folder = legacy_val
            else:
                default_folder = "Qobuz Downloads"
        # ------------------------------------------------------
        default_limit = config.get(section, "default_limit")
        default_quality = config.get(section, "default_quality")
        
        no_m3u = config.getboolean(section, "no_m3u", fallback=False)
        no_lrc_files_config = config.getboolean(section, "no_lrc_files", fallback=False)
        albums_only = config.getboolean(section, "albums_only", fallback=False)
        no_fallback = config.getboolean(section, "no_fallback", fallback=False)
        og_cover = config.getboolean(section, "og_cover", fallback=True)
        embed_art = config.getboolean(section, "embed_art", fallback=True)
        no_cover = config.getboolean(section, "no_cover", fallback=False)
        no_database = config.getboolean(section, "no_database", fallback=False)
        legacy_charmap = config.getboolean(section, "legacy_charmap", fallback=False)
        
        no_credits_config = config.getboolean(section, "no_credits", fallback=False)
        blacklist_config = config.get(section, "blacklist", fallback="blacklist.txt")
        
        app_id = config.get(section, "app_id")
        secrets = [s for s in config.get(section, "secrets").split(",") if s]
        
        smart_discography = config.getboolean(section, "smart_discography", fallback=False)
        folder_format = config.get(section, "folder_format", fallback=DEFAULT_FOLDER)
        track_format = config.get(section, "track_format", fallback=DEFAULT_TRACK)

        arguments = qobuz_dl_args(
            default_quality, default_limit, default_folder
        ).parse_args()
        
        if getattr(arguments, 'no_lyrics', False):
            fetch_lyrics = False
            
        force_english = not getattr(arguments, 'native_lang', False)
        no_credits_flag = getattr(arguments, 'no_credits', False) or no_credits_config 
        
    except (configparser.Error, KeyError) as error:
        arguments = qobuz_dl_args().parse_args()
        if not arguments.reset:
            # FIX: Define ANSI codes locally to bypass UnboundLocalError
            RED_C = '\033[91m'
            YELLOW_C = '\033[93m'
            OFF_C = '\033[0m'
            sys.exit(
                f"{RED_C}Invalid or corrupted configuration ({error}).\n{OFF_C}"
                f"{YELLOW_C}Run 'python -m qobuz_dl -r' to fix this.{OFF_C}"
            )

    if arguments.reset:
        sys.exit(_reset_config(CONFIG_FILE))

    if arguments.show_config:
        print(f"Configuration: {CONFIG_FILE}\nDatabase: {QOBUZ_DB}\n---")
        with open(CONFIG_FILE, "r") as f:
            print(f.read())
        sys.exit()

    if arguments.purge:
        try:
            os.remove(QOBUZ_DB)
        except FileNotFoundError:
            pass
        sys.exit(f"{GREEN}Database has been purged.{OFF}")

    # --- NEW DB SYNC FEATURE (Lightweight Mode) ---
    if getattr(arguments, 'sync_db', None):
        from qobuz_dl.sync import sync_database
        from qobuz_dl.qopy import Client
                
        # Initialize a lightweight API client for Reverse Lookup (bypassing the heavy downloader)
        sync_client = Client(email, password, app_id, secrets, user_auth_token=token, force_english=force_english)
        
        # Path management
        sync_dir = default_folder if arguments.sync_db == "DEFAULT" else arguments.sync_db
        
        if os.name == "nt":
            sync_dir = os.path.abspath(sync_dir)
            if not sync_dir.startswith("\\\\?\\"):
                sync_dir = "\\\\?\\" + sync_dir
                
        sync_database(sync_dir, QOBUZ_DB, sync_client)
        sys.exit(f"\n{GREEN}Database synchronization finished successfully.{OFF}")
    # ----------------------------------------------

    # --- RETRO LYRICS FEATURE (Standalone Mode) ---
    # Intercept the command here before QobuzDLSettings looks for 'directory', which would crash the program
    if arguments.command == "lyrics":
        from qobuz_dl.retro_tagger import inject_lyrics_retroactively
        
        target_dir = arguments.DIR
        if os.name == "nt":
            target_dir = os.path.abspath(target_dir)
            if not target_dir.startswith("\\\\?\\"):
                target_dir = "\\\\?\\" + target_dir
                
        try:
            inject_lyrics_retroactively(target_dir, genius_token=genius_token)
        except KeyboardInterrupt:
            print("\n\n\033[91m[!] Operation manually interrupted by the user (CTRL+C).\033[0m")
            print("\033[93mAlready processed files are safe. Exiting...\033[0m")
        sys.exit(0)
    # ----------------------------------------------

    directory_to_use = arguments.directory if hasattr(arguments, 'directory') and arguments.directory else default_folder
    directory_to_use = os.path.expanduser(directory_to_use)

    # --- WINDOWS LONG PATH BYPASS ---
    if os.name == "nt":
        directory_to_use = os.path.abspath(directory_to_use)
        if not directory_to_use.startswith("\\\\?\\"):
            directory_to_use = "\\\\?\\" + directory_to_use
    # --------------------------------

    settings = QobuzDLSettings.from_arguments_configparser(arguments, config)
    settings.legacy_charmap = legacy_charmap
    
    # Execute the Pre-flight Config Check
    # --- PRE-FLIGHT CONFIG CHECK ---
    formats_to_validate = {
        "folder_format": arguments.folder_format or folder_format,
        "track_format": arguments.track_format or track_format,
        "fallback_folder_format": config.get(section, "fallback_folder_format", fallback="{artist} - {album}"),
        "multiple_disc_track_format": config.get(section, "multiple_disc_track_format", fallback="{disc_number}.{track_number} - {track_title}"),
        "playlist_folder_format": config.get(section, "playlist_folder_format", fallback="{album_artist}/{year} - {album_title}"),
        "playlist_track_format": config.get(section, "playlist_track_format", fallback="{track_number:02d} - {track_title}"),
    }
    validate_config_formats(formats_to_validate)
    # -------------------------------

    qobuz = QobuzDL(
        directory_to_use,
        arguments.quality,
        arguments.embed_art or embed_art,
        ignore_singles_eps=arguments.albums_only or albums_only,
        no_m3u_for_playlists=arguments.no_m3u or no_m3u,
        quality_fallback=not arguments.no_fallback or not no_fallback,
        cover_og_quality=arguments.og_cover or og_cover,
        no_cover=arguments.no_cover or no_cover,
        downloads_db=None if no_database or arguments.no_db else QOBUZ_DB,
        folder_format=arguments.folder_format or folder_format,
        track_format=arguments.track_format or track_format,
        smart_discography=arguments.smart_discography or smart_discography,
        fetch_lyrics=fetch_lyrics,
        no_lrc_files=("--no-lrc-files" in sys.argv) or no_lrc_files_config,
        genius_token=genius_token,
        force_english=force_english,
        no_credits=no_credits_flag,
        settings=settings,
        booklet_only=getattr(arguments, 'booklet_only', False),
        blacklist=getattr(arguments, 'blacklist', None) or blacklist_config,
    )
    
    qobuz.initialize_client(email, password, app_id, secrets)

    _handle_commands(qobuz, arguments)


if __name__ == "__main__":
    main()