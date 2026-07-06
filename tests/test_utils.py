import pytest
from yt_dlp.utils import DownloadError

from ig_reel_downloader import utils


@pytest.mark.parametrize(
    "url, expected",
    [
        (
            "https://www.instagram.com/reel/DFvr8JTuscr",
            ["https://www.instagram.com/reel/DFvr8JTuscr"],
        ),
        ("No link here!", []),
        (
            "https://www.instagram.com/reel/DGcc6bHIKFY\n\nhttps://www.instagram.com/reel/DFvr8JTuscr",
            [
                "https://www.instagram.com/reel/DGcc6bHIKFY",
                "https://www.instagram.com/reel/DFvr8JTuscr",
            ],
        ),
        (
            "https://www.instagram.com/reel/DGcc6bHIKFY\n\nhttps://www.instagram.com/reel/DFvr8JTuscr And some random text",
            [
                "https://www.instagram.com/reel/DGcc6bHIKFY",
                "https://www.instagram.com/reel/DFvr8JTuscr",
            ],
        ),
        (
            "https://www.instagram.com/reel/DJcm-GGRTJq",
            ["https://www.instagram.com/reel/DJcm-GGRTJq"],
        ),
    ],
)
def test_get_urls_from_text(url, expected):
    assert utils.get_urls_from_text(url) == expected


@pytest.mark.parametrize(
    "url, expected",
    [
        ("https://www.instagram.com/reel/DFvr8JTuscr", "DFvr8JTuscr"),
        ("https://www.instagram.com/reel/DGcc6bHIKFY", "DGcc6bHIKFY"),
        ("https://www.instagram.com/reel/DJcm-GGRTJq/", "DJcm-GGRTJq"),
        ("https://www.instagram.com/reel/D", "D"),
        ("https://www.instagram.com/reel/D/", "D"),
        ("https://www.instagram.com/reel/D?", "D"),
        ("https://www.instagram.com/reel/D/123/456/", "D"),
        ("https://www.instagram.com/reel/D/123/456/789/", "D"),
    ],
)
def test_get_id_from_url(url, expected):
    assert utils.get_id_from_url(url) == expected


def test_is_auth_required_download_error():
    error = DownloadError(
        "ERROR: [Instagram] DOricBOkqP6: Instagram sent an empty media response. "
        "Check if this post is accessible in your browser without being logged-in. "
        "If it is not, then use --cookies-from-browser or --cookies for the authentication."
    )

    assert utils.is_auth_required_download_error(error)


def test_is_auth_required_download_error_rejects_other_download_errors():
    assert not utils.is_auth_required_download_error(
        DownloadError("ERROR: unavailable")
    )
