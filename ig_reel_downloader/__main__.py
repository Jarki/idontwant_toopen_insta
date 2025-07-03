import logging
import os

import dotenv
import ig_reel_downloader


dotenv.load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s (%(module)s:%(lineno)s) - %(msg)s",
)
logging.getLogger('httpx').setLevel(logging.WARNING)
logger = logging.getLogger('ig_reel_downloader')

def main():
    OUTPUT_DIR = os.getenv('OUTPUT_DIR', 'output')
    COOKIE_FILEPATH = 'assets/cookies.txt'
    BOT_TOKEN = os.getenv('BOT_TOKEN')
    if BOT_TOKEN is None:
        raise ValueError('BOT_TOKEN is not set')

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    DB_PATH = "data/reels.db"
    path, _ = os.path.split(DB_PATH)
    os.makedirs(path, exist_ok=True)
    repo = ig_reel_downloader.repository.sqlite.SqliteRepository(DB_PATH)
    repo.create_database()

    app = ig_reel_downloader.app.IgReelDownloaderApp(BOT_TOKEN, repo)
    app.set_downloader_config(OUTPUT_DIR, COOKIE_FILEPATH)
    app.run()

if __name__ == '__main__':
    main()

