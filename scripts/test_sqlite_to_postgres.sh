#!/bin/bash
set -euo pipefail

project="ig-reel-downloader-pg-test-${$}"
workdir="$(mktemp -d)"
export PG_TEST_DATA_DIR="${workdir}/data"
export PG_TEST_OUTPUT_DIR="${workdir}/output"
export PG_TEST_PORT="${PG_TEST_PORT:-55432}"
export POSTGRES_DB="pg_transfer_test"
export POSTGRES_USER="transfer_owner"
export POSTGRES_PASSWORD="owner password"
export DB_MIGRATION_USER="transfer_migration"
export DB_MIGRATION_PASSWORD="migration password"
export DB_APP_USER="transfer_app"
export DB_APP_PASSWORD="app password"
DB_MIGRATION_PASSWORD_URL="migration%20password"
DB_APP_PASSWORD_URL="app%20password"
export DB_MIGRATION_URL="postgresql+psycopg://${DB_MIGRATION_USER}:${DB_MIGRATION_PASSWORD_URL}@postgres:5432/${POSTGRES_DB}"
export DATABASE_URL="postgresql+psycopg://${DB_APP_USER}:${DB_APP_PASSWORD_URL}@postgres:5432/${POSTGRES_DB}"
export COMPOSE_PROFILES=""

compose=(
    docker compose
    -p "${project}"
    -f docker-compose.yaml
    -f docker-compose.prod.override.yaml
    -f docker-compose.pg-test.override.yaml
)

active_services="$("${compose[@]}" config --services)"
if [[ " ${active_services//$'\n'/ } " == *" postgres-transfer "* ]]; then
    echo "Profile-gated postgres-transfer appeared in default startup" >&2
    exit 1
fi

cleanup() {
    "${compose[@]}" down --volumes --remove-orphans >/dev/null 2>&1 || true
    rm -rf "${workdir}"
}
trap cleanup EXIT

mkdir -p "${PG_TEST_DATA_DIR}" "${PG_TEST_OUTPUT_DIR}"
mkdir -p "${workdir}/preflight"
cat >"${workdir}/preflight/.env" <<EOF
POSTGRES_DB=${POSTGRES_DB}
POSTGRES_USER=${POSTGRES_USER}
POSTGRES_PASSWORD="${POSTGRES_PASSWORD}"
DB_MIGRATION_USER=${DB_MIGRATION_USER}
DB_MIGRATION_PASSWORD="${DB_MIGRATION_PASSWORD}"
DB_APP_USER=${DB_APP_USER}
DB_APP_PASSWORD="${DB_APP_PASSWORD}"
DB_MIGRATION_URL=${DB_MIGRATION_URL}
DATABASE_URL=${DATABASE_URL}
EOF
bash scripts/preflight.sh "${workdir}/preflight"
mkdir -p "${workdir}/invalid-preflight"
cp "${workdir}/preflight/.env" "${workdir}/invalid-preflight/.env"
cat >>"${workdir}/invalid-preflight/.env" <<EOF
DATABASE_URL=postgresql+psycopg://${DB_APP_USER}:wrong-password@postgres:5432/${POSTGRES_DB}
EOF
if bash scripts/preflight.sh "${workdir}/invalid-preflight" >/dev/null 2>&1; then
    echo "Preflight unexpectedly accepted a mismatched URL password" >&2
    exit 1
fi
uv run python scripts/create_transfer_fixture.py \
    --sqlite-path "${PG_TEST_DATA_DIR}/reels.db" \
    --output-dir "${PG_TEST_OUTPUT_DIR}"
uv run python scripts/create_transfer_fixture.py \
    --sqlite-path "${PG_TEST_DATA_DIR}/invalid-asset.db" \
    --output-dir "${PG_TEST_OUTPUT_DIR}" \
    --invalid-asset-type
uv run python scripts/create_transfer_fixture.py \
    --sqlite-path "${PG_TEST_DATA_DIR}/invalid-metadata.db" \
    --output-dir "${PG_TEST_OUTPUT_DIR}" \
    --invalid-metadata-shape
uv run python scripts/create_transfer_fixture.py \
    --sqlite-path "${PG_TEST_DATA_DIR}/dirty-legacy.db" \
    --output-dir "${PG_TEST_OUTPUT_DIR}" \
    --dirty-legacy
source_checksum="$(sha256sum "${PG_TEST_DATA_DIR}/reels.db" | cut -d ' ' -f 1)"
media_checksum="$(sha256sum "${PG_TEST_OUTPUT_DIR}/existing.mp4" | cut -d ' ' -f 1)"

