#!/bin/bash

set -e

if [ -z "${OUTPUT_DIR:-}" ]; then
    echo "OUTPUT_DIR is not set"
    exit 1
fi

MAX_FILES="${MAX_FILES:-100}"

if [ "$(find "${OUTPUT_DIR}" -type f | wc -l)" -gt "${MAX_FILES}" ]; then
  echo "Removing files from ${OUTPUT_DIR} until there are only ${MAX_FILES} files"
  find "${OUTPUT_DIR}" -type f -printf "%T@ %p\n" |\
    sort -n |\
    head -n -"${MAX_FILES}" |\
    cut -d' ' -f2- |\
    xargs -r rm -- 
fi