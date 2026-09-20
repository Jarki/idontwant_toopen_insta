# Instagram Reel Downloader

This is a Telegram bot that downloads short videos and Instagram post media from supported social links.

## Installation

### 1. Clone the repository

```bash
git clone https://github.com/Jarki/idontwant_toopen_insta.git
cd idontwant_toopen_insta
```

### 2. Copy .env.example to .env and fill in the required values using your preferred text editor

Note: Optional values can be skipped. If uploads of large reels time out, increase `TELEGRAM_MEDIA_WRITE_TIMEOUT` in `.env`.

```bash
cp .env.example .env
```

### 3. (Optional) Create a cookies.txt file

If you want to download account-restricted media, create a Netscape-format
`cookies.txt` file and put it in the `assets` directory. The same file is used for
all providers. Refer to https://github.com/yt-dlp/yt-dlp/wiki/FAQ#how-do-i-pass-cookies-to-yt-dlp

Reddit normally allows public and age-marked posts without an account. Private or
quarantined communities require cookies exported from a browser that is logged in
to Reddit and already has access to that community. Do not configure a Reddit
username/password in the bot; export the browser cookies instead. Treat the file
as a secret and never commit it.

### 4. Run the bot using Docker Compose

```bash
docker compose -f docker/compose.yaml up -d --build
```

## Usage

Send a supported link to the bot and it will download the media when supported:

- Instagram Reels
- Instagram posts, including carousel posts
- TikTok videos
- Reddit hosted videos, images, image galleries, link-post previews, and text posts
- X (formerly Twitter) posts, including text, photos, and video
- YouTube Shorts
- Normal YouTube videos under 60 seconds

X videos larger than 100 MB, or whose size cannot be determined, are rejected before download.

Usage totals are available through Telegram commands:

- `/stats` shows the caller's requested, delivered, delivery-failed, and download-failed totals in the current chat, plus confirmed deliveries by media type.
- `/top` shows the top 10 media requesters in the current chat, including unsuccessful requests.

Chat-scoped statistics begin after the migration that adds chat IDs; older requests cannot be assigned to their original chats retroactively.

### Enabling and disabling downloaders

Each downloader is enabled by default and can be disabled with its corresponding
boolean environment variable:

- `INSTAGRAM_REEL_DOWNLOADER_ENABLED`
- `INSTAGRAM_POST_DOWNLOADER_ENABLED`
- `TIKTOK_DOWNLOADER_ENABLED`
- `REDDIT_DOWNLOADER_ENABLED`
- `X_DOWNLOADER_ENABLED`
- `YOUTUBE_DOWNLOADER_ENABLED`

Accepted values are `true`/`false`, `1`/`0`, `yes`/`no`, and `on`/`off`. When a
downloader is disabled, the bot ignores its URLs without replying. The manually
triggered **Deploy Dev** and **Deploy Prod** GitHub Actions workflows expose an
enabled-by-default checkbox for each downloader and pass those choices to the
deployed bot. Deployments build the branch selected in **Use workflow from**;
`compose_ref` can explicitly override that ref when needed.

### TikTok extractor smoke test

TikTok may return bot-detection responses depending on the machine's public IP and
TLS fingerprint. The automated suite simulates known failure responses without
network access. To reproduce the real extractor behavior from the bot host, run
the opt-in smoke test with a currently public TikTok video:

```bash
TIKTOK_SMOKE_TEST_URL='https://www.tiktok.com/@user/video/1234567890' \
  uv run pytest tests/e2e/test_tiktok_live.py -v -s
```

A newline-delimited corpus can be tested in one run:

```bash
TIKTOK_SMOKE_TEST_URLS_FILE=/path/to/tiktok-urls.txt \
  uv run pytest tests/e2e/test_tiktok_live.py -v -s
```

The test is skipped unless one of these variables is set. It downloads videos to
pytest's temporary directory and does not contact Telegram or PostgreSQL.

### Judgmental GIFs

If `JUDGMENTAL_CHANCE` is enabled, the bot can reply with a stored judgmental Telegram GIF instead of downloading. To seed one, send a GIF/animation to Telegram, then reply to that GIF with:

```text
/add-judgmental
```

The bot stores Telegram's `file_id` in PostgreSQL and reuses it later, avoiding unreliable external GIF URLs.

## Database

The bot stores Telegram users, detected link requests, cached media metadata, failed download attempts, reusable Telegram media file IDs, delivery outcomes, and judgmental animation file IDs in PostgreSQL. After an asset is uploaded once, later fresh cache hits send Telegram's `file_id` instead of uploading the local file again. Mutable Telegram profile fields are refreshed whenever that user submits a detected link. Each request records the submitted and normalized URLs, provider/media identity when known, timestamp, requesting Telegram user when available, and Telegram chat ID for chat-scoped statistics. Successful downloads reference the cached media item that satisfied them, download failures record their normalized reason directly, and confirmed Telegram deliveries record a delivery timestamp. Schema changes are managed with Alembic, running separately from the bot process.

### Configuration

Set `DATABASE_URL` in `.env` using the `postgresql+psycopg://` scheme:

```text
DATABASE_URL=postgresql+psycopg://app:password@postgres:5432/ig_reel_downloader
```

Connection URLs must use the `postgresql+psycopg` dialect. Plain `postgres://` URLs are rejected. URL credentials must match the corresponding role variables; percent-encode reserved characters in user names, passwords, and database names.

### Compose service ordering

Docker Compose enforces this startup graph:

