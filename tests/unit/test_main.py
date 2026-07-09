from pathlib import Path

from ig_reel_downloader import __main__ as main_module


def test_main_does_not_run_migrations(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("BOT_TOKEN", "telegram-token")

    app_instances = []

    class FakeRepository:
        def __init__(self, db_path: str) -> None:
            self.db_path = db_path

        def create_database(self) -> None:
            raise AssertionError("bot startup must not run migrations")

    class FakeDownloader:
        def __init__(self, cookie_filepath: Path | None) -> None:
            self.cookie_filepath = cookie_filepath

    class FakeRegistry:
        def __init__(self, downloaders: list[object]) -> None:
            self.downloaders = downloaders

    class FakeFetchService:
        def __init__(self, repository: FakeRepository, output_dir: Path) -> None:
            self.repository = repository
            self.output_dir = output_dir

    class FakeRenderer:
        def __init__(
            self,
            telegram_media_write_timeout: float,
            telegram_read_timeout: float,
        ) -> None:
            self.telegram_media_write_timeout = telegram_media_write_timeout
            self.telegram_read_timeout = telegram_read_timeout

    class FakeApp:
        def __init__(
            self,
            bot_token: str,
            registry: FakeRegistry,
            fetch_service: FakeFetchService,
            renderer: FakeRenderer,
            *,
            telegram_media_write_timeout: float,
            telegram_read_timeout: float,
        ) -> None:
            self.bot_token = bot_token
            self.registry = registry
            self.fetch_service = fetch_service
            self.renderer = renderer
            self.telegram_media_write_timeout = telegram_media_write_timeout
            self.telegram_read_timeout = telegram_read_timeout
            self.did_run = False
            app_instances.append(self)

        def run(self) -> None:
            self.did_run = True

    monkeypatch.setattr(
        main_module.ig_reel_downloader.repository.sqlite,
        "SqliteRepository",
        FakeRepository,
    )
    monkeypatch.setattr(
        main_module.ig_reel_downloader.downloaders,
        "InstagramReelDownloader",
        FakeDownloader,
    )
    monkeypatch.setattr(
        main_module.ig_reel_downloader.downloaders,
        "InstagramPostDownloader",
        FakeDownloader,
    )
    monkeypatch.setattr(
        main_module.ig_reel_downloader.downloaders,
        "TikTokDownloader",
        FakeDownloader,
    )
    monkeypatch.setattr(
        main_module.ig_reel_downloader.downloaders,
        "YouTubeDownloader",
        FakeDownloader,
    )
    monkeypatch.setattr(
        main_module.ig_reel_downloader.downloaders,
        "DownloaderRegistry",
        FakeRegistry,
    )
    monkeypatch.setattr(
        main_module.ig_reel_downloader.media_fetch,
        "MediaFetchService",
        FakeFetchService,
    )
    monkeypatch.setattr(
        main_module.ig_reel_downloader.telegram_renderer,
        "TelegramMediaRenderer",
        FakeRenderer,
    )
    monkeypatch.setattr(
        main_module.ig_reel_downloader.app,
        "IgReelDownloaderApp",
        FakeApp,
    )

    main_module.main()

    assert len(app_instances) == 1
    app = app_instances[0]
    assert app.bot_token == "telegram-token"
    assert app.fetch_service.repository.db_path == "data/reels.db"
    assert app.fetch_service.output_dir == Path("output")
    assert [d.__class__.__name__ for d in app.registry.downloaders] == [
        "FakeDownloader",
        "FakeDownloader",
        "FakeDownloader",
        "FakeDownloader",
    ]
    assert app.registry.downloaders[0].cookie_filepath == Path("assets/cookies.txt")
    assert app.renderer.telegram_media_write_timeout == 120.0
    assert app.renderer.telegram_read_timeout == 30.0
    assert app.did_run is True
    assert Path("output").is_dir()
    assert Path("data").is_dir()
