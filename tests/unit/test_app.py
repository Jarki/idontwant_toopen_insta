from __future__ import annotations

import asyncio
import datetime
from dataclasses import dataclass
from unittest.mock import patch

import pytest

from ig_reel_downloader import app as app_module
from ig_reel_downloader.downloaders import (
    DownloadContext,
    MediaDownloadResult,
    ProviderItemRef,
    ResolvedMediaRequest,
    ResolveResult,
    UrlCandidate,
)
from ig_reel_downloader.media_fetch import MediaFetchResult
from ig_reel_downloader.repository.models import MediaAsset, MediaItem
from ig_reel_downloader.telegram_renderer import MediaRenderResult


class FakeApplication:
    def __init__(self) -> None:
        self.handlers: list[object] = []

    def add_handler(self, handler: object) -> None:
        self.handlers.append(handler)

    def run_polling(self) -> None:
        pass


class FakeApplicationBuilder:
    def token(self, token: str) -> FakeApplicationBuilder:
        return self

    def media_write_timeout(self, value: float) -> FakeApplicationBuilder:
        return self

    def read_timeout(self, value: float) -> FakeApplicationBuilder:
        return self

    def build(self) -> FakeApplication:
        return FakeApplication()


class FakeSender:
    id = 123


@dataclass
class SentAnimation:
    animation: str
    reply_to_message_id: int | None


class FakeAnimation:
    def __init__(
        self,
        file_id: str,
        file_unique_id: str | None = "unique-animation-id",
    ) -> None:
        self.file_id = file_id
        self.file_unique_id = file_unique_id


class FakeSentMessage:
    def __init__(self, file_id: str) -> None:
        self.animation = FakeAnimation(file_id)


class FakeMessage:
    def __init__(
        self,
        text: str | None,
        reply_to_message: FakeMessage | None = None,
        animation: FakeAnimation | None = None,
    ) -> None:
        self.text = text
        self.message_id = 42
        self.reply_to_message = reply_to_message
        self.animation = animation


class FakeChat:
    def __init__(self) -> None:
        self.sent_messages: list[str] = []
        self.sent_animations: list[SentAnimation] = []

    async def send_message(self, text: str) -> None:
        self.sent_messages.append(text)

    async def send_animation(
        self,
        animation: str,
        reply_to_message_id: int | None = None,
        **kwargs: object,
    ) -> FakeSentMessage:
        self.sent_animations.append(
            SentAnimation(
                animation=animation,
                reply_to_message_id=reply_to_message_id,
            )
        )
        return FakeSentMessage(file_id=f"file_id:{animation}")


class FakeUpdate:
    def __init__(
        self,
        text: str | None,
        chat: FakeChat | None = None,
        reply_to_message: FakeMessage | None = None,
    ) -> None:
        self.message = (
            FakeMessage(text, reply_to_message=reply_to_message)
            if text is not None
            else None
        )
        self.effective_chat = chat
        self.effective_sender = FakeSender()


class FakeRegistry:
    def __init__(
        self,
        candidates: list[UrlCandidate],
        events: list[str],
    ) -> None:
        self.candidates = candidates
        self.events = events
        self.texts: list[str] = []

    def extract_candidates(self, text: str) -> list[UrlCandidate]:
        self.events.append("registry")
        self.texts.append(text)
        return self.candidates


class FakeRepository:
    def __init__(self) -> None:
        self.judgmental_file_ids: list[str] = []
        self.added_judgmental_file_ids: list[tuple[str, str | None]] = []
        self.deleted_judgmental_file_ids: list[str] = []

    def add_judgmental_animation_file_id(
        self,
        file_id: str,
        file_unique_id: str | None,
    ) -> None:
        self.added_judgmental_file_ids.append((file_id, file_unique_id))
        if file_id not in self.judgmental_file_ids:
            self.judgmental_file_ids.append(file_id)

    def list_judgmental_animation_file_ids(self) -> list[str]:
        return list(self.judgmental_file_ids)

    def delete_judgmental_animation_file_id(self, file_id: str) -> None:
        self.deleted_judgmental_file_ids.append(file_id)
        self.judgmental_file_ids = [
            stored_file_id
            for stored_file_id in self.judgmental_file_ids
            if stored_file_id != file_id
        ]


