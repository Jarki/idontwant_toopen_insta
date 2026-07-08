import asyncio
import datetime
from pathlib import Path
from typing import Any

import pytest
from telegram.error import TimedOut

from ig_reel_downloader.repository.models import MediaAsset, MediaItem
from ig_reel_downloader.telegram_renderer import TelegramMediaRenderer


def make_media(
    filepath: str,
    *,
    assets: list[MediaAsset] | None = None,
    description: str | None = "Description",
) -> MediaItem:
    now = datetime.datetime.now()
    return MediaItem(
        id="instagram:reel:ABC123",
        provider="instagram",
        media_kind="reel",
        provider_item_id="ABC123",
        original_url="https://www.instagram.com/reel/ABC123",
        title="Title",
        description=description,
        metadata={"like_count": 12, "comments": []},
        assets=assets
        if assets is not None
        else [MediaAsset(asset_index=0, asset_type="video", filepath=filepath)],
        created_at=now,
        updated_at=now,
    )


class FakeChat:
    def __init__(self) -> None:
        self.sent_videos: list[dict[str, Any]] = []
        self.sent_groups: list[dict[str, Any]] = []
        self.raise_timeout = False

    async def send_video(self, video: str, **kwargs: Any) -> None:
        if self.raise_timeout:
            raise TimedOut("timeout")
        self.sent_videos.append({"video": video, **kwargs})

    async def send_media_group(self, media: list[Any], **kwargs: Any) -> None:
        if self.raise_timeout:
            raise TimedOut("timeout")
        self.sent_groups.append({"media": media, **kwargs})


class FakeUpdate:
    def __init__(self, chat: FakeChat | None) -> None:
        self.effective_chat = chat


def test_renderer_sends_single_video_with_current_caption(tmp_path: Path) -> None:
    media_file = tmp_path / "ABC123.mp4"
    media_file.write_bytes(b"video")
    chat = FakeChat()
    renderer = TelegramMediaRenderer(
        telegram_media_write_timeout=120,
        telegram_read_timeout=30,
    )

    results = asyncio.run(
        renderer.render(FakeUpdate(chat), [make_media(str(media_file))])
    )

    assert [result.sent for result in results] == [True]
    assert chat.sent_videos[0]["video"] == str(media_file)
    assert chat.sent_videos[0]["caption"] == "Title • ❤️ 12\n\nDescription"
    assert chat.sent_videos[0]["write_timeout"] == 120
    assert chat.sent_videos[0]["read_timeout"] == 30


def test_renderer_sends_multiple_videos_as_media_group(tmp_path: Path) -> None:
    first = tmp_path / "first.mp4"
    second = tmp_path / "second.mp4"
    first.write_bytes(b"first")
    second.write_bytes(b"second")
    chat = FakeChat()
    renderer = TelegramMediaRenderer(
        telegram_media_write_timeout=120,
        telegram_read_timeout=30,
    )

    results = asyncio.run(
        renderer.render(
            FakeUpdate(chat), [make_media(str(first)), make_media(str(second))]
        )
    )

    assert [result.sent for result in results] == [True, True]
    assert len(chat.sent_groups) == 1
    assert len(chat.sent_groups[0]["media"]) == 2


def test_renderer_returns_unsupported_for_non_video_shape(tmp_path: Path) -> None:
    image = MediaAsset(
        asset_index=0,
        asset_type="image",
        filepath=str(tmp_path / "image.jpg"),
    )
    chat = FakeChat()
    renderer = TelegramMediaRenderer(
        telegram_media_write_timeout=120,
        telegram_read_timeout=30,
    )

    results = asyncio.run(
        renderer.render(
            FakeUpdate(chat),
            [make_media(str(tmp_path / "image.jpg"), assets=[image])],
        )
    )

    assert results[0].sent is False
    assert results[0].failure_reason == "unsupported"
    assert chat.sent_videos == []
    assert chat.sent_groups == []


def test_renderer_propagates_timed_out_for_app_friendly_message(
    tmp_path: Path,
) -> None:
    media_file = tmp_path / "ABC123.mp4"
    media_file.write_bytes(b"video")
    chat = FakeChat()
    chat.raise_timeout = True
    renderer = TelegramMediaRenderer(
        telegram_media_write_timeout=120,
        telegram_read_timeout=30,
    )

    with pytest.raises(TimedOut):
        asyncio.run(renderer.render(FakeUpdate(chat), [make_media(str(media_file))]))
