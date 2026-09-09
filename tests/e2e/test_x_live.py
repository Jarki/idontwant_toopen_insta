import os
from pathlib import Path

import pytest

from ig_reel_downloader.downloaders.base import DownloadContext
from ig_reel_downloader.downloaders.x import XDownloader
from ig_reel_downloader.renderers import default_renderer_registry
from ig_reel_downloader.telegram_sender import TELEGRAM_RENDER_CONSTRAINTS

KNOWN_TEXT_POSTS = {
    "2097476203863224394": (
        "The people building AI earnestly believe that it could kill us all by the "
        "end of the decade. This is not a marketing stunt. If anything, many "
        "executives and senior researchers will couch their phrasing in the press "
        "to sound sensible - but I hear the same people express fear privately. "
        "No other human activity poses this level of danger."
    ),
    "2097476219138867492": (
        "If you are a lab researcher, I urge you to consider what the next few years "
        "will actually feel like. Do you want to kick off a superintelligent RL run "
        "without a rigorous understanding of its mind? Should you put your head down "
        "because “it’s happening anyway” - or take this moment to call for different "  # noqa: RUF001
        "conditions?"
    ),
}


@pytest.mark.parametrize("url", [os.getenv("X_SMOKE_TEST_URL")])
def test_x_live_renderer_output(tmp_path: Path, url: str | None) -> None:
    """Print the real X renderer output immediately before Telegram sending."""
    if not url:
        pytest.skip("set X_SMOKE_TEST_URL to run the live X renderer smoke test")

    cookie_filepath = Path("assets/cookies.txt")
    downloader = XDownloader(
        cookie_filepath=cookie_filepath if cookie_filepath.is_file() else None
    )
    candidates = downloader.extract_candidates(url)
    assert len(candidates) == 1, "use one supported X status URL"

    resolve_result = downloader.resolve(candidates[0])
    assert resolve_result.request is not None, resolve_result.failure_reason

    download_result = downloader.download(
        resolve_result.request,
        DownloadContext(output_dir=tmp_path),
    )
    assert download_result.media is not None, download_result.failure_reason

    rendered = default_renderer_registry().render(
        download_result.media,
        TELEGRAM_RENDER_CONSTRAINTS,
    )

    print("\n--- RenderedItem passed to TelegramMediaSender ---")
    print(f"rendered_item: {rendered!r}")
    print(f"provider: {rendered.source.provider}")
    print(f"media_kind: {rendered.source.media_kind}")
    print(f"provider_item_id: {rendered.source.provider_item_id}")
    print(f"source_metadata: {rendered.source.metadata!r}")
    print(f"attachments: {rendered.attachments!r}")
    print(f"text_length: {len(rendered.text)}")
    print("text:")
    print(rendered.text)
    print("--- end RenderedItem ---")

    assert rendered.text
    if expected_text := KNOWN_TEXT_POSTS.get(rendered.source.provider_item_id):
        assert rendered.source.description == expected_text
        assert expected_text in rendered.text
        assert rendered.attachments == ()
        assert rendered.source.metadata.get("text_only") is True
        assert {
            "view_count",
            "like_count",
            "repost_count",
            "comment_count",
        } <= rendered.source.metadata.keys()
