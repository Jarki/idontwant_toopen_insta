import asyncio
import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from telegram import InputMediaVideo
from telegram.error import BadRequest, TimedOut

from ig_reel_downloader.renderers import (
    RenderedItem,
    UnsupportedMediaError,
    default_renderer_registry,
)
from ig_reel_downloader.repository.models import MediaAsset, MediaItem
from ig_reel_downloader.telegram_sender import (
    TELEGRAM_RENDER_CONSTRAINTS,
    MediaRenderResult,
    MediaRenderTimedOut,
    TelegramMediaSender,
    _validate_rendered_item,
)


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
        self.timeout_message_number: int | None = None
        self.timeout_group_number: int | None = None
        self.invalid_file_ids: set[str] = set()

    async def send_video(self, video: str, **kwargs: Any) -> Any:
        if self.raise_timeout:
            raise TimedOut("timeout")
        self.sent_videos.append({"video": video, **kwargs})
        if video in self.invalid_file_ids:
            raise BadRequest("invalid file id")
        return SimpleNamespace(
            video=SimpleNamespace(file_id="returned-video-file-id"), photo=[]
        )

    async def send_photo(self, photo: str, **kwargs: Any) -> Any:
        if self.raise_timeout:
            raise TimedOut("timeout")
        self.sent_photos.append({"photo": photo, **kwargs})
        if photo in self.invalid_file_ids:
            raise BadRequest("invalid file id")
        return SimpleNamespace(
            video=None,
            photo=[SimpleNamespace(file_id="returned-photo-file-id")],
        )

    async def send_media_group(self, media: list[Any], **kwargs: Any) -> list[Any]:
        group_number = len(self.sent_groups) + 1
        if self.raise_timeout or group_number == self.timeout_group_number:
            raise TimedOut("timeout")
        self.sent_groups.append({"media": media, **kwargs})
        if any(item.media in self.invalid_file_ids for item in media):
            raise BadRequest("invalid file id")
        return [
            SimpleNamespace(
                video=(
                    SimpleNamespace(file_id=f"returned-video-file-id-{index}")
                    if isinstance(item, InputMediaVideo)
                    else None
                ),
                photo=(
                    []
                    if isinstance(item, InputMediaVideo)
                    else [SimpleNamespace(file_id=f"returned-photo-file-id-{index}")]
                ),
            )
            for index, item in enumerate(media)
        ]

    async def send_message(self, text: str, **kwargs: Any) -> None:
        message_number = len(self.sent_messages) + 1
        if self.raise_timeout or message_number == self.timeout_message_number:
            raise TimedOut("timeout")
        self.sent_messages.append({"text": text, **kwargs})


class FakeUpdate:
    def __init__(self, chat: FakeChat | None) -> None:
        self.effective_chat = chat


async def _send_media(
    sender: TelegramMediaSender,
    update: FakeUpdate,
    media_items: list[MediaItem],
) -> list[MediaRenderResult]:
    registry = default_renderer_registry()
    rendered_items = [
        registry.render(media, TELEGRAM_RENDER_CONSTRAINTS) for media in media_items
    ]
    return await sender.send(update, rendered_items)


def test_sender_sends_single_video_with_current_caption(tmp_path: Path) -> None:
    media_file = tmp_path / "ABC123.mp4"
    media_file.write_bytes(b"video")
    chat = FakeChat()
    sender = TelegramMediaSender(
        telegram_media_write_timeout=120,
        telegram_read_timeout=30,
    )

    results = asyncio.run(
        _send_media(sender, FakeUpdate(chat), [make_media(str(media_file))])
    )

    assert [result.sent for result in results] == [True]
    assert chat.sent_videos[0]["video"] == str(media_file)
    assert chat.sent_videos[0]["caption"] == "Title\n\nDescription\n\n❤️ 12"
    assert chat.sent_videos[0]["write_timeout"] == 120
    assert chat.sent_videos[0]["read_timeout"] == 30
    assert results[0].telegram_file_ids == {0: "returned-video-file-id"}


