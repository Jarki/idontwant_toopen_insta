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
