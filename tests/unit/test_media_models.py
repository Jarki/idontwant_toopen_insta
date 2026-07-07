import datetime
from pathlib import Path

from ig_reel_downloader.downloaders.base import ProviderItemRef
from ig_reel_downloader.repository.models import MediaAsset, MediaItem


def make_media_item() -> MediaItem:
    now = datetime.datetime.now()
    return MediaItem(
        id="instagram:reel:ABC123",
        provider="instagram",
        media_kind="reel",
        provider_item_id="ABC123",
        original_url="https://www.instagram.com/reel/ABC123",
        title="A title",
        description="A description",
        metadata={"like_count": 7, "comments": []},
        assets=[
            MediaAsset(
                asset_index=0,
                asset_type="video",
                filepath=str(Path("output") / "ABC123.mp4"),
                mime_type="video/mp4",
                width=1080,
                height=1920,
                duration_seconds=12.5,
                file_size_bytes=12345,
            )
        ],
        created_at=now,
        updated_at=now,
    )


def test_provider_item_ref_media_id() -> None:
    ref = ProviderItemRef(
        provider="instagram",
        media_kind="reel",
        provider_item_id="ABC123",
    )

    assert ref.media_id == "instagram:reel:ABC123"


def test_media_item_metadata_is_domain_dict() -> None:
    media = make_media_item()

    assert media.metadata["like_count"] == 7
    assert "metadata_json" not in MediaItem.model_fields


def test_media_item_contains_one_video_asset_for_reel() -> None:
    media = make_media_item()

    assert media.assets[0].asset_type == "video"
    assert media.assets[0].asset_index == 0
