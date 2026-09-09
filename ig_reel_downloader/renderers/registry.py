from __future__ import annotations

from ig_reel_downloader.repository.models import MediaItem

from .base import BaseMediaRenderer, MediaRenderer
from .models import RenderConstraints, RenderedItem
from .providers import (
    InstagramMediaRenderer,
    RedditMediaRenderer,
    TikTokMediaRenderer,
    XMediaRenderer,
    YouTubeMediaRenderer,
)


class UnsupportedMediaError(ValueError):
    pass


class RendererRegistry:
    def __init__(self, default: MediaRenderer | None = None) -> None:
        self._default = default or BaseMediaRenderer()
        self._renderers: dict[tuple[str, str], MediaRenderer] = {}

    def register(self, provider: str, media_kind: str, renderer: MediaRenderer) -> None:
        key = (provider, media_kind)
        if key in self._renderers:
            raise ValueError(f"renderer already registered for {provider}:{media_kind}")
        self._renderers[key] = renderer

    def get(self, provider: str, media_kind: str) -> MediaRenderer:
        return self._renderers.get((provider, media_kind), self._default)

    def render(self, media: MediaItem, constraints: RenderConstraints) -> RenderedItem:
        if not media.assets and media.metadata.get("text_only") is not True:
            raise UnsupportedMediaError(
                "media has neither attachments nor text content"
            )
        return self.get(media.provider, media.media_kind).render(media, constraints)


def default_renderer_registry() -> RendererRegistry:
    registry = RendererRegistry()
    instagram = InstagramMediaRenderer()
    registry.register("instagram", "reel", instagram)
    registry.register("instagram", "post", instagram)
    registry.register("tiktok", "video", TikTokMediaRenderer())
    registry.register("reddit", "post", RedditMediaRenderer())
    registry.register("x", "post", XMediaRenderer())
    youtube = YouTubeMediaRenderer()
    registry.register("youtube", "short", youtube)
    registry.register("youtube", "video", youtube)
    return registry
