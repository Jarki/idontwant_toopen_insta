from __future__ import annotations

import re

CANONICAL_POST_URL_PATTERN = re.compile(
    r"(?P<url>https://www\.tiktok\.com/@(?P<username>[a-zA-Z0-9._-]+)"
    r"/(?P<kind>video|photo)/(?P<id>\d+)(?:/)?(?:\?[^\s#]*)?(?:#[^\s]*)?)(?=\s|$)"
)
SHARE_URL_PATTERN = re.compile(
    r"(?P<url>https://(?P<host>v[mt]\.tiktok\.com)/(?P<id>[a-zA-Z0-9_-]+)"
    r"(?:/)?(?:\?[^\s#]*)?(?:#[^\s]*)?)(?=\s|$)"
)


def normalize_tiktok_post_url(username: str, kind: str, post_id: str) -> str:
    return f"https://www.tiktok.com/@{username}/{kind}/{post_id}"


def normalize_tiktok_share_url(host: str, share_id: str) -> str:
    return f"https://{host}/{share_id}/"
