# Agent Workflow Reference

This file contains detailed agent guidance. Keep `AGENTS.md` short and focused on the rules agents need immediately.

## Repository map

Important paths:

- `README.md` — user setup and runtime instructions.
- `ARCH.md` — architecture, message flow, persistence model, deployment notes, and extension points.
- `pyproject.toml` — dependencies and Poe task definitions.
- `ig_reel_downloader/__main__.py` — runtime entry point and environment wiring.
- `ig_reel_downloader/app.py` — Telegram application and message flow.
- `ig_reel_downloader/utils.py` — URL parsing, `yt-dlp` integration, and error classification.
- `ig_reel_downloader/constants.py` — shared constants.
- `ig_reel_downloader/repository/` — repository protocol, Pydantic models, and SQLite implementation.
- `migrations/` — Alembic migration environment and revisions.
- `tests/` — pytest suite.
- `.github/workflows/quality.yml` — GitHub Actions quality gate.
- `Dockerfile`, `docker-compose.yaml`, `docker_entrypoint.sh`, `clean.sh` — container deployment, one-shot migration service, and cleanup loop.
- `.env.example` — documented runtime configuration.

Runtime/local state paths:

- `.env` — local secrets/config; never commit.
- `assets/cookies.txt` — optional Instagram cookies; never commit.
- `data/reels.db` — local SQLite cache; never commit.
- `output/` — downloaded media files; never commit.

## Detailed workflow

Default workflow for non-trivial changes:

1. Read `README.md`, `ARCH.md`, `pyproject.toml`, and relevant source/tests/docs.
2. Identify likely files before editing.
3. Make the smallest change that satisfies the request.
4. Add or update tests for behavior changes.
5. Run focused checks while iterating.
6. Run `uv run poe verify` before declaring code changes complete, unless explicitly skipped.
7. Final response should include changed files, validation commands/results, and remaining risks or follow-ups.

For documentation-only edits, focused validation may be enough. State when code checks were not run because no runtime code changed.

## Agent orchestration

There is no dedicated built-in `refactoring` agent. Use existing agents by role:

- `scout` or `context-builder` — map relevant files, dependencies, risks, and likely edit points.
- `planner` — turn gathered context into a concrete implementation plan.
- `worker` — make approved code changes. Keep this as the single writer in the active worktree by default.
- `reviewer` — inspect plans, diffs, tests, and refactoring opportunities from a fresh perspective.
- `oracle` — challenge direction, architecture, scope, or tradeoffs when the right path is unclear.
- `researcher` — investigate external docs, library behavior, or ecosystem choices when local code is not enough.

Preferred orchestration patterns:

- Small change: parent agent edits directly, then runs focused checks.
- Medium feature/fix: `scout/context-builder` → parent synthesis → `worker` → `reviewer` → validation.
- Refactor: `reviewer` identifies focused opportunities → parent approves scope → `worker` applies changes → `reviewer` validates diff → `uv run poe verify`.
- Architecture decision: `context-builder` gathers local constraints → `oracle` challenges assumptions → parent asks user for scope/product decisions → `worker` implements after approval.
- External/library uncertainty: `researcher` checks upstream docs/issues → `context-builder` maps local impact → parent decides next step.

Default to parallelizing context gathering, review, and research when it helps.

## Coding style

- Follow existing patterns before introducing new abstractions.
- Keep functions focused and easy to test.
- Prefer explicit, typed Python that passes strict mypy.
- Do not add dependencies unless they materially simplify the requested work.
- Keep user-facing Telegram messages clear and concise.
- Preserve existing Docker/Compose behavior unless deployment changes are requested.
- Keep schema migrations separate from bot startup; Docker Compose should run them through the one-shot `migrate` service.

## Testing expectations

- Add pytest coverage for new parsing, error handling, cache, or message-flow behavior when practical.
- For `utils.py` behavior, prefer small unit tests in `tests/unit/test_utils.py` or a new focused test file.
- For `IgReelDownloaderApp` behavior, prefer dependency injection/mocking rather than real Telegram or network calls.
- Do not rely on live Instagram, Telegram, or network access in automated tests.
- If a test cannot be added reasonably, explain why and run the closest focused validation.

## Git and generated files

Do not commit local runtime state, secrets, media, caches, or generated coverage output.

Common ignored local artifacts include:

- `.env`
- `assets/cookies.txt`
- `data/*.db`
- `output/`
- `htmlcov/`
- `.coverage*`
- `.pytest_cache/`
- `.ruff_cache/`
- `.mypy_cache/`
- `.venv/`
- `docs/superpowers/specs/`
- `docs/superpowers/plans/`

Do not commit local brainstorming/spec files unless the user explicitly asks for that documentation to be versioned.
