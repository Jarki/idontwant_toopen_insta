from __future__ import annotations

import asyncio
import collections
import datetime
import logging
from dataclasses import dataclass
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from ig_reel_downloader import app as app_module
from ig_reel_downloader.downloaders import (
    DownloadContext,
    DownloadFailureReason,
    MediaDownloadResult,
    ProviderItemRef,
    ResolvedMediaRequest,
    ResolveResult,
    UrlCandidate,
)
from ig_reel_downloader.media_fetch import MediaFetchResult
from ig_reel_downloader.renderers import (
    RenderConstraints,
    RenderedItem,
    UnsupportedMediaError,
)
from ig_reel_downloader.repository.models import (
    ChatLeaderboardEntry,
    ChatUserStats,
    MediaAsset,
    MediaItem,
    MediaRequest,
    MediaTypeCount,
    TelegramUser,
)
from ig_reel_downloader.telegram_sender import MediaRenderResult, MediaRenderTimedOut


class FakeApplication:
    def __init__(self) -> None:
        self.handlers: list[object] = []
        self.error_handlers: list[object] = []

    def add_handler(self, handler: object) -> None:
        self.handlers.append(handler)

    def add_error_handler(self, handler: object) -> None:
        self.error_handlers.append(handler)

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


class FakeTelegramUser:
    id = 123
    username = "alice"
    first_name = "Alice"
    last_name = "Example"
    language_code = "en"
    is_bot = False


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
    def __init__(self, chat_id: int = -100123) -> None:
        self.id = chat_id
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
        userless: bool = False,
    ) -> None:
        self.message = (
            FakeMessage(text, reply_to_message=reply_to_message)
            if text is not None
            else None
        )
        self.effective_chat = chat
        self.effective_user = None if userless else FakeTelegramUser()
        self.effective_sender = self.effective_user


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
        self.upserted_users: list[TelegramUser] = []
        self.inserted_requests: list[MediaRequest] = []
        self.updated_media_file_ids: list[tuple[str, int, str]] = []
        self.user_stats = ChatUserStats(
            requested=0,
            delivered=0,
            delivery_failed=0,
            download_failed=0,
        )
        self.chat_leaderboard: list[ChatLeaderboardEntry] = []
        self.requested_stats_keys: list[tuple[int, int]] = []
        self.requested_leaderboard_chat_ids: list[int] = []
        self.delivered_request_ids: list[int] = []

    def update_media_asset_telegram_file_id(
        self,
        media_item_id: str,
        asset_index: int,
        telegram_file_id: str,
    ) -> None:
        self.updated_media_file_ids.append(
            (media_item_id, asset_index, telegram_file_id)
        )

    def upsert_telegram_user(self, user: TelegramUser) -> None:
        self.upserted_users.append(user)

    def insert_media_requests(self, requests: list[MediaRequest]) -> list[int]:
        self.inserted_requests.extend(requests)
        return list(range(100, 100 + len(requests)))

    def get_chat_user_stats(
        self,
        telegram_user_id: int,
        telegram_chat_id: int,
    ) -> ChatUserStats:
        self.requested_stats_keys.append((telegram_user_id, telegram_chat_id))
        return self.user_stats

    def mark_media_requests_delivered(self, media_request_ids: list[int]) -> None:
        self.delivered_request_ids.extend(media_request_ids)

    def get_chat_leaderboard(
        self,
        telegram_chat_id: int,
        limit: int = 10,
    ) -> list[ChatLeaderboardEntry]:
        del limit
        self.requested_leaderboard_chat_ids.append(telegram_chat_id)
        return self.chat_leaderboard

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
        self.media_request_ids: list[int] = []

    def fetch(
        self,
        candidate: UrlCandidate,
        media_request_id: int,
    ) -> MediaFetchResult:
        self.events.append(f"fetch:{candidate.url}")
        self.candidates.append(candidate)
        self.media_request_ids.append(media_request_id)
        return self.results[candidate.url]


