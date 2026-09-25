# No Direct Commits to Main

Never commit to `main` (or `master`). When the user asks to commit and the current branch is `main`/`master`, create a feature branch, commit there, push, and open a PR.

## Required workflow

1. If on `main`/`master` (or about to commit there): create and switch to a new branch first
2. Commit on that branch only
3. Push the branch and create a pull request into `main`
4. Return the PR URL when done

## Branch naming

Prefer a short Conventional Commits–style name, e.g. `feat/spaced-repetition` or `fix/tts-timeout`.

## Exceptions

Only commit directly to `main` if the user explicitly overrides this rule in the same request (e.g. "commit to main anyway").
