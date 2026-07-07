# Architecture

_Last reviewed: 2026-07-07_

## Overview

`ig-reel-downloader` is a small Telegram bot that lets users send Instagram Reel links and receive the downloaded video back in Telegram. The app is implemented as a Python package with a single runtime process:

- `python-telegram-bot` handles Telegram long-polling and message delivery.
- `yt-dlp` extracts Reel metadata and downloads the media file.
- SQLite caches Reel metadata and local file paths for recently downloaded reels.
- Alembic manages SQLite schema creation and migrations.
- Docker Compose runs migrations in a one-shot container before starting the bot with persistent `data/`, `output/`, `assets/`, and `.env` mounts.

At a high level, the architecture is a simple layered app:

```text
Telegram user
    │
    ▼
Telegram Bot API / long polling
    │
    ▼
IgReelDownloaderApp
    ├── URL extraction and deduplication
    ├── cache lookup through Repository
    ├── blocking yt-dlp work offloaded to worker threads
    └── Telegram video/media-group responses
    │
    ├── SQLite database: data/reels.db
    ├── Downloaded files: output/<instagram-id>.<ext>
    └── Optional cookies: assets/cookies.txt
```

## Repository layout

```text
.
├── ig_reel_downloader/
│   ├── __main__.py              # Runtime entry point and environment wiring
│   ├── app.py                   # Telegram bot application and message flow
│   ├── constants.py             # Shared constants, currently cache TTL
│   ├── utils.py                 # yt-dlp integration, URL parsing, error classification
│   └── repository/
│       ├── base.py              # Repository Protocol
│       ├── models.py            # Pydantic domain models
│       └── sqlite.py            # SQLAlchemy/Alembic SQLite Repository implementation
├── migrations/                  # Alembic migration environment and revisions
├── tests/unit/                  # Unit tests for pure helpers
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
7. Instantiate `IgReelDownloaderApp`.
8. Configure download output and cookie path.
9. Run Telegram polling.

The bot process does not run Alembic migrations. In Docker Compose deployments, the separate `migrate` service applies migrations before `downloader` starts.

## Core message flow

`IgReelDownloaderApp` in `app.py` owns the Telegram `Application` and registers one handler:

```text
MessageHandler(filters.TEXT, self._message_handler)
```

For each text message:

1. The bot extracts Instagram Reel URLs with `utils.get_urls_from_text()`.
2. Duplicate URLs are removed while preserving order.
3. Each URL is processed concurrently using `asyncio.to_thread(...)` because `yt-dlp` and SQLite operations are blocking/synchronous.
4. For each URL, `_try_get_reel()`:
   - Extracts the Reel ID with `utils.get_id_from_url()`.
   - Looks for a fresh cached DB row using `repository.get_reel_by_id()`.
   - Reuses the cached row only if the referenced file still exists.
   - Otherwise downloads the media through `utils.download_video_result()`.
   - Inserts successful downloads into SQLite.
5. Successful reels are sent back to the Telegram chat:
   - One video: `chat.send_video(...)` with a caption containing title, likes, and description.
   - Multiple videos: `chat.send_media_group(...)` with `InputMediaVideo` items.
6. Failed downloads are summarized as chat messages.
7. Telegram upload `TimedOut` errors are logged and reported to the user with a friendly timeout message.

## Downloading and URL handling

`utils.py` contains the app's integration with `yt-dlp`:

- `download_video_result(url, output_dir, cookie_filepath)` builds `yt-dlp` options:
  - Output template: `<output_dir>/%(id)s.%(ext)s`
  - Format: `best`
  - Quiet mode enabled
  - Optional `cookiefile` if the configured cookie file exists
- It extracts metadata first with `extract_info(..., download=False)`.
- It computes the final local filepath with `prepare_filename(info)`.
- It then downloads the URL and maps metadata into `models.IgReel`.

URL parsing is intentionally narrow:

- `get_urls_from_text()` finds `https://www.instagram.com/reel/<id>` substrings.
- `get_id_from_url()` extracts the `<id>` portion from a Reel URL.

Download failures are normalized into:

- `auth`: recognized `yt-dlp` errors that indicate Instagram authentication/cookies are required.
- `unknown`: every other exception.

## Persistence model

### Domain model

`repository/models.py` defines the main domain object:

