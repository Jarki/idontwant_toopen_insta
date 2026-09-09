import asyncio
import collections
import datetime
import logging
from contextlib import suppress

from telegram import Update
from telegram.error import BadRequest, TimedOut
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from . import judgmental as judgmental_module
from .downloaders import DownloaderRegistry, DownloadFailureReason, UrlCandidate
from .media_fetch import MediaFetchResult, MediaFetchService
from .renderers import RendererRegistry, UnsupportedMediaError
from .repository import models
from .telegram_sender import (
    TELEGRAM_RENDER_CONSTRAINTS,
    MediaRenderResult,
    MediaRenderTimedOut,
    TelegramMediaSender,
)

logger = logging.getLogger(__name__)

DEFAULT_TELEGRAM_MEDIA_WRITE_TIMEOUT = 120.0


def _duration_summary(media: models.MediaItem) -> str:
    durations = [
        f"{a.duration_seconds or '?'}s" for a in media.assets if a.asset_type == "video"
    ]
    return ", ".join(durations) if durations else "no video"


def _user_display_name(
    first_name: str,
    last_name: str | None,
    username: str | None,
) -> str:
    name = " ".join(part for part in (first_name, last_name) if part)
    return f"{name} (@{username})" if username else name


def _media_type_name(provider: str, media_kind: str) -> str:
    provider_names = {
        "instagram": "Instagram",
        "reddit": "Reddit",
        "tiktok": "TikTok",
        "x": "X",
        "youtube": "YouTube",
    }
    return f"{provider_names.get(provider, provider.title())} {media_kind}"


DEFAULT_TELEGRAM_READ_TIMEOUT = 30.0


