import asyncio
import datetime
from typing import Any

import pytest

from ig_reel_downloader import app as app_module
from ig_reel_downloader.downloaders import ProviderItemRef, ResolvedUrlMatch
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
    def token(self, token: str) -> "FakeApplicationBuilder":
        return self

    def media_write_timeout(self, value: float) -> "FakeApplicationBuilder":
        return self

    def read_timeout(self, value: float) -> "FakeApplicationBuilder":
        return self

    def build(self) -> FakeApplication:
        return FakeApplication()


class FakeSender:
    id = 123


class FakeMessage:
    def __init__(self, text: str | None) -> None:
        self.text = text


class FakeChat:
    def __init__(self) -> None:
        self.sent_messages: list[str] = []

    async def send_message(self, text: str) -> None:
        self.sent_messages.append(text)


class FakeUpdate:
    def __init__(self, text: str | None, chat: FakeChat | None = None) -> None:
        self.message = FakeMessage(text) if text is not None else None
        self.effective_chat = chat
        self.effective_sender = FakeSender()


class FakeRegistry:
    def __init__(
        self,
        matches: list[ResolvedUrlMatch],
        events: list[str],
    ) -> None:
        self.matches = matches
        self.events = events
        self.texts: list[str] = []

    def extract_matches(self, text: str) -> list[ResolvedUrlMatch]:
        self.events.append("registry")
        self.texts.append(text)
        return self.matches


class FakeFetchService:
    def __init__(
        self,
        results: dict[str, MediaFetchResult],
        events: list[str],
    ) -> None:
        self.results = results
        self.events = events
        self.matches: list[ResolvedUrlMatch] = []

    def fetch(self, match: ResolvedUrlMatch) -> MediaFetchResult:
        self.events.append(f"fetch:{match.url}")
        self.matches.append(match)
        return self.results[match.url]


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

    def extract_urls(self, text: str) -> list[Any]:
        return []

    def get_provider_item_ref(self, url: str) -> ProviderItemRef | None:
        return ProviderItemRef("instagram", "reel", "ABC123")


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


def make_match(url: str, provider_item_id: str) -> ResolvedUrlMatch:
    return ResolvedUrlMatch(
        url=url,
        start=0,
        end=len(url),
        downloader=FakeDownloader(),
        provider_item_ref=ProviderItemRef("instagram", "reel", provider_item_id),
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
    first_match = make_match(first_url, "ABC123")
    second_match = make_match(second_url, "DEF456")
    first_media = make_media(first_url, "ABC123")
    events: list[str] = []
    registry = FakeRegistry([first_match, second_match], events)
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
    assert fetch_service.matches == [first_match, second_match]
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
    match = make_match(url, "ABC123")
    events: list[str] = []
    registry = FakeRegistry([match], events)
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