class FakeFetchService:
    def __init__(
        self,
        results: dict[str, MediaFetchResult],
        events: list[str],
        repository: FakeRepository | None = None,
    ) -> None:
        self.results = results
        self.events = events
        self.repository = repository or FakeRepository()
        self.candidates: list[UrlCandidate] = []

    def fetch(self, candidate: UrlCandidate) -> MediaFetchResult:
        self.events.append(f"fetch:{candidate.url}")
        self.candidates.append(candidate)
        return self.results[candidate.url]


class FakeRenderer:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.updates: list[FakeUpdate] = []
        self.media_items: list[list[MediaItem]] = []

    async def render(
        self,
        update: FakeUpdate,
        media_items: list[MediaItem],
    ) -> list[MediaRenderResult]:
        self.events.append("renderer")
        self.updates.append(update)
        self.media_items.append(media_items)
        return [MediaRenderResult(media=item, sent=True) for item in media_items]


class FakeDownloader:
    provider = "instagram"
    media_kind = "reel"

    def extract_candidates(self, text: str) -> list[UrlCandidate]:
        return []

    def resolve(self, candidate: UrlCandidate) -> ResolveResult:
        return ResolveResult(
            request=ResolvedMediaRequest(
                url=candidate.url,
                downloader=self,
                provider_item_ref=ProviderItemRef("instagram", "reel", "ABC123"),
                normalized_url=candidate.normalized_url,
            )
        )

    def download(
        self,
        request: ResolvedMediaRequest,
        context: DownloadContext,
    ) -> MediaDownloadResult:
        raise AssertionError("not used")


def make_media(url: str, provider_item_id: str) -> MediaItem:
    now = datetime.datetime.now()
    return MediaItem(
        id=f"instagram:reel:{provider_item_id}",
        provider="instagram",
        media_kind="reel",
        provider_item_id=provider_item_id,
        original_url=url,
        title="Title",
        description=None,
        metadata={"like_count": 0, "comments": []},
        assets=[MediaAsset(asset_index=0, asset_type="video", filepath="video.mp4")],
        created_at=now,
        updated_at=now,
    )


def make_candidate(url: str, provider_item_id: str) -> UrlCandidate:
    return UrlCandidate(
        url=url,
        start=0,
        end=len(url),
        downloader=FakeDownloader(),
        provider="instagram",
        link_type="reel",
        normalized_url=url,
        local_ref=ProviderItemRef("instagram", "reel", provider_item_id),
    )


def build_app(
    monkeypatch: pytest.MonkeyPatch,
    registry: FakeRegistry,
    fetch_service: FakeFetchService,
    renderer: FakeRenderer,
) -> app_module.IgReelDownloaderApp:
    monkeypatch.setattr(app_module, "ApplicationBuilder", FakeApplicationBuilder)
    return app_module.IgReelDownloaderApp(
        "telegram-token",
        registry,
        fetch_service,
        renderer,
    )


