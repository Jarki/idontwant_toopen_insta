# AGENTS.md

## Project summary

`ig-reel-downloader` is a small Python Telegram bot that downloads Instagram Reels and sends the video back to the Telegram chat.

Core technologies: `python-telegram-bot`, `yt-dlp`, SQLite, Alembic, Docker Compose, `uv`, Poe, Ruff, mypy, and pytest.

For deeper system details, read `ARCH.md`. For detailed agent workflow guidance, see `docs/agent-workflow.md`.

## Important

Before making any commit, always read `docs/git-flow.md` first.

## Core workflow

For non-trivial changes:

1. **Create a feature branch first** — never commit code changes directly to `main`. Read `docs/git-flow.md` for branch naming and flow. Stash or discard unrelated dirty working-tree entries before branching.
2. Read `README.md`, `ARCH.md`, `pyproject.toml`, and relevant source/tests/docs.
3. Restate the intended change and likely files before editing.
4. Make the smallest change that satisfies the request.
5. Add or update tests for behavior changes.
6. Run focused tests while iterating (`uv run pytest <test_file> -v`).
7. Run `uv run poe verify` as the single pre-handoff gate — it auto-formats, lint-fixes, typechecks, and tests. **Do not** run `check`, `typecheck`, `format`, or `lint` individually — `verify` covers all of them in one pass. `check` is for CI only (read-only, no auto-fixes).
8. Final response should include changed files, validation commands/results, and remaining risks or follow-ups.

For documentation-only edits, focused validation may be enough. State when code checks were not run because no runtime code changed.

Do not commit generated specs or plans, including files under `docs/superpowers/specs/` and `docs/superpowers/plans/`, unless the user explicitly asks for those artifacts to be committed.

## Validation commands

Run `uv run poe verify` as the single pre-handoff gate (auto-format, lint-fix, typecheck, test).
Do not run individual commands (`typecheck`, `format`, `lint`) separately, unless you specifically need it.

Available Poe tasks:

```bash
uv run poe verify        # (agents) auto-format + lint-fix + typecheck + test
uv run poe check         # (CI only) read-only format-check + lint + typecheck + test
uv run poe test          # run tests only
uv run poe bot           # run the bot locally
uv run poe deploy        # triggers Pi dev deployment; agents must not run unless explicitly asked
```

`uv run poe deploy` triggers the `deploy-dev.yml` workflow on the Pi self-hosted runner. Agents must not run it as validation, during normal handoff, or proactively; use it only when the user explicitly asks for a deployment.

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
- **Agents are only allowed to deploy to dev.** Never trigger a prod deployment (`deploy-prod`, `Deploy Prod`, or the `prod` image tag). Prod deployments are done manually by the user from `main`.

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