"${compose[@]}" build postgres-transfer
"${compose[@]}" run --rm --no-deps --entrypoint /app/clean.sh downloader
"${compose[@]}" up -d --wait postgres

transfer_output="$(
    "${compose[@]}" run --rm postgres-transfer \
        --sqlite-path /app/data/reels.db \
        --upgrade-schema \
        --verify
)"
printf '%s\n' "${transfer_output}"
[[ "${transfer_output}" == *"Transfer complete."* ]]
[[ "${transfer_output}" == *"/app/output/missing.jpg"* ]]

if "${compose[@]}" run --rm postgres-transfer \
    --sqlite-path /app/data/reels.db --verify >/dev/null 2>&1; then
    echo "Second transfer unexpectedly overwrote a non-empty target" >&2
    exit 1
fi

source_after="$(sha256sum "${PG_TEST_DATA_DIR}/reels.db" | cut -d ' ' -f 1)"
media_after="$(sha256sum "${PG_TEST_OUTPUT_DIR}/existing.mp4" | cut -d ' ' -f 1)"
[[ "${source_checksum}" == "${source_after}" ]]
[[ "${media_checksum}" == "${media_after}" ]]

psql_owner() {
    "${compose[@]}" exec -T -e PGPASSWORD="${POSTGRES_PASSWORD}" postgres \
        psql -v ON_ERROR_STOP=1 -h postgres -U "${POSTGRES_USER}" \
        -d "${POSTGRES_DB}" "$@"
}

for expectation in \
    "media_items:1" \
    "media_assets:2" \
    "judgmental_animations:2" \
    "reels:1"; do
    table="${expectation%%:*}"
    expected="${expectation##*:}"
    actual="$(psql_owner -Atc "SELECT COUNT(*) FROM ${table}")"
    [[ "${actual}" == "${expected}" ]]
done

for invalid_source in invalid-asset.db invalid-metadata.db dirty-legacy.db; do
    if "${compose[@]}" run --rm postgres-transfer \
        --sqlite-path "/app/data/${invalid_source}" \
        --reset-target --verify >/dev/null 2>&1; then
        echo "Invalid source ${invalid_source} unexpectedly transferred" >&2
        exit 1
    fi
    [[ "$(psql_owner -Atc 'SELECT COUNT(*) FROM media_items')" == "1" ]]
done

skip_output="$(
    "${compose[@]}" run --rm postgres-transfer \
        --sqlite-path /app/data/dirty-legacy.db \
        --skip-legacy-reels \
        --reset-target \
        --verify
)"
[[ "${skip_output}" == *"Legacy reels: skipped"* ]]
[[ "$(psql_owner -Atc 'SELECT COUNT(*) FROM reels')" == "0" ]]

"${compose[@]}" exec -T -e PGPASSWORD="${DB_APP_PASSWORD}" postgres \
    psql -v ON_ERROR_STOP=1 -h postgres -U "${DB_APP_USER}" \
    -d "${POSTGRES_DB}" -c \
    "INSERT INTO judgmental_animations (file_id, file_unique_id, created_at, updated_at) VALUES ('sequence-probe', 'sequence-probe', NOW(), NOW())"

if "${compose[@]}" exec -T -e PGPASSWORD="${DB_APP_PASSWORD}" postgres \
    psql -v ON_ERROR_STOP=1 -h postgres -U "${DB_APP_USER}" \
    -d "${POSTGRES_DB}" -c "CREATE TABLE app_must_not_create (id integer)" \
    >/dev/null 2>&1; then
    echo "Application role unexpectedly executed DDL" >&2
    exit 1
fi

for migration_table in alembic_version reels; do
    if "${compose[@]}" exec -T -e PGPASSWORD="${DB_APP_PASSWORD}" postgres \
        psql -v ON_ERROR_STOP=1 -h postgres -U "${DB_APP_USER}" \
        -d "${POSTGRES_DB}" -c "SELECT * FROM ${migration_table}" \
        >/dev/null 2>&1; then
        echo "Application role unexpectedly read ${migration_table}" >&2
        exit 1
    fi
done

"${compose[@]}" exec -T -e PGPASSWORD="${DB_MIGRATION_PASSWORD}" postgres \
    psql -v ON_ERROR_STOP=1 -h postgres -U "${DB_MIGRATION_USER}" \
    -d "${POSTGRES_DB}" -c \
    "CREATE TABLE migration_can_create (id integer); DROP TABLE migration_can_create"

