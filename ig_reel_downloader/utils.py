import logging

from yt_dlp.utils import DownloadError

logger = logging.getLogger(__name__)


def is_auth_required_download_error(error: Exception) -> bool:
    message = str(error)
    return (
        isinstance(error, DownloadError)
        and "Instagram sent an empty media response" in message
        and "--cookies" in message
    )


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