class FakeRendererRegistry:
    def render(
        self,
        media: MediaItem,
        constraints: RenderConstraints,
    ) -> RenderedItem:
        del constraints
        return RenderedItem(
            source=media,
            text=media.title or media.original_url,
            attachments=tuple(media.assets),
        )


class FakeSender:
    def __init__(
        self,
        events: list[str],
        telegram_file_ids: dict[int, str] | None = None,
        *,
        sent: bool = True,
    ) -> None:
        self.events = events
        self.telegram_file_ids = telegram_file_ids or {}
        self.sent = sent
        self.updates: list[FakeUpdate] = []
        self.media_items: list[list[MediaItem]] = []

    async def send(
        self,
        update: FakeUpdate,
        rendered_items: list[RenderedItem],
    ) -> list[MediaRenderResult]:
        self.events.append("sender")
        self.updates.append(update)
        media_items = [item.source for item in rendered_items]
        self.media_items.append(media_items)
        return [
            MediaRenderResult(
                media=item,
                sent=self.sent,
                failure_reason=None if self.sent else "unknown",
                telegram_file_ids=self.telegram_file_ids,
            )
            for item in media_items
        ]


class PartiallyTimedOutSender(FakeSender):
    def __init__(
        self,
        events: list[str],
        completed_media: MediaItem,
    ) -> None:
        super().__init__(events)
        self.completed_media = completed_media

    async def send(
        self,
        update: FakeUpdate,
        rendered_items: list[RenderedItem],
    ) -> list[MediaRenderResult]:
        del update
        media_items = [item.source for item in rendered_items]
        partial_results = (
            [
                MediaRenderResult(
                    media=media_items[1],
                    sent=False,
                    telegram_file_ids={0: "partial-telegram-file-id"},
                )
            ]
            if len(media_items) > 1
            else []
        )
        raise MediaRenderTimedOut(
            [MediaRenderResult(media=self.completed_media, sent=True)],
            partial_results,
        )


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
    sender: FakeSender,
) -> app_module.IgReelDownloaderApp:
    monkeypatch.setattr(app_module, "ApplicationBuilder", FakeApplicationBuilder)
    return app_module.IgReelDownloaderApp(
        "telegram-token",
        registry,
        fetch_service,
        FakeRendererRegistry(),
        sender,
    )


def build_command_app(
    monkeypatch: pytest.MonkeyPatch,
    repository: FakeRepository,
) -> app_module.IgReelDownloaderApp:
    events: list[str] = []
    return build_app(
        monkeypatch,
        FakeRegistry([], events),
        FakeFetchService({}, events, repository=repository),
        FakeSender(events),
    )


