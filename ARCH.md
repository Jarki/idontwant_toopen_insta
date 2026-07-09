# Architecture

_Last reviewed: 2026-07-08_

## Overview

`ig-reel-downloader` is a small Telegram bot that lets users send supported social media links and receive the downloaded media back in Telegram. The app is implemented as a Python package with a single runtime process:

- `python-telegram-bot` handles Telegram long-polling and message delivery.
- `yt-dlp` extracts metadata and downloads media files for Instagram, TikTok, and YouTube.
- SQLite caches generic media metadata and local file paths for recently downloaded items.
- Alembic manages SQLite schema creation and migrations.
- Docker Compose runs migrations in a one-shot container before starting the bot with persistent `data/`, `output/`, `assets/`, and `.env` mounts.

At a high level, the core runtime flow is:

```text
Telegram text
    │
    ▼
DownloaderRegistry
    ├── extracts provider URL candidates
    ├── resolves candidates with resolve()
    ├── resolves overlaps
    └── deduplicates by provider/media/item identity
    │
    ▼
IgReelDownloaderApp
    └── offloads each MediaFetchService.fetch(match) with asyncio.to_thread()
    │
    ▼
MediaFetchService
    ├── checks generic SQLite cache by provider/media/item identity
    ├── validates referenced asset files exist
    ├── calls the matched downloader on cache miss
    └── writes refreshed MediaItem/MediaAsset rows
    │
    ▼
TelegramMediaRenderer
    └── sends one-video Reels as a video or media group
```

## Repository layout

```text
.
├── ig_reel_downloader/
│   ├── __main__.py              # Runtime entry point and environment wiring
│   ├── app.py                   # Telegram bot application orchestration
│   ├── constants.py             # Shared constants, currently cache TTL
│   ├── media_fetch.py           # Cache lookup, file-existence validation, download refresh
│   ├── telegram_renderer.py     # Telegram video/media-group rendering
│   ├── utils.py                 # Download error classification helpers
│   ├── downloaders/
│   │   ├── base.py              # Downloader Protocol and shared download models
│   │   ├── instagram.py         # Instagram Reel and Post URL matching and yt-dlp downloader
│   │   ├── tiktok.py            # TikTok video downloader with share-link resolution
│   │   ├── youtube.py           # YouTube Shorts and video downloader with duration gate
│   │   ├── yt_dlp_support.py    # Shared yt-dlp options, asset mapping, error helpers
│   │   └── registry.py          # URL candidate extraction, overlap handling, deduplication
│   └── repository/
│       ├── base.py              # Repository Protocol
│       ├── models.py            # Pydantic domain models
│       └── sqlite.py            # SQLAlchemy/Alembic SQLite Repository implementation
├── migrations/                  # Alembic migration environment and revisions
├── tests/unit/                  # Unit tests for app seams and pure helpers
├── tests/integration/           # Integration tests, including repository/database tests
├── tests/e2e/                   # Future end-to-end tests
├── Dockerfile                   # uv-based container build
├── docker-compose.yaml          # Production-ish local deployment
├── docker_entrypoint.sh         # Starts cleanup loop and bot process
├── clean.sh                     # Output-file retention script
├── pyproject.toml               # Dependencies and Poe task definitions
├── ruff.toml                    # Formatting/linting rules
├── mypy.ini                     # Strict mypy configuration
└── .env.example                 # Runtime configuration template
```

## Runtime entry point

The application starts through `python -m ig_reel_downloader`, which executes `ig_reel_downloader/__main__.py`.

Startup responsibilities:

1. Load `.env` via `python-dotenv`.
2. Configure logging and reduce `httpx` log verbosity.
3. Read environment variables:
   - `BOT_TOKEN` is required.
   - `OUTPUT_DIR` defaults to `output`.
   - `TELEGRAM_MEDIA_WRITE_TIMEOUT` defaults to `120` seconds.
   - `TELEGRAM_READ_TIMEOUT` defaults to `30` seconds.
