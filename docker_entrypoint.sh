#!/bin/bash

cleanup_loop() {
    # This might lead to race conditions but yk
    while true; do
        echo "Cleaning up old files"
        /app/clean.sh
        sleep 1800
    done
}

cleanup_loop &
exec .venv/bin/python3 -m ig_reel_downloader