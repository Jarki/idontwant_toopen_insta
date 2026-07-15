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
    output_dir = Path(os.getenv("OUTPUT_DIR", "output"))
    cookie_filepath = Path("assets/cookies.txt")
    bot_token = os.getenv("BOT_TOKEN")
    telegram_media_write_timeout = _get_float_env(
        "TELEGRAM_MEDIA_WRITE_TIMEOUT",
        ig_reel_downloader.app.DEFAULT_TELEGRAM_MEDIA_WRITE_TIMEOUT,
    )
    telegram_read_timeout = _get_float_env(
        "TELEGRAM_READ_TIMEOUT",
        ig_reel_downloader.app.DEFAULT_TELEGRAM_READ_TIMEOUT,
    )
    judgmental_chance = _get_float_env("JUDGMENTAL_CHANCE", 0.0)
    if bot_token is None:
        msg = "BOT_TOKEN is not set"
        raise ValueError(msg)

    output_dir.mkdir(exist_ok=True)
    database_url = os.getenv("DATABASE_URL")
    if database_url is None:
        msg = "DATABASE_URL is not set"
        raise ValueError(msg)
    if not database_url.startswith("postgresql+psycopg://"):
        msg = "DATABASE_URL must use the postgresql+psycopg:// dialect"
        raise ValueError(msg)

    repo = ig_reel_downloader.repository.postgres.PostgreSQLRepository(database_url)
    downloaders: list[ig_reel_downloader.downloaders.Downloader] = [
        ig_reel_downloader.downloaders.InstagramReelDownloader(
            cookie_filepath=cookie_filepath
        ),
        ig_reel_downloader.downloaders.InstagramPostDownloader(
            cookie_filepath=cookie_filepath
        ),
        ig_reel_downloader.downloaders.TikTokDownloader(
            cookie_filepath=cookie_filepath
        ),
        ig_reel_downloader.downloaders.YouTubeDownloader(
            cookie_filepath=cookie_filepath
        ),
    ]
    registry = ig_reel_downloader.downloaders.DownloaderRegistry(downloaders)
    fetch_service = ig_reel_downloader.media_fetch.MediaFetchService(
        repo,
        output_dir=output_dir,
    )
    renderer = ig_reel_downloader.telegram_renderer.TelegramMediaRenderer(
        telegram_media_write_timeout=telegram_media_write_timeout,
        telegram_read_timeout=telegram_read_timeout,
    )

    app = ig_reel_downloader.app.IgReelDownloaderApp(
        bot_token,
        registry,
        fetch_service,
        renderer,
        telegram_media_write_timeout=telegram_media_write_timeout,
        telegram_read_timeout=telegram_read_timeout,
        judgmental_chance=judgmental_chance,
        judgmental_gifs=ig_reel_downloader.judgmental.JUDGMENTAL_GIFS,
    )
    app.run()


if __name__ == "__main__":
    main()
