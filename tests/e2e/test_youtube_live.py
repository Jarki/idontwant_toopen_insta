"""Opt-in real YouTube downloads; no Telegram or database access."""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from ig_reel_downloader.downloaders.base import DownloadContext
from ig_reel_downloader.downloaders.youtube import YouTubeDownloader

REGRESSION_URL = "https://www.youtube.com/shorts/CGMmA9B4TZE"


def _urls() -> list[str]:
    # Exercise both direct Shorts and the watch URL's metadata-reuse path.
    urls = [REGRESSION_URL, "https://www.youtube.com/watch?v=CGMmA9B4TZE"]
    if os.getenv("YOUTUBE_SMOKE_TEST") == "1" and (
        filename := os.getenv("YOUTUBE_SMOKE_TEST_URLS_FILE")
    ):
        urls.extend(Path(filename).read_text().splitlines())
    return list(dict.fromkeys(url.strip() for url in urls if url.strip()))


@pytest.mark.skipif(
    os.getenv("YOUTUBE_SMOKE_TEST") != "1",
    reason="set YOUTUBE_SMOKE_TEST=1 to run live YouTube downloads",
)
@pytest.mark.parametrize("url", _urls())
def test_youtube_live_download(tmp_path: Path, url: str) -> None:
    assert shutil.which("ffmpeg"), "live YouTube tests require ffmpeg"
    assert shutil.which("ffprobe"), "live YouTube tests require ffprobe"
    downloader = YouTubeDownloader()
    candidates = downloader.extract_candidates(url)
    assert len(candidates) == 1
    resolved = downloader.resolve(candidates[0])
    assert resolved.request is not None, resolved.failure_reason
    result = downloader.download(resolved.request, DownloadContext(output_dir=tmp_path))
    assert result.media is not None, result.failure_reason
    assert result.failure_reason is None
    assert result.media.provider == "youtube"
    assert len(result.media.assets) == 1
    path = Path(result.media.assets[0].filepath)
    assert path.is_file()
    assert path.stat().st_size > 0
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_streams", "-of", "json", str(path)],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    streams = json.loads(probe.stdout)["streams"]
    assert {"video", "audio"} <= {stream["codec_type"] for stream in streams}