class IgReelDownloaderApp:
    def __init__(
        self,
        bot_token: str,
        registry: DownloaderRegistry,
        fetch_service: MediaFetchService,
        renderer_registry: RendererRegistry,
        sender: TelegramMediaSender,
        telegram_media_write_timeout: float = DEFAULT_TELEGRAM_MEDIA_WRITE_TIMEOUT,
        telegram_read_timeout: float = DEFAULT_TELEGRAM_READ_TIMEOUT,
        judgmental_chance: float = 0.0,
        judgmental_gifs: collections.abc.Sequence[str] | None = None,
    ) -> None:
        self.telegram_media_write_timeout = telegram_media_write_timeout
        self.telegram_read_timeout = telegram_read_timeout
        self.registry = registry
        self.fetch_service = fetch_service
        self.renderer_registry = renderer_registry
        self.sender = sender
        self.judgmental_chance = judgmental_chance
        self.judgmental_gifs = list(judgmental_gifs) if judgmental_gifs else []
        # In-memory cache of Telegram file_ids for judgmental animations.
        # After the first successful URL-based send, we store the returned
        # file_id so subsequent sends use it directly (zero traffic, instant).
        self._judgmental_file_ids: dict[str, str] = {}
        self.app: Application = (
            ApplicationBuilder()
            .token(bot_token)
            .media_write_timeout(telegram_media_write_timeout)
            .read_timeout(telegram_read_timeout)
            .build()
        )
        self.app.add_handler(
            MessageHandler(
                filters.Regex(r"^/add-judgmental(?:@\w+)?(?:\s|$)"),
                self._add_judgmental_handler,
            )
        )
        self.app.add_handler(CommandHandler("stats", self._stats_handler))
        self.app.add_handler(CommandHandler("top", self._top_handler))
        self.app.add_handler(MessageHandler(filters.TEXT, self._message_handler))

    def _format_download_error(
        self,
        reel_url: str,
        failure_reason: DownloadFailureReason | None,
    ) -> str:
        if failure_reason == "auth":
            return f"Could not download (auth expired): {reel_url}"
        if failure_reason == "blocked":
            return (
                f"Download was temporarily blocked; please try again later: {reel_url}"
            )
        return f"Could not download {reel_url}"

    async def _get_media_items(
        self,
        candidates: list[UrlCandidate],
        media_request_ids: list[int],
    ) -> list[MediaFetchResult]:
        tasks = [
            asyncio.to_thread(self.fetch_service.fetch, candidate, request_id)
            for candidate, request_id in zip(
                candidates,
                media_request_ids,
                strict=True,
            )
        ]
        return list(await asyncio.gather(*tasks))

    async def _store_telegram_file_ids(
        self,
        render_results: list[MediaRenderResult],
    ) -> None:
        for render_result in render_results:
            for asset_index, file_id in render_result.telegram_file_ids.items():
                try:
                    await asyncio.to_thread(
                        self.fetch_service.repository.update_media_asset_telegram_file_id,
                        render_result.media.id,
                        asset_index,
                        file_id,
                    )
                except Exception:
                    logger.exception(
                        "Failed to store Telegram file_id for %s asset %d",
                        render_result.media.id,
                        asset_index,
                    )

    async def _record_deliveries(
        self,
        render_results: list[MediaRenderResult],
        request_ids_by_media_object: dict[int, collections.deque[int]],
    ) -> None:
        await self._store_telegram_file_ids(render_results)
        delivered_request_ids = [
            request_ids_by_media_object[id(result.media)].popleft()
            for result in render_results
            if result.sent
        ]
        await asyncio.to_thread(
            self.fetch_service.repository.mark_media_requests_delivered,
            delivered_request_ids,
        )

    async def _record_media_requests(
        self,
        update: Update,
        candidates: list[UrlCandidate],
    ) -> list[int]:
        telegram_user = update.effective_user
        telegram_chat = update.effective_chat
        telegram_chat_id = telegram_chat.id if telegram_chat is not None else None
        now = datetime.datetime.now()
        telegram_user_id: int | None = None
        if telegram_user is not None:
            user = models.TelegramUser(
                id=telegram_user.id,
                username=telegram_user.username,
                first_name=telegram_user.first_name,
                last_name=telegram_user.last_name,
                language_code=telegram_user.language_code,
                is_bot=telegram_user.is_bot,
                created_at=now,
                updated_at=now,
            )
            telegram_user_id = user.id
            await asyncio.to_thread(
                self.fetch_service.repository.upsert_telegram_user,
                user,
            )

        requests = [
            models.MediaRequest(
                telegram_user_id=telegram_user_id,
                telegram_chat_id=telegram_chat_id,
                url=candidate.url,
                normalized_url=candidate.normalized_url,
                provider=candidate.provider,
                media_kind=candidate.link_type,
                provider_item_id=(
                    candidate.local_ref.provider_item_id
                    if candidate.local_ref is not None
                    else None
                ),
                created_at=now,
            )
            for candidate in candidates
        ]
        # MediaFetchService receives these request IDs below and records the
        # eventual success or failure.
        return await asyncio.to_thread(
            self.fetch_service.repository.insert_media_requests,
            requests,
        )

    async def _stats_handler(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        del context
        user = update.effective_user
        chat = update.effective_chat
        if user is None or chat is None:
            return

        stats = await asyncio.to_thread(
            self.fetch_service.repository.get_chat_user_stats,
            user.id,
            chat.id,
        )
        lines = [
            "📊 Stats for "
            + _user_display_name(user.first_name, user.last_name, user.username),
            f"🆔 {user.id}",
            "",
            "🎞️ Delivered by type",
        ]
        if stats.delivered_by_type:
            lines.extend(
                f"• {_media_type_name(item.provider, item.media_kind)}: {item.count}"
                for item in stats.delivered_by_type
            )
        else:
            lines.append("• None yet")
        lines.extend(
            [
                "",
                "📈 Request outcomes",
                f"📨 Requested: {stats.requested}",
                f"✅ Delivered: {stats.delivered}",
                f"⚠️ Failed delivery: {stats.delivery_failed}",
                f"❌ Failed download: {stats.download_failed}",
            ]
        )
        await chat.send_message("\n".join(lines))

    async def _top_handler(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        del context
        chat = update.effective_chat
        if chat is None:
            return

        leaderboard = await asyncio.to_thread(
            self.fetch_service.repository.get_chat_leaderboard,
            chat.id,
        )
        if not leaderboard:
            await chat.send_message("No media requests recorded in this chat yet.")
            return

        lines = ["🏆 Top media requesters in this chat"]
        medals = ("🥇", "🥈", "🥉")
        for rank, entry in enumerate(leaderboard, start=1):
            prefix = medals[rank - 1] if rank <= len(medals) else f"{rank}."
            name = _user_display_name(
                entry.first_name,
                entry.last_name,
                entry.username,
            )
            lines.append(f"{prefix} {name} — {entry.count} requests")
        await chat.send_message("\n".join(lines))

    async def _add_judgmental_handler(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        del context
        message = update.message
        chat = update.effective_chat
        if message is None or chat is None:
            return

        reply = message.reply_to_message
        animation = getattr(reply, "animation", None) if reply is not None else None
        file_id = getattr(animation, "file_id", None)
        if not isinstance(file_id, str) or not file_id:
            await chat.send_message(
                "Reply to a Telegram GIF/animation with /add-judgmental "
                "and I will remember it."
            )
            return

        file_unique_id = getattr(animation, "file_unique_id", None)
        if not isinstance(file_unique_id, str):
            file_unique_id = None
        await asyncio.to_thread(
            self.fetch_service.repository.add_judgmental_animation_file_id,
            file_id,
            file_unique_id,
        )
        await chat.send_message("Saved judgmental GIF.")

    async def _list_judgmental_file_ids(self) -> list[str]:
        try:
            return await asyncio.to_thread(
                self.fetch_service.repository.list_judgmental_animation_file_ids
            )
        except Exception:
            logger.exception("Failed to list judgmental animation file_ids")
            return []

    async def _send_judgmental_animation(
        self,
        update: Update,
        reply_to_message_id: int,
        file_ids: collections.abc.Sequence[str],
    ) -> bool:
        chat = update.effective_chat
        if chat is None:
            return False

        if file_ids:
            animation = judgmental_module.pick_gif(file_ids)
            try:
                await chat.send_animation(
                    animation=animation,
                    reply_to_message_id=reply_to_message_id,
                    write_timeout=self.telegram_media_write_timeout,
                    read_timeout=self.telegram_read_timeout,
                )
            except BadRequest as exc:
                await asyncio.to_thread(
                    self.fetch_service.repository.delete_judgmental_animation_file_id,
                    animation,
                )
                logger.warning(
                    "Failed to send stored judgmental GIF file_id %s: %s",
                    animation,
                    exc,
                )
                return False
            return True

        gif_url = judgmental_module.pick_gif(self.judgmental_gifs)
        try:
            # Fallback for deployments that have not seeded Telegram file_ids yet.
            # This path remains best-effort because Telegram must fetch the URL.
            file_id = self._judgmental_file_ids.get(gif_url)
            if file_id is not None:
                sent = await chat.send_animation(
                    animation=file_id,
                    reply_to_message_id=reply_to_message_id,
                    write_timeout=self.telegram_media_write_timeout,
                    read_timeout=self.telegram_read_timeout,
                )
            else:
                sent = await chat.send_animation(
                    animation=gif_url,
                    reply_to_message_id=reply_to_message_id,
                    write_timeout=self.telegram_media_write_timeout,
                    read_timeout=self.telegram_read_timeout,
                )
            if sent and sent.animation:
                self._judgmental_file_ids[gif_url] = sent.animation.file_id
                await asyncio.to_thread(
                    self.fetch_service.repository.add_judgmental_animation_file_id,
                    sent.animation.file_id,
                    getattr(sent.animation, "file_unique_id", None),
                )
        except BadRequest as exc:
            self._judgmental_file_ids.pop(gif_url, None)
            logger.warning("Failed to send judgmental GIF %s: %s", gif_url, exc)
            return False
        return True

    async def _message_handler(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        sender = update.effective_sender
        sender_id = sender.id if sender is not None else "unknown"
        logger.debug("Got message from %s", sender_id)

        message = update.message
        if message is None or not message.text:
            return

        candidates = self.registry.extract_candidates(message.text)
        if not candidates:
            return

        media_request_ids = await self._record_media_requests(update, candidates)

        # Judgmental GIF chance — replace the download response with a GIF.
        judgmental_file_ids = await self._list_judgmental_file_ids()
        judgmental_options = judgmental_file_ids or self.judgmental_gifs
        if judgmental_module.should_fire(self.judgmental_chance, judgmental_options):
            sent_judgmental_animation = await self._send_judgmental_animation(
                update,
                message.message_id,
                judgmental_file_ids,
            )
            if sent_judgmental_animation:
                # Only skip download if the GIF was sent successfully.
                return

        logger.info(
            "Received %d media request(s) from %s: %s",
            len(candidates),
            sender_id,
            [f"{c.provider}:{c.link_type}" for c in candidates],
        )

        fetch_results = await self._get_media_items(candidates, media_request_ids)
        errors: list[str] = []
        media_items: list[models.MediaItem] = []
        request_ids_by_media_object: dict[int, collections.deque[int]] = {}
        for request_id, result in zip(
            media_request_ids,
            fetch_results,
            strict=True,
        ):
            if result.skipped:
                continue
            if result.media is None:
                errors.append(
                    self._format_download_error(result.url, result.failure_reason)
                )
            else:
                media_items.append(result.media)
                request_ids_by_media_object.setdefault(
                    id(result.media),
                    collections.deque(),
                ).append(request_id)

        if not media_items and not errors:
            return

        if media_items:
            summaries = [
                f"{m.provider}:{m.media_kind} ({_duration_summary(m)})"
                for m in media_items
            ]
            logger.info(
                "Delivering %d media item(s) to user %s: %s",
                len(media_items),
                sender_id,
                summaries,
            )

        rendered_items = []
        for media in media_items:
            try:
                rendered_items.append(
                    self.renderer_registry.render(media, TELEGRAM_RENDER_CONSTRAINTS)
                )
            except UnsupportedMediaError:
                errors.append(
                    self._format_download_error(media.original_url, "unsupported")
                )

        try:
            render_results = await self.sender.send(update, rendered_items)
        except MediaRenderTimedOut as exc:
            await self._record_deliveries(
                [*exc.completed_results, *exc.partial_results],
                request_ids_by_media_object,
            )
            logger.exception(
                "Timed out after sending %d of %d media items for user %s",
                len(exc.completed_results),
                len(media_items),
                sender_id,
            )
            chat = update.effective_chat
            if chat is not None:
                with suppress(TimedOut):
                    await chat.send_message(
                        "Timed out while uploading video(s) to Telegram. "
                        "Some media may have been delivered."
                    )
        except TimedOut:
            logger.exception(
                "Timed out while sending %s videos for user %s. "
                "Increase TELEGRAM_MEDIA_WRITE_TIMEOUT if uploads are slow.",
                len(media_items),
                sender_id,
            )
            chat = update.effective_chat
            if chat is not None:
                with suppress(TimedOut):
                    await chat.send_message(
                        "Timed out while uploading video(s) to Telegram. "
                        "The file may be large or the network may be slow."
                    )
        else:
            await self._record_deliveries(
                render_results,
                request_ids_by_media_object,
            )
            for render_result in render_results:
                if not render_result.sent:
                    errors.append(
                        self._format_download_error(
                            render_result.media.original_url,
                            render_result.failure_reason,
                        )
                    )

        if errors:
            errors_text = "\n".join(errors)
            chat = update.effective_chat
            if chat is not None:
                await chat.send_message(errors_text)

    def run(self) -> None:
        self.app.run_polling()