def test_sender_reuses_stored_telegram_file_id_without_opening_file(
    tmp_path: Path,
) -> None:
    missing_file = tmp_path / "missing.mp4"
    asset = MediaAsset(
        asset_index=0,
        asset_type="video",
        filepath=str(missing_file),
        telegram_file_id="stored-video-file-id",
    )
    chat = FakeChat()
    sender = TelegramMediaSender(120, 30)

    results = asyncio.run(
        _send_media(
            sender, FakeUpdate(chat), [make_media(str(missing_file), assets=[asset])]
        )
    )

    assert chat.sent_videos[0]["video"] == "stored-video-file-id"
    assert results[0].telegram_file_ids == {0: "returned-video-file-id"}


def test_sender_reuploads_when_stored_telegram_file_id_is_invalid(
    tmp_path: Path,
) -> None:
    media_file = tmp_path / "video.mp4"
    media_file.write_bytes(b"video")
    asset = MediaAsset(
        asset_index=0,
        asset_type="video",
        filepath=str(media_file),
        telegram_file_id="invalid-video-file-id",
    )
    chat = FakeChat()
    chat.invalid_file_ids.add("invalid-video-file-id")
    sender = TelegramMediaSender(120, 30)

    results = asyncio.run(
        _send_media(
            sender, FakeUpdate(chat), [make_media(str(media_file), assets=[asset])]
        )
    )

    assert [sent["video"] for sent in chat.sent_videos] == [
        "invalid-video-file-id",
        str(media_file),
    ]
    assert results[0].telegram_file_ids == {0: "returned-video-file-id"}


def test_sender_sends_multiple_items_independently(tmp_path: Path) -> None:
    first = tmp_path / "first.mp4"
    second = tmp_path / "second.mp4"
    first.write_bytes(b"first")
    second.write_bytes(b"second")
    chat = FakeChat()
    sender = TelegramMediaSender(
        telegram_media_write_timeout=120,
        telegram_read_timeout=30,
    )

    results = asyncio.run(
        _send_media(
            sender,
            FakeUpdate(chat),
            [make_media(str(first)), make_media(str(second))],
        )
    )

    assert [result.sent for result in results] == [True, True]
    assert chat.sent_groups == []
    assert [sent["video"] for sent in chat.sent_videos] == [str(first), str(second)]
    assert [sent["caption"] for sent in chat.sent_videos] == [
        "Title\n\nDescription\n\n❤️ 12",
        "Title\n\nDescription\n\n❤️ 12",
    ]
    assert results[0].telegram_file_ids == {0: "returned-video-file-id"}
    assert results[1].telegram_file_ids == {0: "returned-video-file-id"}


def test_sender_media_group_reuses_stored_telegram_file_ids(
    tmp_path: Path,
) -> None:
    assets = [
        MediaAsset(
            asset_index=0,
            asset_type="video",
            filepath=str(tmp_path / "missing-video.mp4"),
            telegram_file_id="stored-video-file-id",
        ),
        MediaAsset(
            asset_index=1,
            asset_type="image",
            filepath=str(tmp_path / "missing-image.jpg"),
            telegram_file_id="stored-photo-file-id",
        ),
    ]
    chat = FakeChat()
    sender = TelegramMediaSender(120, 30)

    results = asyncio.run(
        _send_media(sender, FakeUpdate(chat), [make_media("unused", assets=assets)])
    )

    assert [item.media for item in chat.sent_groups[0]["media"]] == [
        "stored-video-file-id",
        "stored-photo-file-id",
    ]
    assert results[0].telegram_file_ids == {
        0: "returned-video-file-id-0",
        1: "returned-photo-file-id-1",
    }


def test_sender_reuploads_media_group_when_stored_file_id_is_invalid(
    tmp_path: Path,
) -> None:
    video_path = tmp_path / "video.mp4"
    image_path = tmp_path / "image.jpg"
    video_path.write_bytes(b"video")
    image_path.write_bytes(b"image")
    assets = [
        MediaAsset(
            asset_index=0,
            asset_type="video",
            filepath=str(video_path),
            telegram_file_id="invalid-video-file-id",
        ),
        MediaAsset(
            asset_index=1,
            asset_type="image",
            filepath=str(image_path),
            telegram_file_id="stored-photo-file-id",
        ),
    ]
    chat = FakeChat()
    chat.invalid_file_ids.add("invalid-video-file-id")
    sender = TelegramMediaSender(120, 30)

    results = asyncio.run(
        _send_media(sender, FakeUpdate(chat), [make_media("unused", assets=assets)])
    )

    assert len(chat.sent_groups) == 2
    assert [item.media for item in chat.sent_groups[0]["media"]] == [
        "invalid-video-file-id",
        "stored-photo-file-id",
    ]
    assert all(
        item.media not in {"invalid-video-file-id", "stored-photo-file-id"}
        for item in chat.sent_groups[1]["media"]
    )
    assert results[0].telegram_file_ids == {
        0: "returned-video-file-id-0",
        1: "returned-photo-file-id-1",
    }


