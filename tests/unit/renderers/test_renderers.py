import datetime

import pytest

from ig_reel_downloader.renderers import (
    BaseMediaRenderer,
    InstagramMediaRenderer,
    RedditMediaRenderer,
    RenderConstraints,
    RendererRegistry,
    TikTokMediaRenderer,
    XMediaRenderer,
    YouTubeMediaRenderer,
    default_renderer_registry,
)
from ig_reel_downloader.renderers.base import _compose_text
from ig_reel_downloader.repository.models import MediaAsset, MediaItem

CONSTRAINTS = RenderConstraints(
    standalone_text_max_length=4096, attachment_caption_max_length=1024
)


def media(
    *,
    provider: str = "x",
    kind: str = "post",
    metadata: dict[str, object] | None = None,
    assets: list[MediaAsset] | None = None,
) -> MediaItem:
    now = datetime.datetime.now()
    return MediaItem(
        id=f"{provider}:{kind}:1",
        provider=provider,
        media_kind=kind,
        provider_item_id="1",
        original_url="https://example.test/1",
        title="Title",
        description="Description",
        metadata=metadata or {},
        assets=assets or [],
        created_at=now,
        updated_at=now,
    )


def test_compose_text_without_description() -> None:
    assert _compose_text("Title", None, ["❤️ 12"], 1024) == "Title\n\n❤️ 12"
    assert _compose_text("Title", "", ["❤️ 12"], 1024) == "Title\n\n❤️ 12"


def test_compose_text_description_boundaries() -> None:
    exact = _compose_text("Title", "D" * 1010, ["❤️ 12"], 1024)
    assert exact == f"Title\n\n{'D' * 1010}\n\n❤️ 12"
    assert len(exact) == 1024

    truncated = _compose_text("Title", "D" * 1011, ["❤️ 12"], 1024)
    assert len(truncated) == 1024
    assert truncated.endswith("…\n\n❤️ 12")


def test_compose_text_omits_description_below_useful_truncation_minimum() -> None:
    assert _compose_text("Title", "D" * 100, [], 26) == "Title"
    assert _compose_text("Title", "D" * 100, [], 27) == f"Title\n\n{'D' * 19}…"


def test_compose_text_title_boundaries_reserve_metrics() -> None:
    exact = _compose_text("T" * 1017, None, ["❤️ 12"], 1024)
    assert exact == f"{'T' * 1017}\n\n❤️ 12"

    truncated = _compose_text("T" * 1020, "description", ["❤️ 12"], 1024)
    assert len(truncated) == 1024
    assert truncated.startswith("T" * 1016 + "…")
    assert truncated.endswith("\n\n❤️ 12")
    assert "description" not in truncated


def test_compose_text_drops_low_priority_segments_that_do_not_fit() -> None:
    assert _compose_text("Title", None, ["A", "B"], 5) == "T…\n\nA"


@pytest.mark.parametrize("max_length", [0, -1])
def test_compose_text_rejects_non_positive_limit(max_length: int) -> None:
    with pytest.raises(ValueError, match="positive"):
        _compose_text("Title", None, [], max_length)


def test_base_omits_missing_count_but_preserves_zero() -> None:
    renderer = BaseMediaRenderer()
    assert renderer.render(media(), CONSTRAINTS).text == "Title\n\nDescription"
    assert (
        renderer.render(media(metadata={"like_count": 0}), CONSTRAINTS).text
        == "Title\n\nDescription\n\n❤️ 0"
    )


def test_reddit_uses_upvote_symbol_and_legacy_key() -> None:
    renderer = RedditMediaRenderer()
    assert (
        renderer.render(
            media(
                provider="reddit", metadata={"upvote_count": 181, "comment_count": 7}
            ),
            CONSTRAINTS,
        ).text
        == "Title\n\nDescription\n\n⬆️ 181 • 💬 7"
    )
    assert (
        "⬆️ 42"
        in renderer.render(
            media(provider="reddit", metadata={"like_count": 42}), CONSTRAINTS
        ).text
    )


