# CLAUDE.md

## Rules

Project rules live in [.claude/rules/](.claude/rules/) and are loaded automatically in every session:

- [Do Not Edit Vendor BMAD Files](.claude/rules/bmad-do-not-edit-vendor.md) — never edit installer-owned BMAD files; customize only via `_bmad/custom/`
- [Conventional Commits](.claude/rules/conventional-commits.md) — format commit messages and PR titles with Conventional Commits
- [No Direct Commits to Main](.claude/rules/no-commit-to-main.md) — never commit to `main`; use a branch and open a PR
- [Pull Request Template](.claude/rules/pull-request-template.md) — fill `.github/pull_request_template.md` when creating PRs

Mirrored Cursor rules live in [.cursor/rules/](.cursor/rules/); keep both copies in sync when editing a rule.
