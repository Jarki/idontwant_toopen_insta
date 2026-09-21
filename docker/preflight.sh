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

reject_known_value() {
    local var_name="$1"
    local known_value="$2"
    if [ -n "${!var_name:-}" ] && [ "${!var_name}" = "$known_value" ]; then
        echo "::error::FATAL: ${var_name} still uses a documented example value"
        errors=$((errors + 1))
    fi
}

# --- Runtime secrets and PostgreSQL credentials ---
check_var "BOT_TOKEN"            "Telegram bot token"
check_var "APP_RELEASE"          "Immutable application release identifier"
check_var "ERROR_API_READ_KEY"   "Current read-only Error API key"
check_var "ERROR_API_READ_LABEL" "Current read-only Error API key label"
check_var "ERROR_API_TRIAGE_KEY" "Current triage Error API key"
check_var "ERROR_API_TRIAGE_LABEL" "Current triage Error API key label"

check_var "POSTGRES_DB"          "PostgreSQL database name"
check_var "POSTGRES_USER"        "PostgreSQL bootstrap/owner user"
check_var "POSTGRES_PASSWORD"    "PostgreSQL bootstrap/owner password"
check_var "DB_MIGRATION_USER"    "Database migration user"
check_var "DB_MIGRATION_PASSWORD" "Database migration password"
check_var "DB_APP_USER"          "Database application user"
check_var "DB_APP_PASSWORD"      "Database application password"
check_var "DB_ERROR_API_USER"    "Restricted Error API user"
check_var "DB_ERROR_API_PASSWORD" "Restricted Error API password"
check_var "DB_MIGRATION_URL"     "Migration database URL" "postgresql+psycopg://*"
check_var "DATABASE_URL"         "Application database URL" "postgresql+psycopg://*"
check_var "ERROR_API_DATABASE_URL" "Restricted Error API database URL" "postgresql+psycopg://*"

reject_known_value "DB_ERROR_API_PASSWORD" "change_me_error_api"
reject_known_value \
    "ERROR_API_DATABASE_URL" \
    "postgresql+psycopg://db_error_api:change_me_error_api@postgres:5432/reels"
reject_known_value \
    "ERROR_API_READ_KEY" \
    "replace_with_random_read_key_at_least_32_chars"
reject_known_value \
    "ERROR_API_TRIAGE_KEY" \
    "replace_with_random_triage_key_at_least_32_chars"

validate_api_key() {
    local key_name="$1"
    local label_name="$2"
    local key="${!key_name:-}"
    local label="${!label_name:-}"
    local key_pattern='^[A-Za-z0-9._~+/-]+=*$'
    local label_pattern='^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$'
    if [ -n "$key" ] && { [ ${#key} -lt 32 ] || [ ${#key} -gt 4096 ] || [[ ! "$key" =~ $key_pattern ]]; }; then
        echo "::error::FATAL: ${key_name} is not a valid bounded Error API bearer token"
        errors=$((errors + 1))
    fi
    if [ -n "$label" ] && [[ ! "$label" =~ $label_pattern ]]; then
        echo "::error::FATAL: ${label_name} is not a valid Error API actor label"
        errors=$((errors + 1))
    fi
    if { [ -n "$key" ] && [ -z "$label" ]; } || { [ -z "$key" ] && [ -n "$label" ]; }; then
        echo "::error::FATAL: ${key_name} and ${label_name} must be configured together"
        errors=$((errors + 1))
    fi
}

validate_api_key "ERROR_API_READ_KEY" "ERROR_API_READ_LABEL"
validate_api_key "ERROR_API_TRIAGE_KEY" "ERROR_API_TRIAGE_LABEL"
validate_api_key "ERROR_API_READ_KEY_NEXT" "ERROR_API_READ_LABEL_NEXT"
validate_api_key "ERROR_API_TRIAGE_KEY_NEXT" "ERROR_API_TRIAGE_LABEL_NEXT"

key_names=(
    ERROR_API_READ_KEY ERROR_API_READ_KEY_NEXT
    ERROR_API_TRIAGE_KEY ERROR_API_TRIAGE_KEY_NEXT
)
for ((i = 0; i < ${#key_names[@]}; i++)); do
    for ((j = i + 1; j < ${#key_names[@]}; j++)); do
        left="${!key_names[i]:-}"
        right="${!key_names[j]:-}"
        if [ -n "$left" ] && [ "$left" = "$right" ]; then
            echo "::error::FATAL: Error API credentials must be distinct"
            errors=$((errors + 1))
        fi
    done
done
password_names=(
    POSTGRES_PASSWORD DB_MIGRATION_PASSWORD
    DB_APP_PASSWORD DB_ERROR_API_PASSWORD
)
for ((i = 0; i < ${#password_names[@]}; i++)); do
    for ((j = i + 1; j < ${#password_names[@]}; j++)); do
        left="${!password_names[i]:-}"
        right="${!password_names[j]:-}"
        if [ -n "$left" ] && [ "$left" = "$right" ]; then
            echo "::error::FATAL: PostgreSQL security-boundary passwords must be distinct"
            errors=$((errors + 1))
        fi
    done
done

roles=("${POSTGRES_USER:-}" "${DB_MIGRATION_USER:-}" "${DB_APP_USER:-}" "${DB_ERROR_API_USER:-}")
for ((i = 0; i < ${#roles[@]}; i++)); do
    for ((j = i + 1; j < ${#roles[@]}; j++)); do
        if [ -n "${roles[i]}" ] && [ "${roles[i]}" = "${roles[j]}" ]; then
            echo "::error::FATAL: PostgreSQL security-boundary role names must be distinct"
            errors=$((errors + 1))
        fi
    done
done

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
validate_url "ERROR_API_DATABASE_URL" "${DB_ERROR_API_USER:-}" "${DB_ERROR_API_PASSWORD:-}"

if [ $errors -gt 0 ]; then
    echo "::error::Preflight FAILED with ${errors} error(s)"
    exit 1
fi

echo "Preflight PASSED — required runtime values and database URLs are valid."