def test_x_displays_available_engagement_in_stable_order() -> None:
    rendered = XMediaRenderer().render(
        media(
            metadata={
                "view_count": 1234,
                "like_count": 73,
                "repost_count": 8,
                "comment_count": 12,
            }
        ),
        CONSTRAINTS,
    )
    assert rendered.text == "Title\n\nDescription\n\n👁 1,234 • ❤️ 73 • 🔁 8 • 💬 12"


def test_x_removes_repeated_description_from_title() -> None:
    description = (
        "GPT-6 Astra gives me realtime back pain physical therapy! "
        "It connected the wearable I built."
    )
    item = media(metadata={"like_count": 12})
    item.title = (
        "Rohan Kotecha - GPT-6 Astra gives me realtime back pain physical "
        "therapy! It connect..."
    )
    item.description = description

    assert XMediaRenderer().render(item, CONSTRAINTS).text == (
        f"Rohan Kotecha\n\n{description}\n\n❤️ 12"
    )


def test_default_renderer_preserves_title_containing_description_prefix() -> None:
    description = "Repeated description text that is long enough to identify"
    item = media()
    item.title = f"Creator - {description}"
    item.description = description

    assert (
        BaseMediaRenderer()
        .render(item, CONSTRAINTS)
        .text.startswith(f"Creator - {description}\n\n{description}")
    )


def test_tiktok_prefers_uploader_handle_over_channel_name() -> None:
    rendered = TikTokMediaRenderer().render(
        media(metadata={"uploader": "handle", "channel": "Display Name"}),
        CONSTRAINTS,
    )
    assert rendered.text.endswith("\n\n@handle")


def test_instagram_views_require_a_video_attachment() -> None:
    metadata = {"view_count": 100, "like_count": 5}
    image = MediaAsset(asset_index=0, asset_type="image", filepath="image.jpg")
    video = MediaAsset(asset_index=1, asset_type="video", filepath="video.mp4")
    renderer = InstagramMediaRenderer()
    assert (
        "👁"
        not in renderer.render(
            media(provider="instagram", metadata=metadata, assets=[image]), CONSTRAINTS
        ).text
    )
    assert (
        "👁 100"
        in renderer.render(
            media(provider="instagram", metadata=metadata, assets=[image, video]),
            CONSTRAINTS,
        ).text
    )


def test_youtube_renders_duration_and_omits_unavailable_metrics() -> None:
    rendered = YouTubeMediaRenderer().render(
        media(
            provider="youtube",
            kind="short",
            metadata={"channel": "Creator", "view_count": 0, "duration": 65},
        ),
        CONSTRAINTS,
    )
    assert rendered.text == ("Title\n\nDescription\n\nCreator • 👁 0 • ⏱ 1:05")
    assert "❤️" not in rendered.text
    assert "💬" not in rendered.text


def test_base_uses_original_url_when_all_display_text_is_missing() -> None:
    item = media()
    item.title = ""
    item.description = None

    assert BaseMediaRenderer().render(item, CONSTRAINTS).text == item.original_url


def test_renderers_are_explicitly_registered_for_all_downloader_types() -> None:
    registry = default_renderer_registry()
    assert isinstance(registry.get("instagram", "reel"), InstagramMediaRenderer)
    assert isinstance(registry.get("instagram", "post"), InstagramMediaRenderer)
    assert isinstance(registry.get("tiktok", "video"), TikTokMediaRenderer)
    assert isinstance(registry.get("reddit", "post"), RedditMediaRenderer)
    assert isinstance(registry.get("x", "post"), XMediaRenderer)
    assert isinstance(registry.get("youtube", "short"), YouTubeMediaRenderer)
    assert isinstance(registry.get("youtube", "video"), YouTubeMediaRenderer)


def test_registry_rejects_duplicates_and_has_default() -> None:
    registry = RendererRegistry()
    renderer = XMediaRenderer()
    registry.register("x", "post", renderer)
    with pytest.raises(ValueError, match="already registered"):
        registry.register("x", "post", renderer)
    assert isinstance(registry.get("unknown", "post"), BaseMediaRenderer)
