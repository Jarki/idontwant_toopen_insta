import asyncio
import datetime
from pathlib import Path
from typing import Any

import pytest
from telegram.error import TimedOut

from ig_reel_downloader.repository.models import MediaAsset, MediaItem
from ig_reel_downloader.telegram_renderer import TelegramMediaRenderer, _format_caption


def make_media(
    filepath: str,
    *,
    assets: list[MediaAsset] | None = None,
    description: str | None = "Description",
    title: str = "Title",
) -> MediaItem:
    now = datetime.datetime.now()
    return MediaItem(
        id="instagram:reel:ABC123",
        provider="instagram",
        media_kind="reel",
        provider_item_id="ABC123",
        original_url="https://www.instagram.com/reel/ABC123",
        title=title,
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
        self.sent_photos: list[dict[str, Any]] = []
        self.sent_groups: list[dict[str, Any]] = []
        self.sent_messages: list[dict[str, Any]] = []
        self.raise_timeout = False

    async def send_video(self, video: str, **kwargs: Any) -> None:
        if self.raise_timeout:
            raise TimedOut("timeout")
        self.sent_videos.append({"video": video, **kwargs})

    async def send_photo(self, photo: str, **kwargs: Any) -> None:
        if self.raise_timeout:
            raise TimedOut("timeout")
        self.sent_photos.append({"photo": photo, **kwargs})

    async def send_media_group(self, media: list[Any], **kwargs: Any) -> None:
        if self.raise_timeout:
            raise TimedOut("timeout")
        self.sent_groups.append({"media": media, **kwargs})

    async def send_message(self, text: str, **kwargs: Any) -> None:
        if self.raise_timeout:
            raise TimedOut("timeout")
        self.sent_messages.append({"text": text, **kwargs})


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


def test_renderer_sends_single_image_with_caption(tmp_path: Path) -> None:
    image_path = tmp_path / "image.jpg"
    image_path.write_bytes(b"image")
    image = MediaAsset(asset_index=0, asset_type="image", filepath=str(image_path))
    chat = FakeChat()
    renderer = TelegramMediaRenderer(
        telegram_media_write_timeout=120,
        telegram_read_timeout=30,
    )

    results = asyncio.run(
        renderer.render(FakeUpdate(chat), [make_media(str(image_path), assets=[image])])
    )

    assert [result.sent for result in results] == [True]
    assert chat.sent_photos[0]["photo"] == str(image_path)
    assert chat.sent_photos[0]["caption"] == "Title • ❤️ 12\n\nDescription"


def test_renderer_sends_multi_asset_item_as_media_group(tmp_path: Path) -> None:
    image_path = tmp_path / "image.jpg"
    video_path = tmp_path / "video.mp4"
    image_path.write_bytes(b"image")
    video_path.write_bytes(b"video")
    media = make_media(
        str(video_path),
        assets=[
            MediaAsset(asset_index=0, asset_type="image", filepath=str(image_path)),
            MediaAsset(asset_index=1, asset_type="video", filepath=str(video_path)),
        ],
    )
    chat = FakeChat()
    renderer = TelegramMediaRenderer(
        telegram_media_write_timeout=120,
        telegram_read_timeout=30,
    )

    results = asyncio.run(renderer.render(FakeUpdate(chat), [media]))

    assert [result.sent for result in results] == [True]
    assert len(chat.sent_groups) == 1
    assert len(chat.sent_groups[0]["media"]) == 2
    assert chat.sent_groups[0]["media"][0].caption == "Title • ❤️ 12\n\nDescription"


def test_renderer_splits_more_than_ten_assets_into_valid_media_groups(
    tmp_path: Path,
) -> None:
    assets = []
    for index in range(11):
        image_path = tmp_path / f"image-{index}.jpg"
        image_path.write_bytes(b"image")
        assets.append(
            MediaAsset(
                asset_index=index,
                asset_type="image",
                filepath=str(image_path),
            )
        )
    chat = FakeChat()
    renderer = TelegramMediaRenderer(
        telegram_media_write_timeout=120,
        telegram_read_timeout=30,
    )

    results = asyncio.run(
        renderer.render(FakeUpdate(chat), [make_media("unused", assets=assets)])
    )

    assert [result.sent for result in results] == [True]
    assert [len(group["media"]) for group in chat.sent_groups] == [9, 2]
    assert chat.sent_groups[0]["media"][0].caption == "Title • ❤️ 12\n\nDescription"
    assert chat.sent_groups[1]["media"][0].caption is None


def test_renderer_sends_reddit_text_post_without_media(tmp_path: Path) -> None:
    text_post = make_media(
        "unused",
        assets=[],
        title="Text post",
        description="Post body",
    )
    text_post.provider = "reddit"
    text_post.media_kind = "post"
    text_post.metadata = {"like_count": 42, "text_only": True}
    chat = FakeChat()
    renderer = TelegramMediaRenderer(
        telegram_media_write_timeout=120,
        telegram_read_timeout=30,
    )

    results = asyncio.run(renderer.render(FakeUpdate(chat), [text_post]))

    assert [result.sent for result in results] == [True]
    assert chat.sent_messages == [
        {
            "text": "Text post • ❤️ 42\n\nPost body",
            "write_timeout": 120,
            "read_timeout": 30,
        }
    ]
    assert chat.sent_photos == []
    assert chat.sent_videos == []
    assert chat.sent_groups == []


def test_renderer_sends_text_only_x_post_body() -> None:
    text_post = make_media(
        "unused",
        assets=[],
        title="Alice (@alice) on X",
        description="Text-only post body",
    )
    text_post.provider = "x"
    text_post.media_kind = "post"
    text_post.metadata = {"text_only": True, "body_only": True}
    chat = FakeChat()
    renderer = TelegramMediaRenderer(
        telegram_media_write_timeout=120,
        telegram_read_timeout=30,
    )

    results = asyncio.run(renderer.render(FakeUpdate(chat), [text_post]))

    assert [result.sent for result in results] == [True]
    assert chat.sent_messages == [
        {
            "text": "Text-only post body",
            "write_timeout": 120,
            "read_timeout": 30,
        }
    ]


def test_renderer_truncates_long_text_only_x_post() -> None:
    text_post = make_media("unused", assets=[], description="D" * 5000)
    text_post.provider = "x"
    text_post.media_kind = "post"
    text_post.metadata = {"text_only": True, "body_only": True}
    chat = FakeChat()
    renderer = TelegramMediaRenderer(
        telegram_media_write_timeout=120,
        telegram_read_timeout=30,
    )

    asyncio.run(renderer.render(FakeUpdate(chat), [text_post]))

    assert len(chat.sent_messages[0]["text"]) == 4096
    assert chat.sent_messages[0]["text"].endswith("…")


def test_renderer_returns_unsupported_for_empty_assets(tmp_path: Path) -> None:
    chat = FakeChat()
    renderer = TelegramMediaRenderer(
        telegram_media_write_timeout=120,
        telegram_read_timeout=30,
    )

    results = asyncio.run(
        renderer.render(
            FakeUpdate(chat),
            [make_media(str(tmp_path / "nonexistent.mp4"), assets=[])],
        )
    )

    assert results[0].sent is False
    assert results[0].failure_reason == "unsupported"
    assert chat.sent_videos == []
    assert chat.sent_groups == []


def test_renderer_propagates_timed_out_for_single_image(tmp_path: Path) -> None:
    image_path = tmp_path / "image.jpg"
    image_path.write_bytes(b"image")
    image = MediaAsset(asset_index=0, asset_type="image", filepath=str(image_path))
    chat = FakeChat()
    chat.raise_timeout = True
    renderer = TelegramMediaRenderer(
        telegram_media_write_timeout=120,
        telegram_read_timeout=30,
    )

    with pytest.raises(TimedOut):
        asyncio.run(
            renderer.render(
                FakeUpdate(chat), [make_media(str(image_path), assets=[image])]
            )
        )


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


def test_format_caption_no_description() -> None:
    media = make_media("fake.mp4", description=None)
    assert _format_caption(media) == "Title • ❤️ 12"


def test_format_caption_empty_description() -> None:
    media = make_media("fake.mp4", description="")
    assert _format_caption(media) == "Title • ❤️ 12"


def test_format_caption_long_description_truncated() -> None:
    media = make_media("fake.mp4", description="D" * 2000)
    caption = _format_caption(media)
    assert len(caption) == 1024
    assert caption.startswith("Title • ❤️ 12")
    assert caption.endswith("…")


def test_format_caption_exact_boundary_no_truncation() -> None:
    # Title (5) + likes (8) + \n\n (2) + 1009-char description = 1024 exactly
    media = make_media("fake.mp4", description="D" * 1009)
    caption = _format_caption(media)
    assert caption == f"Title • ❤️ 12\n\n{'D' * 1009}"
    assert len(caption) == 1024


def test_format_caption_just_over_boundary_truncates_description() -> None:
    media = make_media("fake.mp4", description="D" * 1010)
    caption = _format_caption(media)
    assert len(caption) == 1024
    assert caption.startswith("Title • ❤️ 12\n\n")
    assert caption.endswith("…")


def test_format_caption_long_title_truncates_title() -> None:
    media = make_media("fake.mp4", title="T" * 1020, description=None)
    caption = _format_caption(media)
    assert len(caption) == 1024
    assert caption.startswith("T" * 1015 + "…")
    assert caption.endswith(" • ❤️ 12")


def test_format_caption_long_title_drops_description() -> None:
    media = make_media("fake.mp4", title="T" * 1017, description="D" * 100)
    caption = _format_caption(media)
    assert len(caption) == 1024
    assert caption.startswith("T" * 1015 + "…")
    assert "\n\n" not in caption
    assert caption.endswith(" • ❤️ 12")


def test_format_caption_title_exact_boundary_no_truncation() -> None:
    media = make_media("fake.mp4", title="T" * 1016, description=None)
    caption = _format_caption(media)
    assert caption == f"{'T' * 1016} • ❤️ 12"
    assert len(caption) == 1024
