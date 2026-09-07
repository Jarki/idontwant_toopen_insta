---
description: Land the current feature branch
quick-launch: true
hotkey: l
---
Review the changes and run `uv run poe verify`.

If validation passes:
1. Commit any remaining changes using Conventional Commits.
2. Rebase onto the latest `origin/main` when necessary.
3. Push the branch.
4. Create or update a pull request against `main`.
5. Report the PR and CI status.

Do not merge, deploy, or delete the workspace unless explicitly requested.
