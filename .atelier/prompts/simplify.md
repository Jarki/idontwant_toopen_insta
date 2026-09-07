---
description: Simplify the current changes
quick-launch: true
---
Review the current branch changes and simplify the implementation while preserving behavior.

Focus on:
- Removing unnecessary complexity and duplication.
- Removing dead, unreachable, or obsolete code.
- Improving naming, readability, and maintainability.
- Reusing existing project patterns.
- Avoiding unrelated changes or new dependencies.
- Keeping tests clear and focused.

Update tests when needed, then run `uv run poe verify` and summarize the changes and validation results.
