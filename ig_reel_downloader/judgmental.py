"""Judgmental GIF response feature.

When the env-var chance triggers, the bot replies with a random judgmental
GIF instead of processing the reel.

The GIF list is a constant in this module so it is trivially customizable:
edit the list below to add or replace GIF URLs with your favourites.

GIF URLs must be direct links to a .gif file that Telegram can fetch and
render inline via ``chat.send_animation()``.  To find suitable GIFs:

1. Go to tenor.com or giphy.com.
2. Find a judgmental/reaction GIF you like.
3. Right-click the GIF → "Copy image address" (it should end in **.gif**).
4. Paste the URL into the list below.
"""

import random
from collections.abc import Sequence

# ---------------------------------------------------------------------------
# EDIT THIS LIST — add your favourite judgmental GIF URLs here.
# Each entry must be a direct link to a .gif file.
# ---------------------------------------------------------------------------
JUDGMENTAL_GIFS: Sequence[str] = [
    # Dog side-eye (contributed by user)
    "https://tenor.com/bWuiJ.gif",
]


def should_fire(chance: float, gifs: Sequence[str]) -> bool:
    """Return ``True`` when a judgmental GIF should be sent.

    Always returns ``False`` when the chance is 0 (or negative) or when
    the GIF list is empty.  Values above 1.0 are clamped to 1.0.
    """
    if chance <= 0.0:
        return False
    if not gifs:
        return False
    clamped = min(chance, 1.0)
    return random.random() < clamped


def pick_gif(gifs: Sequence[str]) -> str:
    """Return a randomly chosen GIF URL from the given list."""
    return random.choice(gifs)
