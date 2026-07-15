#!/bin/bash
# Preflight check for PostgreSQL deployment.
# Validates that required credentials, URLs, and paths exist.
# Usage: docker/preflight.sh [deploy-dir]
#   deploy-dir defaults to "."

set -euo pipefail

DEPLOY_DIR="${1:-.}"
ENV_FILE="${DEPLOY_DIR}/.env"

if [ ! -f "$ENV_FILE" ]; then
    echo "::error file=${ENV_FILE}::FATAL: .env file not found at ${ENV_FILE}"
    exit 1
fi

# shellcheck source=/dev/null
set -a
source "$ENV_FILE"
set +a

errors=0

check_var() {
    local var_name="$1"
    local desc="$2"
    local url_pattern="${3:-}"

    if [ -z "${!var_name:-}" ]; then
        echo "::error::FATAL: ${var_name} (${desc}) is not set in .env"
        errors=$((errors + 1))
    elif [ -n "$url_pattern" ] && [[ ! "${!var_name}" == $url_pattern ]]; then
        echo "::error::FATAL: ${var_name} does not match required pattern ${url_pattern}"
        errors=$((errors + 1))
    fi
}

# --- PostgreSQL credentials ---
check_var "POSTGRES_DB"          "PostgreSQL database name"
check_var "POSTGRES_USER"        "PostgreSQL bootstrap/owner user"
check_var "POSTGRES_PASSWORD"    "PostgreSQL bootstrap/owner password"
check_var "DB_MIGRATION_USER"    "Database migration user"
check_var "DB_MIGRATION_PASSWORD" "Database migration password"
check_var "DB_APP_USER"          "Database application user"
check_var "DB_APP_PASSWORD"      "Database application password"
check_var "DB_MIGRATION_URL"     "Migration database URL" "postgresql+psycopg://*"
check_var "DATABASE_URL"         "Application database URL" "postgresql+psycopg://*"

# --- Exact URL endpoint, role, and password validation ---
urlencode() {
    local value="$1"
    local encoded=""
    local char
    local hex
    local i
    local LC_ALL=C

    for ((i = 0; i < ${#value}; i++)); do
        char="${value:i:1}"
        case "$char" in
            [a-zA-Z0-9.~_-])
                encoded+="$char"
                ;;
            *)
                printf -v hex '%02X' "'${char}"
                encoded+="%${hex}"
                ;;
        esac
    done
    printf '%s' "$encoded"
}

validate_url() {
    local var_name="$1"
    local expected_user="$2"
    local expected_password="$3"
    local url="${!var_name:-}"
    [ -n "$url" ] || return

    local expected_url
    expected_url="postgresql+psycopg://$(urlencode "$expected_user"):$(urlencode "$expected_password")@postgres:5432/$(urlencode "${POSTGRES_DB:-}")"
    if [ "$url" != "$expected_url" ]; then
        echo "::error::FATAL: ${var_name} does not exactly match its configured role, password, postgres:5432 endpoint, and POSTGRES_DB"
        errors=$((errors + 1))
    fi
}

validate_url "DB_MIGRATION_URL" "${DB_MIGRATION_USER:-}" "${DB_MIGRATION_PASSWORD:-}"
validate_url "DATABASE_URL" "${DB_APP_USER:-}" "${DB_APP_PASSWORD:-}"

if [ $errors -gt 0 ]; then
    echo "::error::Preflight FAILED with ${errors} error(s)"
    exit 1
fi

echo "Preflight PASSED — all required PostgreSQL variables present and valid."
