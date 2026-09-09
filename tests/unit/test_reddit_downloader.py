import io
from pathlib import Path

import pytest
from yt_dlp.utils import DownloadError

from ig_reel_downloader.downloaders.base import (
    DownloadContext,
    ProviderItemRef,
    ResolutionError,
    ResolvedMediaRequest,
)
from ig_reel_downloader.downloaders.reddit import RedditDownloader


class YoutubeDLStub:
    def __init__(self, _options: dict[str, object]) -> None:
        pass

    def __enter__(self) -> "YoutubeDLStub":
        return self

    def __exit__(self, _exc_type: object, _exc: object, _tb: object) -> None:
        return None


class FakeResponse(io.BytesIO):
    def __init__(
        self,
        body: bytes = b"",
        *,
        url: str = "https://i.redd.it/image.jpg",
        content_type: str = "image/jpeg",
        content_length: int | None = None,
    ) -> None:
        super().__init__(body)
        self.url = url
        self.headers = {"Content-Type": content_type}
        if content_length is not None:
            self.headers["Content-Length"] = str(content_length)

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self.close()


@pytest.mark.parametrize(
    ("url", "post_id"),
    [
        (
            "https://www.reddit.com/r/samplecommunity/comments/text123/title/?utm_source=share",
            "text123",
        ),
        ("https://old.reddit.com/comments/abc123", "abc123"),
        ("https://m.reddit.com/user/alice/comments/def456/a_post/", "def456"),
    ],
)
def test_reddit_extracts_canonical_post_urls(url: str, post_id: str) -> None:
    downloader = RedditDownloader()

    candidates = downloader.extract_candidates(f"See {url} now")

    assert len(candidates) == 1
    assert candidates[0].link_type == "post"
    assert candidates[0].normalized_url == f"https://www.reddit.com/comments/{post_id}/"
    assert candidates[0].local_ref == ProviderItemRef("reddit", "post", post_id)


def test_reddit_extracts_share_url_without_local_identity() -> None:
    downloader = RedditDownloader()
    url = "https://www.reddit.com/r/examplecommunity/s/ShareToken123"

    candidates = downloader.extract_candidates(f"See {url}.")

    assert len(candidates) == 1
    assert candidates[0].url == url
    assert candidates[0].link_type == "share"
    assert candidates[0].local_ref is None


@pytest.mark.parametrize(
    "url",
    [
        "https://www.reddit.com/r/samplecommunity/",
        "https://www.reddit.com/r/samplecommunity/s/",
        "https://www.reddit.com/settings/",
        "https://reddit.example.com/r/samplecommunity/comments/abc/title/",
    ],
)
def test_reddit_rejects_unsupported_urls(url: str) -> None:
    assert RedditDownloader().extract_candidates(url) == []


def test_reddit_resolves_share_redirect_to_post_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeYoutubeDL(YoutubeDLStub):
        def __init__(self, options: dict[str, object]) -> None:
            assert options == {"quiet": True}

        def urlopen(self, request: object) -> FakeResponse:
            return FakeResponse(
                url=(
                    "https://www.reddit.com/r/examplecommunity/comments/post456/"
                    "title/?share_id=tracking"
                )
            )

    monkeypatch.setattr(
        "ig_reel_downloader.downloaders.reddit.yt_dlp.YoutubeDL", FakeYoutubeDL
    )
    downloader = RedditDownloader()
    candidate = downloader.extract_candidates(
        "https://www.reddit.com/r/examplecommunity/s/ShareToken123"
    )[0]

    result = downloader.resolve(candidate)

    assert result.request is not None
    assert result.request.provider_item_ref == ProviderItemRef(
        "reddit", "post", "post456"
    )
    assert result.request.normalized_url == "https://www.reddit.com/comments/post456/"
    assert result.request.url == candidate.url


