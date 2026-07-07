# AGENTS.md

## Project summary

`ig-reel-downloader` is a small Python Telegram bot that downloads Instagram Reels and sends the video back to the Telegram chat.

Core technologies:

- `python-telegram-bot` for Telegram long polling and message handling.
- `yt-dlp` for Instagram metadata extraction and media downloads.
- SQLite for a local cache of downloaded Reel metadata and file paths.
- Docker Compose for local/production-ish deployment.
- `uv`, Poe, Ruff, mypy, and pytest for development and validation.

For deeper system details, read `ARCH.md`. Do not duplicate the full architecture here; this file is the agent operating manual.

## Repository map

Important paths:

- `README.md` — user setup and runtime instructions.
- `ARCH.md` — architecture, message flow, persistence model, deployment notes, and extension points.
- `pyproject.toml` — dependencies and Poe task definitions.
- `ig_reel_downloader/__main__.py` — runtime entry point and environment wiring.
- `ig_reel_downloader/app.py` — Telegram application and message flow.
- `ig_reel_downloader/utils.py` — URL parsing, `yt-dlp` integration, error classification, row factory.
- `ig_reel_downloader/constants.py` — shared constants.
- `ig_reel_downloader/repository/` — repository protocol, Pydantic models, SQLite implementation.
- `tests/` — pytest suite.
- `Dockerfile`, `docker-compose.yaml`, `docker_entrypoint.sh`, `clean.sh` — container deployment and cleanup loop.
- `.env.example` — documented runtime configuration.

Runtime/local state paths:

- `.env` — local secrets/config; never commit.
- `assets/cookies.txt` — optional Instagram cookies; never commit.
- `data/reels.db` — local SQLite cache; never commit.
- `output/` — downloaded media files; never commit.

## Validation commands

Use Poe tasks through `uv`.

Full validation before final handoff:

```bash
uv run poe check
```

Focused checks:

```bash
uv run poe format-check
uv run poe lint
uv run poe typecheck
uv run poe test
```

Auto-fix formatting/linting when appropriate:

```bash
uv run poe format
uv run poe lint-fix
```

Run the bot locally:

```bash
uv run poe bot
```

## Agent workflow

Default workflow for non-trivial changes:

1. Read `README.md`, `ARCH.md`, `pyproject.toml`, and the relevant source/tests.
2. Restate the intended change and identify likely files before editing.
3. Keep one writer active in the worktree. Use other agents for read-only context, review, or validation unless worktrees are intentionally used.
4. Make the smallest change that satisfies the request.
5. Add or update tests for behavior changes.
6. Run focused checks while iterating.
7. Run `uv run poe check` before declaring the work complete, unless the user explicitly asks to skip it.
8. Final response should include changed files, validation commands/results, and remaining risks or follow-ups.

For very small documentation-only edits, focused validation may be enough. State when code checks were not run because no runtime code changed.

## Agent orchestration

There is no dedicated built-in `refactoring` agent. Use the existing agents by role:

- `scout` or `context-builder` — map relevant files, dependencies, risks, and likely edit points before larger work.
- `planner` — turn gathered context into a concrete implementation plan when the change has multiple steps.
- `worker` — make approved code changes. Keep this as the single writer in the active worktree by default.
- `reviewer` — inspect plans, diffs, tests, and refactoring opportunities from a fresh perspective.
- `oracle` — challenge direction, architecture, scope, or tradeoffs when the right path is unclear.
- `researcher` — investigate external docs, library behavior, or ecosystem choices when local code is not enough.

Preferred orchestration patterns:

- Small change: parent agent edits directly, then runs focused checks.
- Medium feature/fix: `scout/context-builder` → parent synthesis → `worker` → `reviewer` → validation.
- Refactor: `reviewer` identifies focused opportunities → parent approves scope → `worker` applies changes → `reviewer` validates diff → `uv run poe check`.
- Architecture decision: `context-builder` gathers local constraints → `oracle` challenges assumptions → parent asks user for any product/scope decision → `worker` implements only after approval.
- External/library uncertainty: `researcher` checks upstream docs/issues → `context-builder` maps local impact → parent decides next step.

Default to parallelizing read-only work, review, and research. Do not run multiple writers against the same worktree unless isolated git worktrees are intentionally used.

## Architecture constraints

Read `ARCH.md` before changing message flow, persistence, download behavior, deployment, or cleanup behavior.

Important constraints to preserve unless explicitly approved otherwise:

- The app is a single-process Telegram long-polling bot.
- Blocking `yt-dlp` and SQLite work is offloaded from async handlers with `asyncio.to_thread()`.
- SQLite is the local cache; there is no external database service.
- Cached rows are reusable only while fresh and while the referenced media file still exists.
- Cleanup removes old media files from `output/`; it does not prune SQLite rows.
- Optional cookies live at `assets/cookies.txt` and are used only if the file exists.
- Large Telegram uploads may time out; timeout behavior should stay user-friendly.
- URL parsing is intentionally narrow unless broadening support is part of the task.
- Avoid adding queues, web servers, schedulers, ORMs, or new infrastructure unless the user explicitly asks for that scope.

## Coding style

- Follow existing patterns before introducing new abstractions.
- Keep functions focused and easy to test.
- Prefer explicit, typed Python that passes strict mypy.
- Do not add dependencies unless they materially simplify the requested work.
- Keep user-facing Telegram messages clear and concise.
- Preserve existing Docker/Compose behavior unless deployment changes are requested.

## Testing expectations

- Add pytest coverage for new parsing, error handling, cache, or message-flow behavior when practical.
- For `utils.py` behavior, prefer small unit tests in `tests/test_utils.py` or a new focused test file.
- For `IgReelDownloaderApp` behavior, prefer dependency injection/mocking rather than real Telegram or network calls.
- Do not rely on live Instagram, Telegram, or network access in automated tests.
- If a test cannot be added reasonably, explain why and run the closest focused validation.

## Decision boundaries

Agents may decide independently:

- Small refactors that directly support the requested change.
- Test additions for changed behavior.
- Minor wording improvements in docs or user-facing messages.
- Running format/lint auto-fixes that do not alter behavior.

Ask the user before:

- Adding new runtime dependencies.
- Changing deployment architecture or required environment variables.
- Introducing new storage systems, queues, schedulers, web servers, or background services.
- Broadening the bot beyond Instagram Reel downloading.
- Changing cache semantics, cleanup policy, or file retention behavior.
- Making changes that require new secrets or external accounts.

## Commit messages

Use concise Conventional Commit-style messages:

```text
<type>: <imperative summary>
```

Common types:

- `feat` — user-visible feature.
- `fix` — bug fix.
- `docs` — documentation-only change.
- `test` — test-only change.
- `refactor` — behavior-preserving code restructure.
- `build` — packaging, dependency, Docker, or tooling change.
- `chore` — maintenance with no runtime or docs impact.

Guidelines:

- Use lowercase type names.
- Use an imperative summary: `add`, `fix`, `update`, not `added`, `fixed`, `updates`.
- Keep the first line short and specific.
- Prefer `docs: add AGENTS.md` for this file.

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

Do not commit local brainstorming/spec files unless the user explicitly asks for that documentation to be versioned.
