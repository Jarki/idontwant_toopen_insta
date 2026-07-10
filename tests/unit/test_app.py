from __future__ import annotations

import asyncio
import datetime
from dataclasses import dataclass
from unittest.mock import AsyncMock, MagicMock, patch

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


class FakeMessage:
    def __init__(self, text: str | None) -> None:
        self.text = text
        self.message_id = 42


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
    ) -> None:
        self.sent_animations.append(
            SentAnimation(
                animation=animation,
                reply_to_message_id=reply_to_message_id,
            )
        )


class FakeUpdate:
    def __init__(self, text: str | None, chat: FakeChat | None = None) -> None:
        self.message = FakeMessage(text) if text is not None else None
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


class FakeFetchService:
    def __init__(
        self,
        results: dict[str, MediaFetchResult],
        events: list[str],
    ) -> None:
        self.results = results
        self.events = events
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
    assert fetch_service.candidates == [first_candidate, second_candidate]
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


def test_message_handler_sends_judgmental_gif_when_chance_triggers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    url = "https://www.instagram.com/reel/ABC123"
    candidate = make_candidate(url, "ABC123")
    gif_url = "https://example.com/judgmental.gif"
    gif_bytes = b"fake-gif-content"
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

    # Mock the httpx download so we don't make real HTTP requests
    mock_resp = MagicMock()
    mock_resp.content = gif_bytes
    mock_resp.raise_for_status.return_value = None

    mock_client = MagicMock()
    mock_client.get = AsyncMock(return_value=mock_resp)

    mock_client_cm = MagicMock()
    mock_client_cm.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client_cm.__aexit__ = AsyncMock(return_value=None)

    with (
        patch.object(app_module.judgmental_module, "should_fire", return_value=True),
        patch.object(app_module.judgmental_module, "pick_gif", return_value=gif_url),
        patch.object(app_module.httpx, "AsyncClient", return_value=mock_client_cm),
    ):
        asyncio.run(app._message_handler(update, object()))

    # Should have sent the GIF as a reply, not downloaded
    assert len(chat.sent_animations) == 1
    anim = chat.sent_animations[0]
    # send_animation receives the downloaded bytes, not the URL
    assert anim.animation == gif_bytes
    assert anim.reply_to_message_id == 42  # matches FakeMessage.message_id
    assert chat.sent_messages == []  # no download error
    # Registry is called to check/collect candidates, but fetch/renderer never run
    assert events == ["registry"]