4. Create the output directory.
5. Create `data/`.
6. Instantiate `SqliteRepository` for `data/reels.db`.
7. Instantiate the Instagram Reel downloader, `DownloaderRegistry`, `MediaFetchService`, and `TelegramMediaRenderer`.
8. Instantiate `IgReelDownloaderApp` with those collaborators.
9. Run Telegram polling.

The bot process does not run Alembic migrations. In Docker Compose deployments, the separate `migrate` service applies migrations before `downloader` starts.

## Core message flow

`IgReelDownloaderApp` in `app.py` owns the Telegram `Application` and registers one handler:

```text
MessageHandler(filters.TEXT, self._message_handler)
```

For each text message:

1. `DownloaderRegistry.extract_candidates()` asks registered downloaders to extract `UrlCandidate` URL spans, which are then resolved through each downloader's `resolve()` method.
2. The registry resolves provider identities with `ProviderItemRef`, resolves overlapping spans, and deduplicates while preserving message order by provider/media/item identity.
3. `IgReelDownloaderApp` offloads each `MediaFetchService.fetch(match)` call through `asyncio.to_thread(...)` because `yt-dlp` and SQLite operations are blocking/synchronous.
4. `MediaFetchService`:
   - Looks for a fresh generic cache row with `repository.get_media_by_provider_item(provider, media_kind, provider_item_id)`.
   - Reuses the cached item only when all referenced asset files still exist.
   - Calls the matched downloader on cache miss, stale cache, or missing local files.
   - Verifies the downloader returned the expected provider/media/item identity.
   - Persists successful downloads with `repository.insert_media(media)`.
5. Successful media items are passed to `TelegramMediaRenderer`:
   - One supported video: `chat.send_video(...)` with a caption containing title, likes, and description.
   - Multiple supported videos: `chat.send_media_group(...)` with `InputMediaVideo` items.
6. Failed downloads or unsupported rendered items are summarized as chat messages.
7. Telegram upload `TimedOut` errors are logged and reported to the user with a friendly timeout message.

### Quiet-skip behavior

Normal YouTube videos over 60 seconds are skipped without producing any bot response. `YouTubeDownloader.resolve()` returns `ResolveResult(request=None, skipped=True)` for these videos, and `IgReelDownloaderApp` ignores skipped fetch results without sending error messages to the chat.

## Downloading and URL handling

Downloader interfaces live in `downloaders/base.py`:

- `Downloader` defines URL extraction, provider identity resolution, and download operations.
- `ProviderItemRef` identifies media as `provider`, `media_kind`, and `provider_item_id`; its cache id is `provider:media_kind:provider_item_id`.
- `MediaDownloadResult` normalizes successful `MediaItem` downloads and failure reasons.

`downloaders/instagram.py` contains the Instagram Reel and Post `yt-dlp` integration:

- URL matching intentionally targets `https://www.instagram.com/reel/<id>` links specifically.
- `InstagramReelDownloader.download()` builds `yt-dlp` options:
  - Output template: `<output_dir>/%(id)s.%(ext)s`
  - Format: `best`
  - Quiet mode enabled
  - Optional `cookiefile` if `assets/cookies.txt` exists
- It extracts metadata first with `extract_info(..., download=False)`, computes the final local filepath with `prepare_filename(info)`, downloads the URL, and maps metadata into a generic `MediaItem` with one video `MediaAsset`.

Download failures are normalized into:

- `auth`: recognized `yt-dlp` errors that indicate Instagram authentication/cookies are required.
- `unsupported`: URLs or media shapes not supported by the current downloader/renderer.
- `unknown`: every other exception or mismatch.

`utils.py` contains only the shared authentication-error classifier used by the Instagram downloader.

## Persistence model

### Domain model

`repository/models.py` defines generic cache models:

```python
class MediaAsset(pydantic.BaseModel):
    asset_index: int
    asset_type: Literal["video", "image"]
    filepath: str
    mime_type: str | None = None
    width: int | None = None
    height: int | None = None
    duration_seconds: float | None = None
    file_size_bytes: int | None = None

class MediaItem(pydantic.BaseModel):
    id: str
    provider: str
    media_kind: str
    provider_item_id: str
    original_url: str
    title: str
    description: str | None
    metadata: dict[str, Any]
    assets: list[MediaAsset]
    created_at: datetime.datetime
    updated_at: datetime.datetime
```

`IgReel` remains only as a legacy model for migration tests and compatibility with existing `reels` rows. Runtime cache reads and writes use `MediaItem`/`MediaAsset`.

### Repository abstraction

`repository/base.py` defines a `Repository` Protocol with generic cache operations:

- `create_database()` for explicit migration/test callers.
- `get_media_by_provider_item(provider, media_kind, provider_item_id)`.
- `insert_media(media)`.

The app layer depends on `MediaFetchService` and the repository protocol rather than concrete SQLite APIs.

### SQLite implementation

`repository/sqlite.py` implements `SqliteRepository`:

- Uses SQLAlchemy 2.x for the concrete SQLite implementation.
- Enables SQLite foreign keys for every connection.
- Uses short-lived SQLAlchemy sessions for reads and writes.
- Reads fresh cache rows from `media_items` by provider/media/item identity.
- Writes `media_items` with SQLite `ON CONFLICT DO UPDATE` upsert semantics and replaces child `media_assets` atomically.
- Preserves the original `created_at` on refresh and uses `updated_at` for cache freshness.
- Exposes `create_database()` for tests and explicit migration callers; normal Docker Compose deployments run Alembic through the separate `migrate` service instead of bot startup.

The generic cache tables are:

- `media_items`: one row per provider/media/item identity, with metadata stored as JSON text and a unique constraint on `(provider, media_kind, provider_item_id)`.
- `media_assets`: ordered local assets for each media item, with a foreign key to `media_items` and a unique `(media_item_id, asset_index)` constraint.

The legacy `reels` table remains in the database for compatibility and is not dropped in this milestone. Existing databases that already contain `reels` are baselined and then copied into `media_items`/`media_assets` during migration without deleting `reels` rows. If a preexisting `reels` table is missing required columns or constraints, migration fails instead of stamping an incompatible schema.

Alembic tracks applied migrations in `alembic_version`. Docker Compose migrations run in the one-shot `migrate` service before `downloader` starts. Manual database tasks are also available through Poe:

- `uv run poe db-upgrade`
- `uv run poe db-downgrade`
- `uv run poe db-current`
- `uv run poe db-history`
- `uv run poe db-revision "message"`

Manual commands target `data/reels.db` by default. Set `DATABASE_URL` or `DB_PATH` to target another database for CLI migration work.

Cached media rows are considered time-fresh only while:

- `media_items.updated_at > now - CACHE_STALE_TIME`

`CACHE_STALE_TIME` is currently `24` hours. File existence is not checked by the repository; `MediaFetchService` validates referenced asset files before reusing cached rows.

## Filesystem state

Runtime state is split across three paths:

- `output/`: downloaded media files, named by Instagram ID and extension.
- `data/reels.db`: SQLite cache metadata.
- `assets/cookies.txt`: optional Instagram cookies for restricted reels.

The DB can contain rows whose files were removed by cleanup. This is expected: `MediaFetchService` verifies file existence before reusing a cached row and redownloads when any referenced file is missing.

## Deployment architecture

Docker deployment is defined by `Dockerfile`, `docker-compose.yaml`, `docker_entrypoint.sh`, and `clean.sh`.

### Image build

The Docker image:

1. Starts from `python:3.14-slim-trixie`.
2. Copies `uv` from `ghcr.io/astral-sh/uv`.
3. Uses `uv sync --locked --no-install-project` to install locked third-party dependencies first.
4. Copies the project into `/app`.
5. Runs `uv sync --locked` to install the project.
6. Uses `/bin/bash docker_entrypoint.sh` as the entrypoint.