def test_message_handler_uses_registry_fetch_service_and_renderer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first_url = "https://www.instagram.com/reel/ABC123"
    second_url = "https://www.instagram.com/reel/DEF456"
    first_candidate = make_candidate(first_url, "ABC123")
    second_candidate = make_candidate(second_url, "DEF456")
    first_media = make_media(first_url, "ABC123")
    events: list[str] = []
    registry = FakeRegistry([first_candidate, second_candidate], events)
    fetch_service = FakeFetchService(
        {
            first_url: MediaFetchResult(media=first_media, url=first_url),
            second_url: MediaFetchResult(
                media=None,
                url=second_url,
                failure_reason="auth",
            ),
        },
        events,
    )
    renderer = FakeRenderer(events)
    app = build_app(monkeypatch, registry, fetch_service, renderer)
    chat = FakeChat()
    update = FakeUpdate(f"reels: {first_url} {second_url}", chat)

    asyncio.run(app._message_handler(update, object()))

    assert registry.texts == [f"reels: {first_url} {second_url}"]
    # Both candidates fetched (order is non-deterministic with threads).
    assert len(fetch_service.candidates) == 2
    assert first_candidate in fetch_service.candidates
    assert second_candidate in fetch_service.candidates
    assert renderer.updates == [update]
    assert renderer.media_items == [[first_media]]
    assert chat.sent_messages == [
        "Could not download (auth expired): https://www.instagram.com/reel/DEF456"
    ]
    assert events[0] == "registry"
    assert set(events[1:-1]) == {f"fetch:{first_url}", f"fetch:{second_url}"}
    assert events[-1] == "renderer"


def test_message_handler_sends_auth_failure_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    url = "https://www.instagram.com/reel/ABC123"
    candidate = make_candidate(url, "ABC123")
    events: list[str] = []
    registry = FakeRegistry([candidate], events)
    fetch_service = FakeFetchService(
        {url: MediaFetchResult(media=None, url=url, failure_reason="auth")},
        events,
    )
    renderer = FakeRenderer(events)
    app = build_app(monkeypatch, registry, fetch_service, renderer)
    chat = FakeChat()

    asyncio.run(app._message_handler(FakeUpdate(url, chat), object()))

    assert renderer.media_items == [[]]
    assert chat.sent_messages == [
        "Could not download (auth expired): https://www.instagram.com/reel/ABC123"
    ]


def test_message_handler_does_not_send_error_for_skipped_fetch_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    url = "https://youtu.be/long"
    candidate = make_candidate(url, "long")
    events: list[str] = []
    registry = FakeRegistry([candidate], events)
    fetch_service = FakeFetchService(
        {url: MediaFetchResult(media=None, url=url, skipped=True)},
        events,
    )
    renderer = FakeRenderer(events)
    app = build_app(monkeypatch, registry, fetch_service, renderer)
    chat = FakeChat()

    asyncio.run(app._message_handler(FakeUpdate(url, chat), object()))

    assert chat.sent_messages == []
    assert renderer.media_items == []


def test_add_judgmental_command_stores_replied_animation_file_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    registry = FakeRegistry([], events)
    repository = FakeRepository()
    fetch_service = FakeFetchService({}, events, repository=repository)
    renderer = FakeRenderer(events)
    app = build_app(monkeypatch, registry, fetch_service, renderer)
    chat = FakeChat()
    replied_animation = FakeMessage(
        None,
        animation=FakeAnimation("telegram-file-id", "telegram-unique-id"),
    )
    update = FakeUpdate("/add-judgmental", chat, reply_to_message=replied_animation)

    asyncio.run(app._add_judgmental_handler(update, object()))

    assert repository.added_judgmental_file_ids == [
        ("telegram-file-id", "telegram-unique-id")
    ]
    assert repository.judgmental_file_ids == ["telegram-file-id"]
    assert chat.sent_messages == ["Saved judgmental GIF."]


def test_add_judgmental_command_requires_replied_animation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    registry = FakeRegistry([], events)
    repository = FakeRepository()
    fetch_service = FakeFetchService({}, events, repository=repository)
    renderer = FakeRenderer(events)
    app = build_app(monkeypatch, registry, fetch_service, renderer)
    chat = FakeChat()
    update = FakeUpdate("/add-judgmental", chat)

    asyncio.run(app._add_judgmental_handler(update, object()))

    assert repository.added_judgmental_file_ids == []
    assert chat.sent_messages == [
        "Reply to a Telegram GIF/animation with /add-judgmental and I will remember it."
    ]