psql_owner -c "CREATE DATABASE transfer_contract OWNER ${DB_MIGRATION_USER}"
PGTEST_URL="postgresql+psycopg://${DB_MIGRATION_USER}:${DB_MIGRATION_PASSWORD_URL}@127.0.0.1:${PG_TEST_PORT}/transfer_contract" \
    uv run pytest tests/integration/repository/test_sqlite_to_postgres.py -q
psql_owner -c "DROP DATABASE transfer_contract"

psql_owner -c "CREATE DATABASE repository_contract OWNER ${DB_MIGRATION_USER}"
"${compose[@]}" exec -T -e PGPASSWORD="${POSTGRES_PASSWORD}" postgres \
    psql -v ON_ERROR_STOP=1 -h postgres -U "${POSTGRES_USER}" \
    -d repository_contract -c \
    "REVOKE ALL ON SCHEMA public FROM PUBLIC;
     GRANT USAGE, CREATE ON SCHEMA public TO ${DB_MIGRATION_USER};
     GRANT USAGE ON SCHEMA public TO ${DB_APP_USER};
     ALTER DEFAULT PRIVILEGES FOR ROLE ${DB_MIGRATION_USER} IN SCHEMA public
       GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO ${DB_APP_USER};
     ALTER DEFAULT PRIVILEGES FOR ROLE ${DB_MIGRATION_USER} IN SCHEMA public
       GRANT USAGE ON SEQUENCES TO ${DB_APP_USER};"
DB_MIGRATION_URL="postgresql+psycopg://${DB_MIGRATION_USER}:${DB_MIGRATION_PASSWORD_URL}@127.0.0.1:${PG_TEST_PORT}/repository_contract" \
DATABASE_URL="postgresql+psycopg://${DB_APP_USER}:${DB_APP_PASSWORD_URL}@127.0.0.1:${PG_TEST_PORT}/repository_contract" \
    uv run pytest tests/integration/repository/test_postgres_repository.py -q
psql_owner -c "DROP DATABASE repository_contract"

"${compose[@]}" up -d --force-recreate --wait postgres
[[ "$(psql_owner -Atc 'SELECT COUNT(*) FROM media_items')" == "1" ]]
"${compose[@]}" run --rm postgres-bootstrap
[[ "$(psql_owner -Atc 'SELECT COUNT(*) FROM media_items')" == "1" ]]

# Exercise the production dependency chain: bootstrap -> migrate -> downloader.
# The test override makes downloader a one-shot application-role runtime probe.
"${compose[@]}" up --abort-on-container-exit --exit-code-from downloader downloader
downloader_container="$("${compose[@]}" ps -aq downloader)"
downloader_env="$(
    docker inspect --format '{{range .Config.Env}}{{println .}}{{end}}' \
        "${downloader_container}"
)"
for privileged_var in \
    POSTGRES_PASSWORD \
    DB_MIGRATION_URL \
    DB_MIGRATION_PASSWORD \
    DB_APP_PASSWORD; do
    if [[ "${downloader_env}" == *"${privileged_var}="* ]]; then
        echo "Downloader unexpectedly received ${privileged_var}" >&2
        exit 1
    fi
done

psql_owner -c "CREATE DATABASE pg_transfer_restore"
"${compose[@]}" exec -T -e PGPASSWORD="${POSTGRES_PASSWORD}" postgres \
    pg_dump -h postgres -U "${POSTGRES_USER}" "${POSTGRES_DB}" \
    > "${workdir}/backup.sql"
"${compose[@]}" exec -T -e PGPASSWORD="${POSTGRES_PASSWORD}" postgres \
    psql -v ON_ERROR_STOP=1 -h postgres -U "${POSTGRES_USER}" \
    -d pg_transfer_restore < "${workdir}/backup.sql"
restored_count="$(
    "${compose[@]}" exec -T -e PGPASSWORD="${POSTGRES_PASSWORD}" postgres \
        psql -At -h postgres -U "${POSTGRES_USER}" \
        -d pg_transfer_restore -c "SELECT COUNT(*) FROM media_items"
)"
[[ "${restored_count}" == "1" ]]
"${compose[@]}" exec -T -e PGPASSWORD="${POSTGRES_PASSWORD}" postgres \
    dropdb -h postgres -U "${POSTGRES_USER}" pg_transfer_restore

echo "PostgreSQL transfer Compose test passed"
