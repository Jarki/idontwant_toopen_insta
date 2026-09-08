# syntax=docker/dockerfile:1.7
FROM python:3.14-slim-trixie
COPY --from=ghcr.io/astral-sh/uv:0.9.18 /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1
ENV UV_NO_DEV=1
ENV UV_LINK_MODE=copy

WORKDIR /app

# Reddit hosts video and audio as separate streams; yt-dlp uses ffmpeg to merge them.
RUN apt-get update \
    && apt-get install --no-install-recommends -y ffmpeg \
    && rm -rf /var/lib/apt/lists/*

RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --locked --no-install-project

COPY . /app/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked

ENTRYPOINT [ "/bin/bash", "docker/entrypoint.sh" ]
