# AGENTS.md

## Project summary

`ig-reel-downloader` is a small Python Telegram bot that downloads Instagram Reels and sends the video back to the Telegram chat.

Core technologies: `python-telegram-bot`, `yt-dlp`, SQLite, Alembic, Docker Compose, `uv`, Poe, Ruff, mypy, and pytest.

For deeper system details, read `ARCH.md`. For detailed agent workflow guidance, see `docs/agent-workflow.md`.

## Core workflow

For non-trivial changes:

1. Read `README.md`, `ARCH.md`, `pyproject.toml`, and relevant source/tests/docs.
2. Restate the intended change and likely files before editing.
3. Make the smallest change that satisfies the request.
4. Add or update tests for behavior changes.
5. Run focused checks while iterating.
6. Run `uv run poe check` before declaring code changes complete, unless the user explicitly asks to skip it.
7. Final response should include changed files, validation commands/results, and remaining risks or follow-ups.

For documentation-only edits, focused validation may be enough. State when code checks were not run because no runtime code changed.

## Validation commands

Use Poe tasks through `uv`:

```bash
uv run poe check         # full validation
uv run poe format-check  # formatting check
uv run poe lint          # Ruff lint
uv run poe typecheck     # mypy
uv run poe test          # pytest
uv run poe bot           # run the bot locally
```

## Critical constraints

Preserve these unless the user explicitly approves a change:

- The app is a single-process Telegram long-polling bot.
- Blocking `yt-dlp` and SQLite work is offloaded from async handlers with `asyncio.to_thread()`.
- SQLite is the local cache; there is no external database service.
- Schema migrations run separately from bot startup through the Docker Compose `migrate` service.
- Cached rows are reusable only while fresh and while the referenced media file still exists.
- Cleanup removes old media files from `output/`; it does not prune SQLite rows.
- Optional cookies live at `assets/cookies.txt` and are used only if the file exists.
- Large Telegram uploads may time out; timeout behavior should stay user-friendly.
- URL parsing is intentionally narrow unless broadening support is part of the task.
- Avoid adding queues, web servers, schedulers, ORMs, or new infrastructure unless requested.

## Project task memory

Use the session todo list for active in-session progress. Use project memory only for durable decisions, accepted future work, deferred task boundaries, and completed-task supersession notes. Do not store secrets, local runtime state, or speculative ideas the user has not accepted.

## Decision boundaries

Agents may decide independently on small supporting refactors, tests for changed behavior, minor wording improvements, and safe formatting/lint fixes.

Ask the user before adding dependencies, changing deployment architecture or required environment variables, introducing new infrastructure, broadening beyond Instagram Reel downloading, changing cache/cleanup/file-retention semantics, or requiring new secrets/external accounts.

## Commit messages

Use concise Conventional Commit-style messages:

```text
<type>: <imperative summary>
```

Common types: `feat`, `fix`, `docs`, `test`, `refactor`, `build`, `chore`.

## See also

- `README.md` — setup and runtime instructions.
- `ARCH.md` — architecture and deployment details.
- `docs/agent-workflow.md` — detailed agent workflow, repo map, testing, generated files, and orchestration guidance.
- `docs/git-flow.md` — branch, quality-gate, release-tag, and deployment model.
- `docs/database.md` — SQLite/Alembic database tasks through `uv run poe`.
- `pyproject.toml` — dependencies and Poe task definitions.
