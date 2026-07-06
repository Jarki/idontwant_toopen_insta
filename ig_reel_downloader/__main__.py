import logging
import os
from pathlib import Path

import dotenv

import ig_reel_downloader

dotenv.load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s (%(module)s:%(lineno)s) - %(msg)s",
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger("ig_reel_downloader")


def main() -> None:
    output_dir = os.getenv("OUTPUT_DIR", "output")
    cookie_filepath = "assets/cookies.txt"
    bot_token = os.getenv("BOT_TOKEN")
    if bot_token is None:
        msg = "BOT_TOKEN is not set"
        raise ValueError(msg)

    Path(output_dir).mkdir(exist_ok=True)
    db_path = "data/reels.db"
    Path(db_path).parent.mkdir(exist_ok=True)

    repo = ig_reel_downloader.repository.sqlite.SqliteRepository(db_path)
    repo.create_database()

    app = ig_reel_downloader.app.IgReelDownloaderApp(bot_token, repo)
    app.set_downloader_config(output_dir, cookie_filepath)
    app.run()


if __name__ == "__main__":
    main()
