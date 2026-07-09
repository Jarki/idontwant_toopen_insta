# Bug Report: Long captions break Telegram `send_video` / `send_photo`

**Date:** 2026-07-09
**Traceback source:** Production runtime on Pi

## Symptom

When a user sends a link whose media item has a long title or description (e.g. a YouTube/TikTok video with a multi-sentence description, or an Instagram carousel post with a long caption), the bot crashes with:

```
telegram.error.BadRequest: Message caption is too long
```

The error is not caught by the existing `TimedOut` handling in the app layer, so it propagates as an unhandled exception and the bot stops processing that update.

## Root Cause

`TelegramMediaRenderer._format_caption()` in `ig_reel_downloader/telegram_renderer.py` builds a caption as:

```
{title} • ❤️ {like_count}\n\n{description}
```

with **no length limit**. Telegram's API enforces a **1024-character maximum** for message captions on `sendVideo`, `sendPhoto`, and `sendMediaGroup`. The function naively concatenates whatever the downloader returned, so a long description from any provider (TikTok, YouTube, Instagram) will exceed the limit.

## Affected code paths

- `chat.send_video(…, caption=_format_caption(item))` — single-video case
- `chat.send_photo(…, caption=_format_caption(item))` — single-image case
- `chat.send_media_group(…, caption=_format_caption(item))` — first item in a media group

## Reproduction

Any media item whose `title + likes + description` exceeds 1024 characters will trigger the error. For example, a YouTube Short with a 50-character title and a 1200-character video description.

## Fix approach

Truncate the caption to ≤ 1024 characters while preserving as much useful content as possible:

1. Prioritize: **title** > **likes** (always kept, very short) > **description**
2. If the total exceeds 1024, truncate the **description** first, appending `…`.
3. If even without any description the title alone pushes over 1024, truncate the **title** with `…` as well.
4. Always keep the likes segment intact — it's always < ~15 chars.

The Telegram documentation confirms the 1024-byte caption limit as of Bot API 7.11+.
