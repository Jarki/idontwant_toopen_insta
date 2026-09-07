import logging

from yt_dlp.utils import DownloadError

logger = logging.getLogger(__name__)


def is_auth_required_download_error(error: Exception) -> bool:
    if not isinstance(error, DownloadError):
        return False
    message = str(error).lower()
    instagram_auth_error = (
        "instagram sent an empty media response" in message and "--cookies" in message
    )
    reddit_auth_error = any(
        marker in message
        for marker in (
            "account authentication is required",
            "an account that has opted in is required",
            "an account that has been approved is required",
        )
    )
    return instagram_auth_error or reddit_auth_error


def is_bot_detection_download_error(error: Exception) -> bool:
    if not isinstance(error, DownloadError):
        return False
    message = str(error).lower()
    return any(
        marker in message
        for marker in (
            "unexpected response from webpage request",
            "unable to extract universal data for rehydration",
            "ip address is blocked from accessing this post",
        )
    )