```python
class IgReel(pydantic.BaseModel):
    id: str
    title: str
    description: str | None
    filepath: str
    url: str
    comments: str
    like_count: int
    created_at: datetime.datetime = pydantic.Field(default_factory=datetime.datetime.now)
```

`comments` is stored as a JSON string created from the `yt-dlp` metadata.

### Repository abstraction

`repository/base.py` defines a `Repository` Protocol with three operations:

- `create_database()`
- `get_reel_by_id(reel_id)`
- `insert_reel(reel)`

This keeps the app layer independent from the concrete persistence mechanism, even though SQLite is currently the only implementation.

### SQLite implementation

`repository/sqlite.py` implements `SqliteRepository`:

- Uses SQLAlchemy 2.x for the concrete SQLite implementation.
- Defines a private `ReelRecord` ORM mapping for the existing `reels` table.
- Keeps `IgReel` as the public/domain model and maps at repository boundaries.
- Uses short-lived SQLAlchemy sessions for reads and writes.
- Uses SQLite `ON CONFLICT DO UPDATE` upsert semantics so repeated downloads update existing records while preserving other rows.
- Exposes `create_database()` for tests and explicit migration callers; normal Docker Compose deployments run Alembic through the separate `migrate` service instead of bot startup.

The database table is:

```sql
CREATE TABLE IF NOT EXISTS reels (
    id TEXT PRIMARY KEY,
    title TEXT,
    description TEXT,
    filepath TEXT,
    url TEXT,
    like_count INTEGER,
    created_at DATETIME,
    comments TEXT
);
```

Alembic tracks applied migrations in `alembic_version`. The initial migration creates `reels` when missing. Existing unversioned databases with the legacy `reels` table are baselined without dropping rows; the migration validates that required columns exist before stamping the revision. The initial downgrade is intentionally data-preserving and does not drop `reels`.

Docker Compose migrations run in the one-shot `migrate` service before `downloader` starts. Manual database tasks are also available through Poe:

- `uv run poe db-upgrade`
- `uv run poe db-downgrade`
- `uv run poe db-current`
- `uv run poe db-history`
- `uv run poe db-revision "message"`

Manual commands target `data/reels.db` by default. Set `DATABASE_URL` or `DB_PATH` to target another database for CLI migration work.

Cached reels are considered valid only while:

- `created_at > now - REEL_STALE_TIME`
- the referenced media file still exists on disk

`REEL_STALE_TIME` is currently `24` hours.

## Filesystem state

Runtime state is split across three paths:

- `output/`: downloaded media files, named by Instagram ID and extension.
- `data/reels.db`: SQLite cache metadata.
- `assets/cookies.txt`: optional Instagram cookies for restricted reels.

The DB can contain rows whose files were removed by cleanup. This is expected: `_try_get_reel()` verifies file existence before reusing a cached row and redownloads when the file is missing.

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
- `uv run poe check` for all of the above
- `uv run poe db-upgrade`, `db-current`, `db-history`, `db-downgrade`, and `db-revision` for Alembic migrations

The current test suite includes unit tests for URL parsing/authentication-error detection and repository integration tests for SQLite/Alembic behavior.

## Important architectural constraints and notes

- The app is a single-process polling bot; there is no queue, scheduler, or web server.
- Downloads are performed with blocking `yt-dlp` calls but are offloaded from the async Telegram handler with `asyncio.to_thread()`.
- SQLite access goes through SQLAlchemy sessions; blocking repository work is still called from worker threads via the app's existing `asyncio.to_thread()` boundaries.
- Schema migrations are separate from bot startup; Docker Compose runs them through the one-shot `migrate` service before `downloader` starts.
- Multiple-video responses use a Telegram media group without per-item captions; single-video responses include the formatted caption.
- Cache invalidation is time-based (`24h`) and file-existence-based.
- Cleanup only removes media files; it does not prune stale SQLite rows.
- URL matching currently targets `https://www.instagram.com/reel/...` links specifically.
- Auth failure detection is based on a specific `yt-dlp` Instagram error message.

## Extension points

Likely future extension points are:

- Add repository implementations by satisfying `repository.base.Repository`.
- Broaden URL extraction in `utils.get_urls_from_text()` for more Instagram URL variants.
- Expand tests around `IgReelDownloaderApp` by injecting a fake `Repository` and mocking `utils.download_video_result()`.
- Add future DB schema changes as Alembic revisions under `migrations/versions/`.
- Add richer media-group captions if Telegram API constraints and UX allow it.