def test_message_handler_prefers_stored_judgmental_file_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    url = "https://www.instagram.com/reel/ABC123"
    candidate = make_candidate(url, "ABC123")
    events: list[str] = []
    registry = FakeRegistry([candidate], events)
    repository = FakeRepository()
    repository.judgmental_file_ids = ["stored-file-id"]
    fetch_service = FakeFetchService({}, events, repository=repository)
    renderer = FakeRenderer(events)

    monkeypatch.setattr(app_module, "ApplicationBuilder", FakeApplicationBuilder)
    app = app_module.IgReelDownloaderApp(
        "telegram-token",
        registry,
        fetch_service,
        renderer,
        judgmental_chance=0.5,
        judgmental_gifs=["https://example.com/broken.gif"],
    )
    chat = FakeChat()
    update = FakeUpdate(url, chat)

    with (
        patch.object(app_module.judgmental_module, "should_fire", return_value=True),
        patch.object(
            app_module.judgmental_module,
            "pick_gif",
            return_value="stored-file-id",
        ),
    ):
        asyncio.run(app._message_handler(update, object()))

    assert [animation.animation for animation in chat.sent_animations] == [
        "stored-file-id"
    ]
    assert events == ["registry"]
    assert app._judgmental_file_ids == {}


def test_message_handler_sends_judgmental_gif_when_chance_triggers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    url = "https://www.instagram.com/reel/ABC123"
    candidate = make_candidate(url, "ABC123")
    gif_url = "https://example.com/judgmental.gif"
    events: list[str] = []
    registry = FakeRegistry([candidate], events)
    fetch_service = FakeFetchService({}, events)
    renderer = FakeRenderer(events)

    monkeypatch.setattr(app_module, "ApplicationBuilder", FakeApplicationBuilder)
    app = app_module.IgReelDownloaderApp(
        "telegram-token",
        registry,
        fetch_service,
        renderer,
        judgmental_chance=0.5,
        judgmental_gifs=[gif_url],
    )

    chat = FakeChat()
    update = FakeUpdate(url, chat)

    with (
        patch.object(app_module.judgmental_module, "should_fire", return_value=True),
        patch.object(app_module.judgmental_module, "pick_gif", return_value=gif_url),
    ):
        asyncio.run(app._message_handler(update, object()))

    # Should have sent the GIF as a reply, not downloaded
    assert len(chat.sent_animations) == 1
    anim = chat.sent_animations[0]
    assert anim.animation == gif_url
    assert anim.reply_to_message_id == 42  # matches FakeMessage.message_id
    assert chat.sent_messages == []  # no download error
    # Registry is called to check/collect candidates, but fetch/renderer never run
    assert events == ["registry"]
    # The file_id should have been cached
    assert app._judgmental_file_ids.get(gif_url) == f"file_id:{gif_url}"


def test_judgmental_gif_uses_cached_file_id_on_subsequent_send(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    url = "https://www.instagram.com/reel/ABC123"
    candidate = make_candidate(url, "ABC123")
    gif_url = "https://example.com/judgmental.gif"
    events: list[str] = []
    registry = FakeRegistry([candidate], events)
    fetch_service = FakeFetchService({}, events)
    renderer = FakeRenderer(events)

    monkeypatch.setattr(app_module, "ApplicationBuilder", FakeApplicationBuilder)
    app = app_module.IgReelDownloaderApp(
        "telegram-token",
        registry,
        fetch_service,
        renderer,
        judgmental_chance=0.5,
        judgmental_gifs=[gif_url],
    )

    # Pre-populate the cache with a file_id
    cached_file_id = "cached_file_id_123"
    app._judgmental_file_ids[gif_url] = cached_file_id

    chat = FakeChat()
    update = FakeUpdate(url, chat)

    with (
        patch.object(app_module.judgmental_module, "should_fire", return_value=True),
        patch.object(app_module.judgmental_module, "pick_gif", return_value=gif_url),
    ):
        asyncio.run(app._message_handler(update, object()))

    # Should have sent via file_id (not URL)
    assert len(chat.sent_animations) == 1
    anim = chat.sent_animations[0]
    assert anim.animation == cached_file_id
    assert anim.reply_to_message_id == 42
    assert events == ["registry"]
