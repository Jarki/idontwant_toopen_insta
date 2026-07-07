# Git Flow

This project uses a lightweight feature-branch flow with `main` as the deployable branch.

## Branch model

- `main` is the stable branch.
- Create short-lived feature branches from `main`.
- Open pull requests back into `main` for review and quality checks.
- Avoid committing directly to `main` unless the change is small and intentional.

Example:

```bash
git switch main
git pull --ff-only
git switch -c feat/short-description
```

## Quality gates

Quality gates are defined in `.github/workflows/quality.yml`.

The workflow runs on:

- Pull requests targeting `main`.
- Pushes to `main`.

The gate runs:

```bash
uv sync --locked --all-groups
uv run poe check
```

`uv run poe check` currently includes formatting checks, Ruff linting, mypy, and pytest.

## Merging

Before merging a feature branch:

1. Run the relevant focused checks locally while iterating.
2. Run `uv run poe check` before final handoff for code changes.
3. Open a pull request into `main`.
4. Wait for the GitHub Actions quality gate to pass.
5. Merge into `main`.

After merge, the same quality gate runs on `main` to verify the final branch state.

## Release tags

Release tags are created only when there is something worth promoting as a deployable release.

Suggested tag format:

```text
vMAJOR.MINOR.PATCH
```

Example:

```bash
git switch main
git pull --ff-only
git tag v0.1.0
git push origin v0.1.0
```

For now, tags do not trigger deployment automation in this repository.

## Deployment model

Near-term expectation:

- Commits merged to `main` represent the current development-ready state.
- Release tags identify versions that may be deployed manually.
- The Pi remains responsible for running the bot with local `.env`, `assets/`, `data/`, and `output/` state.

Future deployment options include:

- Build and publish Docker images to GitHub Container Registry from GitHub-hosted runners.
- Pull tagged images on the Pi and restart Docker Compose manually or with a small script.
- Add a self-hosted GitHub Actions runner on the Pi only if direct GitHub Actions execution on the device is needed.

Do not add deployment automation, GHCR publishing, or a Pi self-hosted runner without an explicit user decision.

## Commit messages

Use concise Conventional Commit-style messages:

```text
<type>: <imperative summary>
```

Common types: `feat`, `fix`, `docs`, `test`, `refactor`, `build`, `chore`.
