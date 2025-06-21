FROM python:3.12.10-slim-bookworm AS builder

RUN pip install poetry==2.1.3
ENV POETRY_NO_INTERACTION=1 \
    POETRY_VIRTUALENVS_IN_PROJECT=1 \
    POETRY_VIRTUALENVS_CREATE=1

WORKDIR /build
COPY pyproject.toml poetry.lock ./
RUN touch README.md

RUN poetry install --no-root --no-cache

FROM python:3.12.10-slim-bookworm AS runtime

WORKDIR /app
COPY --from=builder /build /app
COPY ig_reel_downloader ig_reel_downloader
COPY clean.sh docker_entrypoint.sh ./
ENTRYPOINT [ "/bin/bash", "docker_entrypoint.sh" ] 