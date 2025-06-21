# !/bin/bash

set -e

source .env

if [ -z "${OUTPUT_DIR}" ]; then
    echo "OUTPUT_DIR is not set"
    exit 1
fi

if [ -z "${MAX_FILES}" ]; then
  MAX_FILES=100
fi

if [ $(find ${OUTPUT_DIR} -type f | wc -l) -gt ${MAX_FILES} ]; then
  echo "Removing files from ${OUTPUT_DIR} until there are only ${MAX_FILES} files"
  find ${OUTPUT_DIR} -type f -printf "%T@ %p\n" |\
    sort -n |\
    head -n -"${MAX_FILES}" |\
    cut -d' ' -f2- |\
    xargs -r rm -- 
fi