#!/usr/bin/env bash
# Deploy the current branch via GitHub Actions.
set -euo pipefail

BRANCH=$(git rev-parse --abbrev-ref HEAD)
echo "Deploying branch: ${BRANCH}"

gh workflow run "Deploy Dev" --ref "${BRANCH}"
echo "Triggered deploy-dev workflow for ${BRANCH}"
