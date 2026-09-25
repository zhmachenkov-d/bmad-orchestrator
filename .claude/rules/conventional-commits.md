# Conventional Commits

Use [Conventional Commits](https://www.conventionalcommits.org/) for every git commit subject and PR title.

## Format

```text
<type>[optional scope]: <description>
```

Allowed types: `feat`, `fix`, `docs`, `style`, `refactor`, `perf`, `test`, `build`, `ci`, `chore`, `revert`

## Rules

- Description: imperative mood, lowercase start, no trailing period
- Keep the subject ~72 characters or less
- Scope in parentheses when useful (e.g. `feat(auth): ...`)
- Breaking changes: `!` after type/scope and/or a `BREAKING CHANGE:` footer
- Optional body after a blank line for why/context
- PR titles use the same format as the commit subject

## Examples

```text
# ✅ GOOD
feat(lessons): add spaced repetition queue
fix: correct TTS timeout handling
feat(api)!: remove legacy lesson schema

# ❌ BAD
Added new feature
Update stuff.
FEAT: New API
```