@pytest.mark.parametrize(
    "redirect_url",
    [
        "https://www.reddit.com/r/samplecommunity/",
        "https://attacker.example/comments/post456/",
        "http://www.reddit.com/comments/post456/",
        "https://www.reddit.com:443/comments/post456/",
    ],
)
def test_reddit_share_resolution_rejects_invalid_redirect(
    monkeypatch: pytest.MonkeyPatch,
    redirect_url: str,
) -> None:
    class FakeYoutubeDL(YoutubeDLStub):
        def urlopen(self, request: object) -> FakeResponse:
            return FakeResponse(url=redirect_url)

    monkeypatch.setattr(
        "ig_reel_downloader.downloaders.reddit.yt_dlp.YoutubeDL", FakeYoutubeDL
    )
    downloader = RedditDownloader()
    candidate = downloader.extract_candidates(
        "https://www.reddit.com/r/samplecommunity/s/OtherShare456"
    )[0]

    with pytest.raises(ResolutionError) as exc_info:
        downloader.resolve(candidate)

    assert exc_info.value.failure_reason == "unsupported"


def test_reddit_downloads_direct_image(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    post_data = {
        "id": "post456",
        "title": "Image post",
        "selftext": "description",
        "ups": 181,
        "num_comments": 24,
        "upvote_ratio": 0.91,
        "author": "alice",
        "subreddit": "pics",
        "created_utc": 1_700_000_000,
        "over_18": False,
        "post_hint": "image",
        "url": "https://i.redd.it/image.jpeg",
        "preview": {
            "images": [
                {
                    "source": {
                        "url": "https://preview.redd.it/image",
                        "width": 1200,
                        "height": 675,
                    }
                }
            ]
        },
    }

    class FakeYoutubeDL(YoutubeDLStub):
        def __init__(self, options: dict[str, object]) -> None:
            assert options == {"quiet": True}

        def urlopen(self, request: object) -> FakeResponse:
            return FakeResponse(b"jpeg bytes")

    monkeypatch.setattr(
        "ig_reel_downloader.downloaders.reddit.yt_dlp.YoutubeDL", FakeYoutubeDL
    )
    downloader = RedditDownloader()
    monkeypatch.setattr(downloader, "_load_post_data", lambda _url, _post_id: post_data)
    request = _request(downloader, "post456")

    result = downloader.download(request, DownloadContext(output_dir=tmp_path))

    assert result.failure_reason is None
    assert result.media is not None
    assert result.media.id == "reddit:post:post456"
    assert result.media.title == "Image post"
    assert result.media.description == "description"
    assert result.media.metadata == {
        "author": "alice",
        "subreddit": "pics",
        "created_utc": 1_700_000_000,
        "upvote_count": 181,
        "comment_count": 24,
        "upvote_ratio": 0.91,
        "over_18": False,
    }
    assert result.media.assets[0].asset_type == "image"
    assert result.media.assets[0].width == 1200
    assert result.media.assets[0].height == 675
    assert Path(result.media.assets[0].filepath).read_bytes() == b"jpeg bytes"


def test_reddit_downloads_gallery_images_in_gallery_order(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    post_data = {
        "id": "gallery1",
        "title": "Gallery",
        "gallery_data": {"items": [{"media_id": "second"}, {"media_id": "first"}]},
        "media_metadata": {
            "first": {
                "e": "Image",
                "s": {"u": "https://i.redd.it/first.png", "x": 10, "y": 20},
            },
            "second": {
                "e": "Image",
                "s": {"u": "https://i.redd.it/second.jpg", "x": 30, "y": 40},
            },
        },
    }

    class FakeYoutubeDL(YoutubeDLStub):
        calls = 0

        def urlopen(self, request: object) -> FakeResponse:
            self.__class__.calls += 1
            return FakeResponse(f"image-{self.calls}".encode())

    monkeypatch.setattr(
        "ig_reel_downloader.downloaders.reddit.yt_dlp.YoutubeDL", FakeYoutubeDL
    )
    downloader = RedditDownloader()
    monkeypatch.setattr(downloader, "_load_post_data", lambda _url, _post_id: post_data)

    result = downloader.download(
        _request(downloader, "gallery1"), DownloadContext(output_dir=tmp_path)
    )

    assert result.media is not None
    assert [asset.asset_index for asset in result.media.assets] == [0, 1]
    assert [asset.width for asset in result.media.assets] == [30, 10]
    assert result.media.assets[0].filepath.endswith("gallery1-0.jpg")
    assert result.media.assets[1].filepath.endswith("gallery1-1.png")


def test_reddit_rejects_mixed_gallery_instead_of_caching_partial_media(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    post_data = {
        "id": "gallery1",
        "title": "Mixed gallery",
        "gallery_data": {"items": [{"media_id": "image"}, {"media_id": "animation"}]},
        "media_metadata": {
            "image": {
                "e": "Image",
                "s": {"u": "https://i.redd.it/image.jpg", "x": 10, "y": 20},
            },
            "animation": {
                "e": "AnimatedImage",
                "s": {"u": "https://i.redd.it/animation.gif"},
            },
        },
    }
    downloader = RedditDownloader()
    monkeypatch.setattr(downloader, "_load_post_data", lambda _url, _post_id: post_data)

    result = downloader.download(
        _request(downloader, "gallery1"), DownloadContext(output_dir=tmp_path)
    )

    assert result.media is None
    assert result.failure_reason == "unsupported"


@pytest.mark.parametrize(
    "image_url",
    [
        "https://internal.example/image.jpg",
        "http://i.redd.it/image.jpg",
        "https://i.redd.it:443/image.jpg",
        "https://user@i.redd.it/image.jpg",
    ],
)
def test_reddit_rejects_unsafe_image_url(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    image_url: str,
) -> None:
    post_data = {
        "id": "post456",
        "post_hint": "image",
        "url": image_url,
    }
    downloader = RedditDownloader()
    monkeypatch.setattr(downloader, "_load_post_data", lambda _url, _post_id: post_data)

    result = downloader.download(
        _request(downloader, "post456"), DownloadContext(output_dir=tmp_path)
    )

    assert result.media is None
    assert result.failure_reason == "unsupported"


@pytest.mark.parametrize("use_content_length", [True, False])
def test_reddit_rejects_oversized_image_and_removes_partial_file(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    use_content_length: bool,
) -> None:
    post_data = {
        "id": "post456",
        "post_hint": "image",
        "url": "https://i.redd.it/image.jpg",
    }

    class FakeYoutubeDL(YoutubeDLStub):
        def urlopen(self, request: object) -> FakeResponse:
            return FakeResponse(
                b"123456",
                content_length=6 if use_content_length else None,
            )

    monkeypatch.setattr(
        "ig_reel_downloader.downloaders.reddit.yt_dlp.YoutubeDL", FakeYoutubeDL
    )
    monkeypatch.setattr(
        "ig_reel_downloader.downloaders.reddit.MAX_REDDIT_IMAGE_BYTES", 5
    )
    downloader = RedditDownloader()
    monkeypatch.setattr(downloader, "_load_post_data", lambda _url, _post_id: post_data)

    result = downloader.download(
        _request(downloader, "post456"), DownloadContext(output_dir=tmp_path)
    )

    assert result.media is None
    assert result.failure_reason == "unsupported"
    output_dir = tmp_path / "reddit" / "post" / "post456"
    assert list(output_dir.iterdir()) == []


def test_reddit_rejects_redirected_image_outside_reddit_cdn(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    post_data = {
        "id": "post456",
        "post_hint": "image",
        "url": "https://i.redd.it/image.jpg",
    }

    class FakeYoutubeDL(YoutubeDLStub):
        def urlopen(self, request: object) -> FakeResponse:
            return FakeResponse(url="https://internal.example/image.jpg")

    monkeypatch.setattr(
        "ig_reel_downloader.downloaders.reddit.yt_dlp.YoutubeDL", FakeYoutubeDL
    )
    downloader = RedditDownloader()
    monkeypatch.setattr(downloader, "_load_post_data", lambda _url, _post_id: post_data)

    result = downloader.download(
        _request(downloader, "post456"), DownloadContext(output_dir=tmp_path)
    )

    assert result.media is None
    assert result.failure_reason == "unsupported"


def test_reddit_downloads_hosted_video(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    post_data = {
        "id": "video789",
        "title": "Video post",
        "post_hint": "hosted:video",
        "ups": 826,
    }

    class FakeYoutubeDL(YoutubeDLStub):
        def __init__(self, options: dict[str, object]) -> None:
            assert options == {
                "outtmpl": str(
                    tmp_path / "reddit" / "post" / "video789" / "%(id)s.%(ext)s"
                ),
                "format": "bestvideo+bestaudio/best",
                "quiet": True,
            }

        def extract_info(self, url: str, download: bool = False) -> dict[str, object]:
            assert url == "https://www.reddit.com/comments/video789/"
            return {"id": "video-id", "ext": "mp4", "duration": 83}

        def prepare_filename(self, info: dict[str, object]) -> str:
            return str(tmp_path / "video-id.mp4")

        def download(self, urls: list[str]) -> None:
            assert urls == ["https://www.reddit.com/comments/video789/"]

    monkeypatch.setattr(
        "ig_reel_downloader.downloaders.reddit.yt_dlp.YoutubeDL", FakeYoutubeDL
    )
    downloader = RedditDownloader()
    monkeypatch.setattr(downloader, "_load_post_data", lambda _url, _post_id: post_data)

    result = downloader.download(
        _request(downloader, "video789"), DownloadContext(output_dir=tmp_path)
    )

    assert result.media is not None
    assert result.media.assets[0].asset_type == "video"
    assert result.media.assets[0].duration_seconds == 83


def test_reddit_returns_text_post_without_downloading_an_image(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    downloader = RedditDownloader()
    monkeypatch.setattr(
        downloader,
        "_load_post_data",
        lambda _url, post_id: {
            "id": post_id,
            "title": "Text post",
            "selftext": "Post body",
            "is_self": True,
            "ups": 42,
        },
    )

    result = downloader.download(
        _request(downloader, "text123"), DownloadContext(output_dir=tmp_path)
    )

    assert result.failure_reason is None
    assert result.media is not None
    assert result.media.title == "Text post"
    assert result.media.description == "Post body"
    assert result.media.assets == []
    assert result.media.metadata["text_only"] is True
    assert not (tmp_path / "reddit").exists()


@pytest.mark.parametrize("reason", ["private", "quarantined"])
def test_reddit_classifies_restricted_metadata_as_auth(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    reason: str,
) -> None:
    class FakeYoutubeDL(YoutubeDLStub):
        def urlopen(self, request: object) -> FakeResponse:
            request_url = str(request.full_url)
            body = (
                b'{"error": 403, "reason": "' + reason.encode() + b'"}'
                if request_url.endswith(".json")
                else b""
            )
            return FakeResponse(body)

    monkeypatch.setattr(
        "ig_reel_downloader.downloaders.reddit.yt_dlp.YoutubeDL", FakeYoutubeDL
    )
    downloader = RedditDownloader()

    result = downloader.download(
        _request(downloader, "private1"), DownloadContext(output_dir=tmp_path)
    )

    assert result.media is None
    assert result.failure_reason == "auth"


def test_reddit_classifies_provider_ip_block(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    downloader = RedditDownloader()

    def raise_blocked(_url: str, _post_id: str) -> dict[str, object]:
        raise DownloadError("IP address is blocked from accessing this post")

    monkeypatch.setattr(downloader, "_load_post_data", raise_blocked)

    result = downloader.download(
        _request(downloader, "blocked1"), DownloadContext(output_dir=tmp_path)
    )

    assert result.media is None
    assert result.failure_reason == "blocked"


def _request(downloader: RedditDownloader, post_id: str) -> ResolvedMediaRequest:
    url = f"https://www.reddit.com/comments/{post_id}/"
    return ResolvedMediaRequest(
        url=url,
        downloader=downloader,
        provider_item_ref=ProviderItemRef("reddit", "post", post_id),
        normalized_url=url,
    )
