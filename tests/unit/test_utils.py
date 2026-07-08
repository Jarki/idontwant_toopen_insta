from yt_dlp.utils import DownloadError

from ig_reel_downloader import utils


def test_is_auth_required_download_error() -> None:
    error = DownloadError(
        "ERROR: [Instagram] DOricBOkqP6: Instagram sent an empty media response. "
        "Check if this post is accessible in your browser without being logged-in. "
        "If it is not, then use --cookies-from-browser or --cookies for the authentication."
    )

    assert utils.is_auth_required_download_error(error)


def test_is_auth_required_download_error_rejects_other_download_errors() -> None:
    assert not utils.is_auth_required_download_error(
        DownloadError("ERROR: unavailable")
    )