1. `postgres` becomes healthy (its port is exposed only to the Compose network).
2. `postgres-bootstrap` creates or validates the distinct migration, bot, and Error API roles.
3. `migrate` applies the public application migration history.
4. `error-api-migrate` applies the independent observability migration history.
5. `downloader` and `error-api` start independently after both histories are current.

Neither long-running process runs migrations. The downloader depends on
`error-api-migrate`, never on Error API availability, so stopping the API does
not stop Telegram polling or direct ledger recording. PostgreSQL has no
published host port. Production publishes on `127.0.0.1:8000` and development
on `127.0.0.1:8001` by default; `ERROR_API_HOST_PORT` explicitly overrides
either loopback-only port.

### Schema migrations

Both migration histories run separately from their runtimes. To apply them
manually through Compose, in order:

```bash
docker compose -f docker/compose.yaml run --rm migrate
docker compose -f docker/compose.yaml run --rm error-api-migrate
```

Manual local commands are also available through Poe:

```bash
uv run poe db-upgrade
uv run poe db-current
uv run poe error-db-upgrade
uv run poe error-db-current
```

Main migrations use `DATABASE_URL` and Error API migrations use
`DB_MIGRATION_URL`; both require the runtime role names so migrations can apply
explicit least-privilege grants. The Error API runtime URL is never used for
DDL.

Poe commands use `DATABASE_URL` from the environment. PostgreSQL migrations
also require both runtime role names so Alembic can preserve their isolation
from migration metadata:

```bash
DB_APP_USER=db_app \
DB_ERROR_API_USER=db_error_api \
DATABASE_URL=postgresql+psycopg://db_migration:pass@localhost:5432/db \
  uv run poe db-upgrade
```

## Private Error API operations

The API remains authenticated even though it is private. It accepts independent
read and triage bearer keys: read keys can inspect data but cannot mutate it;
triage keys can perform only the documented rename, status, note, link, and
mark-fixed operations. Generate high-entropy keys, give each a non-secret audit
label, and never share database credentials with agents.

To expose the loopback listener to the tailnet, configure Tailscale Serve on the
host (for example, forward a tailnet HTTPS name to
`http://127.0.0.1:8000`). Do not change Compose to a public bind. Tailnet ACLs
should allow only the agent identities that need the API, while the API key
remains mandatory as a second boundary.

For key rotation, add a new key and label to the matching `*_KEY_NEXT` and
`*_LABEL_NEXT` pair, restart only `error-api`, move clients to the next key,
then promote it to the current pair and clear the next pair. Read and triage
keys must stay distinct. Rotation does not require a migration or bot restart.

Agents use the HTTP-only client without shell or database access:

```bash
ERROR_API_URL='https://errors.example-tailnet.ts.net' \
ERROR_API_KEY='<scoped-key>' \
  uv run bot-ops errors list --status new
uv run bot-ops errors show ERR-1842
uv run bot-ops errors repro ERR-1842 --limit 3
uv run bot-ops errors status ERR-1842 investigating
uv run bot-ops errors note ERR-1842 'Reproduced in dev'
uv run bot-ops errors mark-fixed ERR-1842 --at now
```

If an API migration fails, leave both runtimes behind the failed gate, correct
the migration/configuration, and rerun the two one-shot migration services.
If only the Error API runtime fails, keep the downloader running, inspect its
container logs, repair the API, and restart `error-api`; ledger recording
continues directly to PostgreSQL. Restore recovery must stop both runtimes,
restore the database, rerun bootstrap and both migration gates, then start both
runtimes.

### Backup and rollback

Create PostgreSQL backups and verify them before relying on them for rollback:

1. Create a custom-format backup on the host:

   ```bash
   docker compose exec postgres sh -c \
     'pg_dump --format=custom -U "$POSTGRES_USER" "$POSTGRES_DB"' \
     > backup.dump
   ```

2. Restore into a disposable database and verify application rows:

   ```bash
   docker compose exec postgres sh -c \
     'dropdb --if-exists -U "$POSTGRES_USER" ig_verify &&
      createdb -U "$POSTGRES_USER" ig_verify'
   docker compose exec -T postgres sh -c \
     'pg_restore --exit-on-error -U "$POSTGRES_USER" -d ig_verify' \
     < backup.dump
   docker compose exec postgres sh -c \
     'psql -U "$POSTGRES_USER" -d ig_verify \
      -c "SELECT COUNT(*) FROM media_items"'
   docker compose exec postgres sh -c \
     'dropdb -U "$POSTGRES_USER" ig_verify'
   ```

3. To roll back after PostgreSQL writes have begun, stop both runtimes, replace
   the target database, restore the verified archive, reapply bootstrap grants,
   and restart through both migration gates:

   ```bash
   docker compose stop downloader error-api
   docker compose exec postgres sh -c \
     'dropdb --force -U "$POSTGRES_USER" "$POSTGRES_DB" &&
      createdb -U "$POSTGRES_USER" "$POSTGRES_DB"'
   docker compose exec -T postgres sh -c \
     'pg_restore --exit-on-error -U "$POSTGRES_USER" -d "$POSTGRES_DB"' \
     < backup.dump
   docker compose run --rm postgres-bootstrap
   docker compose run --rm migrate
   docker compose run --rm error-api-migrate
   docker compose up -d downloader error-api
   ```

### Filesystem state

The runtime bot only writes to the filesystem for downloaded media:

- `output/` (or `OUTPUT_DIR`): downloaded media files.
- `assets/`: optional cookies and static resources.

The database is accessed over the network; no `data/` directory mount is required at runtime.
