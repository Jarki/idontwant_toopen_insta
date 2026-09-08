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

The bot stores Telegram users, detected link requests, cached media metadata, failed download attempts, reusable Telegram media file IDs, and judgmental animation file IDs in PostgreSQL. After an asset is uploaded once, later fresh cache hits send Telegram's `file_id` instead of uploading the local file again. Mutable Telegram profile fields are refreshed whenever that user submits a detected link. Each request records the submitted and normalized URLs, provider/media identity when known, timestamp, and requesting Telegram user when available. Successful requests reference the cached media item that satisfied them; failed requests record their normalized reason directly. Schema changes are managed with Alembic, running separately from the bot process.

### Configuration

Set `DATABASE_URL` in `.env` using the `postgresql+psycopg://` scheme:

```text
DATABASE_URL=postgresql+psycopg://app:password@postgres:5432/ig_reel_downloader
```

Connection URLs must use the `postgresql+psycopg` dialect. Plain `postgres://` URLs are rejected. URL credentials must match the corresponding role variables; percent-encode reserved characters in user names, passwords, and database names.

### Compose service ordering

Docker Compose starts database-related services in this order:

1. `postgres` — PostgreSQL container with persistent named volume and `pg_isready` healthcheck.
2. `postgres-bootstrap` — one-shot service that creates migration and application roles, grants privileges, and exits. Rerunnable against an existing volume.
3. `migrate` — one-shot Alembic migration service that applies pending schema changes against PostgreSQL.
4. `downloader` — the long-running bot. It starts only after migration completes successfully.

Port `5432` is not published to the host by default. Administrative access uses a network-attached Compose service or a temporary controlled host port.

### Schema migrations

Migrations run separately from the bot process. Docker Compose applies them through the one-shot `migrate` service before the bot starts.

To run migrations manually through Compose:

```bash
docker compose -f docker/compose.yaml run --rm migrate
```

Manual local migration commands are also available through Poe:

```bash
uv run poe db-upgrade    # upgrade the configured database to the latest schema
uv run poe db-current    # show the current migration revision
uv run poe db-history    # list migration history
uv run poe db-downgrade  # downgrade one revision
uv run poe db-revision "message"  # create an autogenerated revision
```

Poe commands use `DATABASE_URL` from the environment. PostgreSQL migrations
also require `DB_APP_USER` so Alembic can keep its metadata migration-only:

```bash
DB_APP_USER=db_app \
DATABASE_URL=postgresql+psycopg://db_migration:pass@localhost:5432/db \
  uv run poe db-upgrade
```

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

3. To roll back after PostgreSQL writes have begun, stop the bot, replace the
   target database, restore the verified archive, reapply bootstrap grants, and
   restart through the migration gate:

   ```bash
   docker compose stop downloader
   docker compose exec postgres sh -c \
     'dropdb --force -U "$POSTGRES_USER" "$POSTGRES_DB" &&
      createdb -U "$POSTGRES_USER" "$POSTGRES_DB"'
   docker compose exec -T postgres sh -c \
     'pg_restore --exit-on-error -U "$POSTGRES_USER" -d "$POSTGRES_DB"' \
     < backup.dump
   docker compose run --rm postgres-bootstrap
   docker compose run --rm migrate
   docker compose up -d downloader
   ```

### Filesystem state

The runtime bot only writes to the filesystem for downloaded media:

- `output/` (or `OUTPUT_DIR`): downloaded media files.
- `assets/`: optional cookies and static resources.

The database is accessed over the network; no `data/` directory mount is required at runtime.
