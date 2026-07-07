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

    class FakeApp:
        def __init__(
            self,
            bot_token: str,
            repo: FakeRepository,
            *,
            telegram_media_write_timeout: float,
            telegram_read_timeout: float,
        ) -> None:
            self.bot_token = bot_token
            self.repo = repo
            self.telegram_media_write_timeout = telegram_media_write_timeout
            self.telegram_read_timeout = telegram_read_timeout
            self.downloader_config: tuple[str, str] | None = None
            self.did_run = False
            app_instances.append(self)

        def set_downloader_config(self, output_dir: str, cookie_filepath: str) -> None:
            self.downloader_config = (output_dir, cookie_filepath)

        def run(self) -> None:
            self.did_run = True

    monkeypatch.setattr(
        main_module.ig_reel_downloader.repository.sqlite,
        "SqliteRepository",
        FakeRepository,
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
    assert app.repo.db_path == "data/reels.db"
    assert app.downloader_config == ("output", "assets/cookies.txt")
    assert app.did_run is True
    assert Path("output").is_dir()
    assert Path("data").is_dir()
