from __future__ import annotations

from ig_reel_downloader.repository.models import MediaItem

from .base import BaseMediaRenderer, _metrics, optional_count


def _attribution(
    media: MediaItem,
    *,
    at_handle: bool = False,
    prefer_uploader: bool = False,
) -> list[str]:
    channel = media.metadata.get("channel")
    uploader = media.metadata.get("uploader")
    value = (uploader or channel) if prefer_uploader else (channel or uploader)
    if not isinstance(value, str) or not value.strip():
        return []
    value = value.strip()
    if at_handle and not value.startswith("@"):
        value = f"@{value}"
    return [value]


class InstagramMediaRenderer(BaseMediaRenderer):
    def segments(self, media: MediaItem, *, has_video: bool) -> list[str]:
        definitions = (("like_count", "❤️"), ("comment_count", "💬"))
        metrics = _metrics(media, definitions)
        if has_video and (views := optional_count(media, "view_count")) is not None:
            metrics.insert(0, f"👁 {views:,}")
        return [*_attribution(media, at_handle=True), *metrics]


class TikTokMediaRenderer(BaseMediaRenderer):
    def segments(self, media: MediaItem, *, has_video: bool) -> list[str]:
        del has_video
        return [
            *_attribution(media, at_handle=True, prefer_uploader=True),
            *_metrics(
                media,
                (
                    ("view_count", "👁"),
                    ("like_count", "❤️"),
                    ("comment_count", "💬"),
                    ("repost_count", "↗️"),
                ),
            ),
        ]


class RedditMediaRenderer(BaseMediaRenderer):
    def segments(self, media: MediaItem, *, has_video: bool) -> list[str]:
        del has_video
        segments: list[str] = []
        author = media.metadata.get("author")
        subreddit = media.metadata.get("subreddit")
        if (
            isinstance(author, str)
            and author
            and isinstance(subreddit, str)
            and subreddit
        ):
            segments.append(f"u/{author} in r/{subreddit}")
        elif isinstance(author, str) and author:
            segments.append(f"u/{author}")
        elif isinstance(subreddit, str) and subreddit:
            segments.append(f"r/{subreddit}")
        upvotes = optional_count(media, "upvote_count")
        if upvotes is None:  # Compatibility with cached rows from before normalization.
            upvotes = optional_count(media, "like_count")
        if upvotes is not None:
            segments.append(f"⬆️ {upvotes:,}")
        segments.extend(_metrics(media, (("comment_count", "💬"),)))
        if media.metadata.get("over_18") is True:
            segments.append("🔞 NSFW")
        return segments


class XMediaRenderer(BaseMediaRenderer):
    def title(self, media: MediaItem) -> str:
        title = super().title(media)
        if not media.description:
            return title

        repeated_text = f" - {media.description[:40]}"
        if repeated_text in title:
            return title.partition(repeated_text)[0].rstrip()
        return title

    def segments(self, media: MediaItem, *, has_video: bool) -> list[str]:
        del has_video
        return _metrics(
            media,
            (
                ("view_count", "👁"),
                ("like_count", "❤️"),
                ("repost_count", "🔁"),
                ("comment_count", "💬"),
            ),
        )


class YouTubeMediaRenderer(BaseMediaRenderer):
    def segments(self, media: MediaItem, *, has_video: bool) -> list[str]:
        del has_video
        segments = [
            *_attribution(media),
            *_metrics(
                media,
                (
                    ("view_count", "👁"),
                    ("like_count", "❤️"),
                    ("comment_count", "💬"),
                ),
            ),
        ]
        duration = media.metadata.get("duration")
        if (
            isinstance(duration, int | float)
            and not isinstance(duration, bool)
            and duration >= 0
        ):
            total_seconds = round(duration)
            minutes, seconds = divmod(total_seconds, 60)
            hours, minutes = divmod(minutes, 60)
            formatted = (
                f"{hours}:{minutes:02d}:{seconds:02d}"
                if hours
                else f"{minutes}:{seconds:02d}"
            )
            segments.append(f"⏱ {formatted}")
        return segments