def test_message_handler_uses_registry_fetch_service_renderer_and_sender(
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
    sender = FakeSender(events)
    app = build_app(monkeypatch, registry, fetch_service, sender)
    chat = FakeChat()
    update = FakeUpdate(f"reels: {first_url} {second_url}", chat)

    asyncio.run(app._message_handler(update, object()))

    assert registry.texts == [f"reels: {first_url} {second_url}"]
    # Both candidates fetched (order is non-deterministic with threads).
    assert len(fetch_service.candidates) == 2
    assert first_candidate in fetch_service.candidates
    assert second_candidate in fetch_service.candidates
    assert sender.updates == [update]
    assert sender.media_items == [[first_media]]
    assert len(fetch_service.repository.upserted_users) == 1
    user = fetch_service.repository.upserted_users[0]
    requests = fetch_service.repository.inserted_requests
    assert user.id == 123
    assert user.username == "alice"
    assert user.first_name == "Alice"
    assert user.last_name == "Example"
    assert user.language_code == "en"
    assert user.is_bot is False
    assert [request.url for request in requests] == [first_url, second_url]
    assert [request.telegram_chat_id for request in requests] == [-100123, -100123]
    assert [request.provider_item_id for request in requests] == ["ABC123", "DEF456"]
    assert set(fetch_service.media_request_ids) == {100, 101}
    assert fetch_service.repository.delivered_request_ids == [100]
    assert chat.sent_messages == []
    assert events[0] == "registry"
    assert set(events[1:-1]) == {f"fetch:{first_url}", f"fetch:{second_url}"}
    assert events[-1] == "sender"


def test_unexpected_render_failure_reports_bound_context_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    url = "https://www.instagram.com/reel/ABC123"
    media = make_media(url, "ABC123")
    events: list[str] = []
    app = build_app(
        monkeypatch,
        FakeRegistry([make_candidate(url, "ABC123")], events),
        FakeFetchService({url: MediaFetchResult(media=media, url=url)}, events),
        FakeSender(events),
    )

    class RaisingRenderer:
        def render(
            self, media: MediaItem, constraints: RenderConstraints
        ) -> RenderedItem:
            del media, constraints
            raise RuntimeError("unexpected render failure")

    app.renderer_registry = RaisingRenderer()  # type: ignore[assignment]
    reports: list[tuple[dict[str, object], object]] = []

    def capture_report(message: str, **kwargs: object) -> None:
        del message
        reports.append((kwargs, app_module.error_reporter.current_context()))

    monkeypatch.setattr(app_module.logger, "error", capture_report)
    update = FakeUpdate(url, FakeChat())
    with pytest.raises(RuntimeError) as raised:
        asyncio.run(app._message_handler(update, object()))  # type: ignore[arg-type]

    asyncio.run(
        app._unexpected_error_handler(
            update,
            SimpleNamespace(error=raised.value),  # type: ignore[arg-type]
        )
    )

    assert len(reports) == 1
    kwargs, context = reports[0]
    assert kwargs["extra"] == {
        "event_code": "telegram.render_unexpected",
        "redact_exception_message": True,
    }
    assert context.media_request_ids == (100,)
    assert context.provider == "instagram"
    assert context.media_kind == "reel"
    assert context.stage == "render"


def test_unexpected_upload_failure_reports_bound_context_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    url = "https://www.instagram.com/reel/ABC123"
    media = make_media(url, "ABC123")
    events: list[str] = []

    class RaisingSender(FakeSender):
        async def send(
            self,
            update: FakeUpdate,
            rendered_items: list[RenderedItem],
        ) -> list[MediaRenderResult]:
            del update, rendered_items
            raise RuntimeError("unexpected upload failure")

    app = build_app(
        monkeypatch,
        FakeRegistry([make_candidate(url, "ABC123")], events),
        FakeFetchService({url: MediaFetchResult(media=media, url=url)}, events),
        RaisingSender(events),
    )
    reports: list[tuple[dict[str, object], object]] = []

    def capture_report(message: str, **kwargs: object) -> None:
        del message
        reports.append((kwargs, app_module.error_reporter.current_context()))

    monkeypatch.setattr(app_module.logger, "error", capture_report)
    update = FakeUpdate(url, FakeChat())
    with pytest.raises(RuntimeError) as raised:
        asyncio.run(app._message_handler(update, object()))  # type: ignore[arg-type]

    asyncio.run(
        app._unexpected_error_handler(
            update,
            SimpleNamespace(error=raised.value),  # type: ignore[arg-type]
        )
    )

    assert len(reports) == 1
    kwargs, context = reports[0]
    assert kwargs["extra"] == {
        "event_code": "telegram.upload_unexpected",
        "redact_exception_message": True,
    }
    assert context.media_request_ids == (100,)
    assert context.stage == "telegram_upload"


def test_upload_error_links_only_requests_actually_passed_to_sender(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    skipped_url = "https://www.instagram.com/reel/SKIPPED"
    attempted_url = "https://www.instagram.com/reel/ATTEMPTED"
    skipped_media = make_media(skipped_url, "SKIPPED")
    attempted_media = make_media(attempted_url, "ATTEMPTED")
    events: list[str] = []

    class MixedRenderer:
        def render(
            self, media: MediaItem, constraints: RenderConstraints
        ) -> RenderedItem:
            if media is skipped_media:
                raise UnsupportedMediaError
            return FakeRendererRegistry().render(media, constraints)

    class RaisingSender(FakeSender):
        async def send(
            self,
            update: FakeUpdate,
            rendered_items: list[RenderedItem],
        ) -> list[MediaRenderResult]:
            assert [item.source for item in rendered_items] == [attempted_media]
            del update
            raise RuntimeError("unexpected upload failure")

    app = build_app(
        monkeypatch,
        FakeRegistry(
            [
                make_candidate(skipped_url, "SKIPPED"),
                make_candidate(attempted_url, "ATTEMPTED"),
            ],
            events,
        ),
        FakeFetchService(
            {
                skipped_url: MediaFetchResult(media=skipped_media, url=skipped_url),
                attempted_url: MediaFetchResult(
                    media=attempted_media, url=attempted_url
                ),
            },
            events,
        ),
        RaisingSender(events),
    )
    app.renderer_registry = MixedRenderer()  # type: ignore[assignment]
    snapshots = []

    def capture_report(message: str, **kwargs: object) -> None:
        record = logging.LogRecord(
            app_module.logger.name,
            logging.ERROR,
            __file__,
            1,
            message,
            (),
            kwargs.get("exc_info"),  # type: ignore[arg-type]
        )
        extra = kwargs.get("extra", {})
        assert isinstance(extra, dict)
        for name, value in extra.items():
            setattr(record, name, value)
        snapshots.append(app_module.error_reporter.snapshot_from_record(record))

    monkeypatch.setattr(app_module.logger, "error", capture_report)

    with pytest.raises(RuntimeError):
        asyncio.run(
            app._message_handler(
                FakeUpdate(f"{skipped_url} {attempted_url}", FakeChat()),
                object(),
            )
        )

    assert len(snapshots) == 1
    assert snapshots[0].media_request_ids == (101,)
    assert snapshots[0].component == "telegram_upload"


def test_file_id_persistence_failure_reports_request_ids(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    repository = FakeRepository()
    fetch_service = FakeFetchService({}, events, repository=repository)
    app = build_app(
        monkeypatch,
        FakeRegistry([], events),
        fetch_service,
        FakeSender(events),
    )
    media = make_media("https://www.instagram.com/reel/ABC123", "ABC123")
    result = MediaRenderResult(
        media=media,
        sent=True,
        telegram_file_ids={0: "telegram-video-id"},
    )
    request_ids = {id(media): collections.deque([321])}
    contexts = []

    def fail_update(*args: object) -> None:
        del args
        raise RuntimeError("persistence failed")

    def capture_exception(message: str, **kwargs: object) -> None:
        del message, kwargs
        contexts.append(app_module.error_reporter.current_context())

    monkeypatch.setattr(
        repository,
        "update_media_asset_telegram_file_id",
        fail_update,
    )
    monkeypatch.setattr(app_module.logger, "exception", capture_exception)

    asyncio.run(app._record_deliveries([result], request_ids))

    assert len(contexts) == 1
    assert contexts[0].media_request_ids == (321,)
    assert contexts[0].provider == "instagram"
    assert contexts[0].media_kind == "reel"
    assert contexts[0].stage == "persist_telegram_file_id"


def test_delivery_persistence_failure_reports_only_delivered_requests_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    repository = FakeRepository()
    app = build_app(
        monkeypatch,
        FakeRegistry([], events),
        FakeFetchService({}, events, repository=repository),
        FakeSender(events),
    )
    delivered = make_media("https://www.instagram.com/reel/DELIVERED", "DELIVERED")
    unsent = make_media("https://www.instagram.com/reel/UNSENT", "UNSENT")
    results = [
        MediaRenderResult(media=delivered, sent=True),
        MediaRenderResult(media=unsent, sent=False),
    ]
    request_ids = {
        id(delivered): collections.deque([401]),
        id(unsent): collections.deque([402]),
    }
    snapshots = []

    def fail_delivery(_request_ids: list[int]) -> None:
        raise RuntimeError("delivery persistence failed")

    def capture_report(message: str, **kwargs: object) -> None:
        record = logging.LogRecord(
            app_module.logger.name,
            logging.ERROR,
            __file__,
            1,
            message,
            (),
            kwargs.get("exc_info"),  # type: ignore[arg-type]
        )
        extra = kwargs.get("extra", {})
        assert isinstance(extra, dict)
        for name, value in extra.items():
            setattr(record, name, value)
        snapshots.append(app_module.error_reporter.snapshot_from_record(record))

    monkeypatch.setattr(
        repository,
        "mark_media_requests_delivered",
        fail_delivery,
    )
    monkeypatch.setattr(app_module.logger, "error", capture_report)

    with pytest.raises(RuntimeError) as raised:
        asyncio.run(app._record_deliveries(results, request_ids))
    asyncio.run(
        app._unexpected_error_handler(
            object(),
            SimpleNamespace(error=raised.value),  # type: ignore[arg-type]
        )
    )

    assert len(snapshots) == 1
    assert snapshots[0].event_code == "telegram.delivery_persist_failed"
    assert snapshots[0].media_request_ids == (401,)
    assert snapshots[0].component == "persist_delivery"
    assert list(request_ids[id(unsent)]) == [402]


def test_message_handler_persists_file_ids_returned_by_sender(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    url = "https://www.instagram.com/reel/ABC123"
    media = make_media(url, "ABC123")
    events: list[str] = []
    repository = FakeRepository()
    fetch_service = FakeFetchService(
        {url: MediaFetchResult(media=media, url=url)},
        events,
        repository=repository,
    )
    sender = FakeSender(events, telegram_file_ids={0: "telegram-video-id"})
    app = build_app(
        monkeypatch,
        FakeRegistry([make_candidate(url, "ABC123")], events),
        fetch_service,
        sender,
    )

    asyncio.run(app._message_handler(FakeUpdate(url, FakeChat()), object()))

    assert repository.updated_media_file_ids == [
        ("instagram:reel:ABC123", 0, "telegram-video-id")
    ]


def test_message_handler_does_not_mark_failed_send_as_delivered(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    url = "https://www.instagram.com/reel/ABC123"
    candidate = make_candidate(url, "ABC123")
    media = make_media(url, "ABC123")
    events: list[str] = []
    fetch_service = FakeFetchService(
        {url: MediaFetchResult(media=media, url=url)},
        events,
    )
    app = build_app(
        monkeypatch,
        FakeRegistry([candidate], events),
        fetch_service,
        FakeSender(events, sent=False),
    )
    chat = FakeChat()

    asyncio.run(app._message_handler(FakeUpdate(url, chat), object()))

    assert fetch_service.repository.delivered_request_ids == []
    assert chat.sent_messages == []


def test_message_handler_records_deliveries_completed_before_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first_url = "https://www.instagram.com/reel/ABC123"
    second_url = "https://www.instagram.com/reel/DEF456"
    first_media = make_media(first_url, "ABC123")
    second_media = make_media(second_url, "DEF456")
    events: list[str] = []
    fetch_service = FakeFetchService(
        {
            first_url: MediaFetchResult(media=first_media, url=first_url),
            second_url: MediaFetchResult(media=second_media, url=second_url),
        },
        events,
    )
    app = build_app(
        monkeypatch,
        FakeRegistry(
            [
                make_candidate(first_url, "ABC123"),
                make_candidate(second_url, "DEF456"),
            ],
            events,
        ),
        fetch_service,
        PartiallyTimedOutSender(events, first_media),
    )
    chat = FakeChat()

    asyncio.run(
        app._message_handler(FakeUpdate(f"{first_url} {second_url}", chat), object())
    )

    assert fetch_service.repository.delivered_request_ids == [100]
    assert fetch_service.repository.updated_media_file_ids == [
        (second_media.id, 0, "partial-telegram-file-id")
    ]
    assert chat.sent_messages == []


def test_message_handler_keeps_upload_timeout_silent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    url = "https://www.instagram.com/reel/ABC123"
    media = make_media(url, "ABC123")
    events: list[str] = []

    class TimedOutSender(FakeSender):
        async def send(
            self,
            update: FakeUpdate,
            rendered_items: list[RenderedItem],
        ) -> list[MediaRenderResult]:
            del update, rendered_items
            raise app_module.TimedOut("upload diagnostic")

    fetch_service = FakeFetchService(
        {url: MediaFetchResult(media=media, url=url)},
        events,
    )
    app = build_app(
        monkeypatch,
        FakeRegistry([make_candidate(url, "ABC123")], events),
        fetch_service,
        TimedOutSender(events),
    )
    chat = FakeChat()

    asyncio.run(app._message_handler(FakeUpdate(url, chat), object()))

    assert fetch_service.repository.delivered_request_ids == []
    assert chat.sent_messages == []


def test_stats_command_shows_request_outcome_breakdown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = FakeRepository()
    repository.user_stats = ChatUserStats(
        requested=6,
        delivered=3,
        delivery_failed=1,
        download_failed=2,
        delivered_by_type=[
            MediaTypeCount(provider="instagram", media_kind="reel", count=2),
            MediaTypeCount(provider="tiktok", media_kind="video", count=1),
        ],
    )
    app = build_command_app(monkeypatch, repository)
    chat = FakeChat()

    asyncio.run(app._stats_handler(FakeUpdate("/stats", chat), object()))

    assert repository.requested_stats_keys == [(123, -100123)]
    assert chat.sent_messages == [
        "📊 Stats for Alice Example (@alice)\n"
        "🆔 123\n"
        "\n"
        "🎞️ Delivered by type\n"
        "• Instagram reel: 2\n"
        "• TikTok video: 1\n"
        "\n"
        "📈 Request outcomes\n"
        "📨 Requested: 6\n"
        "✅ Delivered: 3\n"
        "⚠️ Failed delivery: 1\n"
        "❌ Failed download: 2"
    ]


def test_stats_command_handles_no_media(monkeypatch: pytest.MonkeyPatch) -> None:
    app = build_command_app(monkeypatch, FakeRepository())
    chat = FakeChat()

    asyncio.run(app._stats_handler(FakeUpdate("/stats", chat), object()))

    assert chat.sent_messages == [
        "📊 Stats for Alice Example (@alice)\n"
        "🆔 123\n"
        "\n"
        "🎞️ Delivered by type\n"
        "• None yet\n"
        "\n"
        "📈 Request outcomes\n"
        "📨 Requested: 0\n"
        "✅ Delivered: 0\n"
        "⚠️ Failed delivery: 0\n"
        "❌ Failed download: 0"
    ]


def test_top_command_shows_chat_leaderboard(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = FakeRepository()
    repository.chat_leaderboard = [
        ChatLeaderboardEntry(
            username="alice",
            first_name="Alice",
            last_name="Example",
            count=4,
        ),
        ChatLeaderboardEntry(
            username=None,
            first_name="Bob",
            last_name=None,
            count=2,
        ),
    ]
    app = build_command_app(monkeypatch, repository)
    chat = FakeChat(chat_id=-987)

    asyncio.run(app._top_handler(FakeUpdate("/top", chat), object()))

    assert repository.requested_leaderboard_chat_ids == [-987]
    assert chat.sent_messages == [
        "🏆 Top media requesters in this chat\n"
        "🥇 Alice Example (@alice) — 4 requests\n"
        "🥈 Bob — 2 requests"
    ]


def test_top_command_handles_empty_chat(monkeypatch: pytest.MonkeyPatch) -> None:
    app = build_command_app(monkeypatch, FakeRepository())
    chat = FakeChat()

    asyncio.run(app._top_handler(FakeUpdate("/top", chat), object()))

    assert chat.sent_messages == ["No media requests recorded in this chat yet."]


def test_message_handler_records_and_processes_userless_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    url = "https://www.instagram.com/reel/ABC123"
    candidate = make_candidate(url, "ABC123")
    media = make_media(url, "ABC123")
    events: list[str] = []
    registry = FakeRegistry([candidate], events)
    fetch_service = FakeFetchService(
        {url: MediaFetchResult(media=media, url=url)},
        events,
    )
    sender = FakeSender(events)
    app = build_app(monkeypatch, registry, fetch_service, sender)
    update = FakeUpdate(url, FakeChat(), userless=True)

    asyncio.run(app._message_handler(update, object()))

    assert fetch_service.repository.upserted_users == []
    assert len(fetch_service.repository.inserted_requests) == 1
    assert fetch_service.repository.inserted_requests[0].telegram_user_id is None
    assert fetch_service.media_request_ids == [100]
    assert sender.media_items == [[media]]


@pytest.mark.parametrize(
    "failure_reason", ["auth", "blocked", "unsupported", "unknown"]
)
def test_message_handler_keeps_download_failures_silent(
    monkeypatch: pytest.MonkeyPatch,
    failure_reason: DownloadFailureReason,
) -> None:
    url = (
        "https://www.instagram.com/reel/ABC123"
        "?error_reference=secret&traceback=diagnostic"
    )
    candidate = make_candidate(url, "ABC123")
    events: list[str] = []
    registry = FakeRegistry([candidate], events)
    fetch_service = FakeFetchService(
        {
            url: MediaFetchResult(
                media=None,
                url=url,
                failure_reason=failure_reason,
            )
        },
        events,
    )
    sender = FakeSender(events)
    app = build_app(monkeypatch, registry, fetch_service, sender)
    chat = FakeChat()

    asyncio.run(app._message_handler(FakeUpdate(url, chat), object()))

    assert fetch_service.media_request_ids == [100]
    assert sender.media_items == []
    assert chat.sent_messages == []


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
    sender = FakeSender(events)
    app = build_app(monkeypatch, registry, fetch_service, sender)
    chat = FakeChat()

    asyncio.run(app._message_handler(FakeUpdate(url, chat), object()))

    assert chat.sent_messages == []
    assert sender.media_items == []


def test_add_judgmental_command_stores_replied_animation_file_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    registry = FakeRegistry([], events)
    repository = FakeRepository()
    fetch_service = FakeFetchService({}, events, repository=repository)
    sender = FakeSender(events)
    app = build_app(monkeypatch, registry, fetch_service, sender)
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
    sender = FakeSender(events)
    app = build_app(monkeypatch, registry, fetch_service, sender)
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
    sender = FakeSender(events)

    monkeypatch.setattr(app_module, "ApplicationBuilder", FakeApplicationBuilder)
    app = app_module.IgReelDownloaderApp(
        "telegram-token",
        registry,
        fetch_service,
        FakeRendererRegistry(),
        sender,
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
    assert len(repository.upserted_users) == 1
    assert [request.url for request in repository.inserted_requests] == [url]
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
    sender = FakeSender(events)

    monkeypatch.setattr(app_module, "ApplicationBuilder", FakeApplicationBuilder)
    app = app_module.IgReelDownloaderApp(
        "telegram-token",
        registry,
        fetch_service,
        FakeRendererRegistry(),
        sender,
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
    # Registry is called to check/collect candidates, but fetch/sender never run
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
    sender = FakeSender(events)

    monkeypatch.setattr(app_module, "ApplicationBuilder", FakeApplicationBuilder)
    app = app_module.IgReelDownloaderApp(
        "telegram-token",
        registry,
        fetch_service,
        FakeRendererRegistry(),
        sender,
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