def test_sender_sends_single_image_with_caption(tmp_path: Path) -> None:
    image_path = tmp_path / "image.jpg"
    image_path.write_bytes(b"image")
    image = MediaAsset(asset_index=0, asset_type="image", filepath=str(image_path))
    chat = FakeChat()
    sender = TelegramMediaSender(
        telegram_media_write_timeout=120,
        telegram_read_timeout=30,
    )

    results = asyncio.run(
        _send_media(
            sender, FakeUpdate(chat), [make_media(str(image_path), assets=[image])]
        )
    )

    assert [result.sent for result in results] == [True]
    assert chat.sent_photos[0]["photo"] == str(image_path)
    assert chat.sent_photos[0]["caption"] == "Title\n\nDescription\n\n❤️ 12"


def test_sender_sends_multi_asset_item_as_media_group(tmp_path: Path) -> None:
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
    sender = TelegramMediaSender(
        telegram_media_write_timeout=120,
        telegram_read_timeout=30,
    )

    results = asyncio.run(_send_media(sender, FakeUpdate(chat), [media]))

    assert [result.sent for result in results] == [True]
    assert len(chat.sent_groups) == 1
    assert len(chat.sent_groups[0]["media"]) == 2
    assert chat.sent_groups[0]["media"][0].caption == "Title\n\nDescription\n\n❤️ 12"


