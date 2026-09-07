import os
from pathlib import Path

import pytest

from ig_reel_downloader.downloaders.base import DownloadContext
from ig_reel_downloader.downloaders.tiktok import TikTokDownloader


def _smoke_test_urls() -> list[str | None]:
    urls_file = os.getenv("TIKTOK_SMOKE_TEST_URLS_FILE")
    if urls_file:
        return [
            url.strip()
            for url in Path(urls_file).read_text().splitlines()
            if url.strip()
        ]
    return [os.getenv("TIKTOK_SMOKE_TEST_URL")]


@pytest.mark.parametrize("url", _smoke_test_urls())
def test_tiktok_live_download(tmp_path: Path, url: str | None) -> None:
    """Opt-in smoke test for TikTok/yt-dlp behavior from the current host IP."""
    if not url:
        pytest.skip(
            "set TIKTOK_SMOKE_TEST_URL or TIKTOK_SMOKE_TEST_URLS_FILE "
            "to run the live TikTok smoke test"
        )

    downloader = TikTokDownloader()
    candidates = downloader.extract_candidates(url)
    assert len(candidates) == 1, "use one supported canonical or share TikTok URL"

    resolve_result = downloader.resolve(candidates[0])
    assert resolve_result.request is not None, resolve_result.failure_reason

    result = downloader.download(
        resolve_result.request,
        DownloadContext(output_dir=tmp_path),
    )

    assert result.media is not None, result.failure_reason
    assert result.failure_reason is None
    assert result.media.provider == "tiktok"
    assert result.media.assets
    assert all(Path(asset.filepath).is_file() for asset in result.media.assets)
