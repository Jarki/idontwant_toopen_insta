import pytest
from yt_dlp.utils import DownloadError

from ig_reel_downloader import utils


def test_is_auth_required_download_error() -> None:
    error = DownloadError(
        "ERROR: [Instagram] DOricBOkqP6: Instagram sent an empty media response. "
        "Check if this post is accessible in your browser without being logged-in. "
        "If it is not, then use --cookies-from-browser or --cookies for the authentication."
    )

    assert utils.is_auth_required_download_error(error)


@pytest.mark.parametrize(
    "message",
    [
        "Account authentication is required",
        "Quarantined subreddit; an account that has opted in is required",
        "Private subreddit; an account that has been approved is required",
    ],
)
def test_is_auth_required_download_error_recognizes_reddit(message: str) -> None:
    assert utils.is_auth_required_download_error(DownloadError(message))


def test_is_auth_required_download_error_rejects_other_download_errors() -> None:
    assert not utils.is_auth_required_download_error(
        DownloadError("ERROR: unavailable")
    )


@pytest.mark.parametrize(
    "message",
    [
        "[TikTok] 7668090902816017671: Unexpected response from webpage request",
        "[TikTok] 7680948066874232085: "
        "Unable to extract universal data for rehydration",
    ],
)
def test_is_bot_detection_download_error(message: str) -> None:
    assert utils.is_bot_detection_download_error(DownloadError(message))


def test_is_bot_detection_download_error_rejects_unrelated_error() -> None:
    assert not utils.is_bot_detection_download_error(
        DownloadError("ERROR: unavailable")
    )