def test_sender_splits_more_than_ten_assets_into_valid_media_groups(
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
    sender = TelegramMediaSender(
        telegram_media_write_timeout=120,
        telegram_read_timeout=30,
    )

    results = asyncio.run(
        _send_media(sender, FakeUpdate(chat), [make_media("unused", assets=assets)])
    )

    assert [result.sent for result in results] == [True]
    assert [len(group["media"]) for group in chat.sent_groups] == [9, 2]
    assert chat.sent_groups[0]["media"][0].caption == "Title\n\nDescription\n\n❤️ 12"
    assert chat.sent_groups[1]["media"][0].caption is None


def test_sender_preserves_file_ids_when_later_media_group_times_out(
    tmp_path: Path,
) -> None:
    assets = []
    for index in range(11):
        image_path = tmp_path / f"image-{index}.jpg"
        image_path.write_bytes(b"image")
        assets.append(
            MediaAsset(asset_index=index, asset_type="image", filepath=str(image_path))
        )
    chat = FakeChat()
    chat.timeout_group_number = 2
    sender = TelegramMediaSender(120, 30)

    with pytest.raises(MediaRenderTimedOut) as exc_info:
        asyncio.run(
            _send_media(sender, FakeUpdate(chat), [make_media("unused", assets=assets)])
        )

    assert exc_info.value.completed_results == []
    assert len(exc_info.value.partial_results) == 1
    partial = exc_info.value.partial_results[0]
    assert partial.sent is False
    assert partial.telegram_file_ids == {
        index: f"returned-photo-file-id-{index}" for index in range(9)
    }


def test_sender_validates_rendered_attachments_against_their_source() -> None:
    source = make_media("video.mp4")
    unknown_asset = source.assets[0].model_copy(update={"filepath": "other.mp4"})

    with pytest.raises(ValueError, match="outside its source"):
        _validate_rendered_item(
            RenderedItem(source=source, text="caption", attachments=(unknown_asset,))
        )

    with pytest.raises(ValueError, match="dropped all"):
        _validate_rendered_item(
            RenderedItem(source=source, text="text instead", attachments=())
        )


def test_sender_sends_reddit_text_post_without_media(tmp_path: Path) -> None:
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
    sender = TelegramMediaSender(
        telegram_media_write_timeout=120,
        telegram_read_timeout=30,
    )

    results = asyncio.run(_send_media(sender, FakeUpdate(chat), [text_post]))

    assert [result.sent for result in results] == [True]
    assert chat.sent_messages == [
        {
            "text": "Text post\n\nPost body\n\n⬆️ 42",
            "write_timeout": 120,
            "read_timeout": 30,
        }
    ]
    assert chat.sent_photos == []
    assert chat.sent_videos == []
    assert chat.sent_groups == []


def test_sender_sends_text_only_x_post_body() -> None:
    text_post = make_media(
        "unused",
        assets=[],
        title="Alice (@alice) on X",
        description="Text-only post body",
    )
    text_post.provider = "x"
    text_post.media_kind = "post"
    text_post.metadata = {"like_count": 73, "text_only": True}
    chat = FakeChat()
    sender = TelegramMediaSender(
        telegram_media_write_timeout=120,
        telegram_read_timeout=30,
    )

    results = asyncio.run(_send_media(sender, FakeUpdate(chat), [text_post]))

    assert [result.sent for result in results] == [True]
    assert chat.sent_messages == [
        {
            "text": "Alice (@alice) on X\n\nText-only post body\n\n❤️ 73",
            "write_timeout": 120,
            "read_timeout": 30,
        }
    ]


def test_sender_reports_completed_items_when_later_send_times_out() -> None:
    first = make_media("unused", assets=[], title="First", description="Body")
    first.metadata = {"text_only": True}
    second = first.model_copy(
        deep=True,
        update={"id": "x:post:second", "provider_item_id": "second"},
    )
    chat = FakeChat()
    chat.timeout_message_number = 2
    sender = TelegramMediaSender(120, 30)

    with pytest.raises(MediaRenderTimedOut) as exc_info:
        asyncio.run(_send_media(sender, FakeUpdate(chat), [first, second]))

    assert [result.media.id for result in exc_info.value.completed_results] == [
        first.id
    ]
    assert [result.sent for result in exc_info.value.completed_results] == [True]


def test_sender_truncates_long_text_only_x_post() -> None:
    text_post = make_media("unused", assets=[], description="D" * 5000)
    text_post.provider = "x"
    text_post.media_kind = "post"
    text_post.metadata = {"like_count": 73, "text_only": True}
    chat = FakeChat()
    sender = TelegramMediaSender(
        telegram_media_write_timeout=120,
        telegram_read_timeout=30,
    )

    asyncio.run(_send_media(sender, FakeUpdate(chat), [text_post]))

    assert len(chat.sent_messages[0]["text"]) == 4096
    assert chat.sent_messages[0]["text"].endswith("…\n\n❤️ 73")


def test_registry_rejects_empty_non_text_media(tmp_path: Path) -> None:
    chat = FakeChat()
    sender = TelegramMediaSender(120, 30)

    with pytest.raises(UnsupportedMediaError):
        asyncio.run(
            _send_media(
                sender,
                FakeUpdate(chat),
                [make_media(str(tmp_path / "nonexistent.mp4"), assets=[])],
            )
        )

    assert chat.sent_videos == []
    assert chat.sent_groups == []


def test_sender_propagates_timed_out_for_single_image(tmp_path: Path) -> None:
    image_path = tmp_path / "image.jpg"
    image_path.write_bytes(b"image")
    image = MediaAsset(asset_index=0, asset_type="image", filepath=str(image_path))
    chat = FakeChat()
    chat.raise_timeout = True
    sender = TelegramMediaSender(
        telegram_media_write_timeout=120,
        telegram_read_timeout=30,
    )

    with pytest.raises(TimedOut):
        asyncio.run(
            _send_media(
                sender,
                FakeUpdate(chat),
                [make_media(str(image_path), assets=[image])],
            )
        )


def test_sender_propagates_timed_out_for_app_friendly_message(
    tmp_path: Path,
) -> None:
    media_file = tmp_path / "ABC123.mp4"
    media_file.write_bytes(b"video")
    chat = FakeChat()
    chat.raise_timeout = True
    sender = TelegramMediaSender(
        telegram_media_write_timeout=120,
        telegram_read_timeout=30,
    )

    with pytest.raises(TimedOut):
        asyncio.run(
            _send_media(sender, FakeUpdate(chat), [make_media(str(media_file))])
        )