### Compose services

`docker-compose.yaml` defines two services:

- `migrate`: a one-shot service that uses the app image to run `/app/.venv/bin/alembic upgrade head` with `./data:/app/data` mounted.
- `downloader`: the long-running bot service. It depends on `migrate` completing successfully before startup.

`downloader` has these mounts:

- `./${OUTPUT_DIR:-output}:/app/${OUTPUT_DIR:-output}`
- `./assets:/app/assets`
- `./.env:/app/.env`
- `./data:/app/data`

`downloader` restarts with `unless-stopped`; `migrate` does not restart.

### Cleanup loop

`docker_entrypoint.sh` starts a background cleanup loop before launching the bot:

```text
every 1800 seconds:
    /app/clean.sh
```

`clean.sh` reads `.env`, requires `OUTPUT_DIR`, defaults `MAX_FILES` to `100`, and deletes the oldest files when the output directory exceeds the configured limit.

## Configuration

The documented environment variables are:

| Variable | Required | Default | Purpose |
| --- | --- | --- | --- |
| `BOT_TOKEN` | yes | none | Telegram bot token. |
| `OUTPUT_DIR` | no | `output` | Download directory for video files. |
| `MAX_FILES` | no | `100` | Retention limit used by `clean.sh`. |
| `TELEGRAM_MEDIA_WRITE_TIMEOUT` | no | `120` | Telegram media upload write timeout. |
| `TELEGRAM_READ_TIMEOUT` | no | `30` | Telegram API read timeout. |

Optional cookies should be placed at `assets/cookies.txt`; the code passes them to `yt-dlp` only when the file exists.

## Quality gates

Developer tasks are defined in `pyproject.toml` via Poe:

- `uv run poe format-check`
- `uv run poe lint`
- `uv run poe typecheck`
- `uv run poe test`
- `uv run poe verify` for auto-format, lint-fix, typecheck, and tests
- `uv run poe check` for the read-only CI quality gate
- `uv run poe db-upgrade`, `db-current`, `db-history`, `db-downgrade`, and `db-revision` for Alembic migrations

The current test suite includes unit tests for downloader registry, Instagram URL matching/downloading seams, media fetching, Telegram rendering, authentication-error detection, app orchestration, and repository integration tests for SQLite/Alembic behavior.

## Important architectural constraints and notes

- The app is a single-process polling bot; there is no queue, scheduler, or web server.
- Downloads are performed with blocking `yt-dlp` calls and are offloaded from the async Telegram handler with `asyncio.to_thread()`.
- SQLite access goes through SQLAlchemy sessions; blocking repository work is still called from worker threads via the app's existing `asyncio.to_thread()` boundaries.
- Schema migrations are separate from bot startup; Docker Compose runs them through the one-shot `migrate` service before `downloader` starts.
- Multiple-video responses use a Telegram media group without per-item captions; single-video responses include the formatted caption.
- Cache invalidation is time-based (`24h`) and file-existence-based.
- Cleanup only removes media files; it does not prune stale SQLite rows.
- URL matching targets Instagram Reels, Instagram Posts, TikTok canonical/share links, YouTube Shorts, and YouTube Short/standard video links specifically.
- Auth failure detection is based on a specific `yt-dlp` Instagram error message.

## Extension points

Likely future extension points are:

- Add provider support by implementing `downloaders.base.Downloader` and registering it in `DownloaderRegistry`.
- Add repository implementations by satisfying `repository.base.Repository`.
- Add richer rendering behavior in `TelegramMediaRenderer` if Telegram API constraints and UX allow it.
- Add future DB schema changes as Alembic revisions under `migrations/versions/`.
- Broaden Instagram URL extraction inside `InstagramReelDownloader` if broader URL support is explicitly requested.
