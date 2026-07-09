#!/usr/bin/env bash
# Build & push Docker image, then deploy the current branch to the Pi.
set -euo pipefail

BRANCH=$(git rev-parse --abbrev-ref HEAD)
echo "=== Deploying branch: ${BRANCH} ==="

# Step 1: Build and push Docker image to GHCR
echo ">>> Building and pushing Docker image to GHCR..."
gh workflow run "Docker" --ref "${BRANCH}" --field tags=latest

# Wait a moment for the run to appear, then watch it to completion
sleep 5
RUN_ID=$(gh run list --workflow Docker --branch "${BRANCH}" --limit 1 --json databaseId --jq '.[0].databaseId')
if [ -z "${RUN_ID}" ] || [ "${RUN_ID}" = "null" ]; then
  echo "ERROR: Could not find triggered Docker workflow run."
  exit 1
fi
echo "Watching Docker workflow run ${RUN_ID}..."
gh run watch "${RUN_ID}" --exit-status

echo ">>> Docker image pushed successfully."

# Step 2: Deploy (pull and restart containers on the Pi)
echo ">>> Triggering Deploy Dev on the Pi..."
gh workflow run "Deploy Dev" --ref "${BRANCH}"
echo "Triggered deploy-dev workflow for ${BRANCH}"
