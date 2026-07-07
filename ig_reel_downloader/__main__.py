import logging
import os
from pathlib import Path

import dotenv

import ig_reel_downloader

dotenv.load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s (%(module)s:%(lineno)s) - %(message)s",
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger("ig_reel_downloader")


def _get_float_env(name: str, default: float) -> float:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return float(value)
    except ValueError as e:
        msg = f"{name} must be a number"
        raise ValueError(msg) from e


def main() -> None:
    output_dir = os.getenv("OUTPUT_DIR", "output")
    cookie_filepath = "assets/cookies.txt"
    bot_token = os.getenv("BOT_TOKEN")
    telegram_media_write_timeout = _get_float_env(
        "TELEGRAM_MEDIA_WRITE_TIMEOUT",
        ig_reel_downloader.app.DEFAULT_TELEGRAM_MEDIA_WRITE_TIMEOUT,
    )
    telegram_read_timeout = _get_float_env(
        "TELEGRAM_READ_TIMEOUT",
        ig_reel_downloader.app.DEFAULT_TELEGRAM_READ_TIMEOUT,
    )
    if bot_token is None:
        msg = "BOT_TOKEN is not set"
        raise ValueError(msg)

    Path(output_dir).mkdir(exist_ok=True)
    db_path = "data/reels.db"
    Path(db_path).parent.mkdir(exist_ok=True)

    repo = ig_reel_downloader.repository.sqlite.SqliteRepository(db_path)

    app = ig_reel_downloader.app.IgReelDownloaderApp(
        bot_token,
        repo,
        telegram_media_write_timeout=telegram_media_write_timeout,
        telegram_read_timeout=telegram_read_timeout,
    )
    app.set_downloader_config(output_dir, cookie_filepath)
    app.run()


if __name__ == "__main__":
    main()
