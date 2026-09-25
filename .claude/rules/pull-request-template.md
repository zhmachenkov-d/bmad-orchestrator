# Pull Request Template

When creating a pull request with `gh pr create`, always use `.github/pull_request_template.md` as the body.

## Required workflow

1. Read `.github/pull_request_template.md`
2. Fill every section with concrete content for this change (do not leave placeholders or HTML comments)
3. For Related issue: use a real issue link or `Fixes #N` / `Closes #N` when applicable; write `N/A` if none
4. Check only the checklist items that apply and are done
5. Pass the completed body via HEREDOC to `gh pr create --body`
6. PR title must follow Conventional Commits (same format as commit subjects)

## Example

```bash
gh pr create --title "feat(auth): add session refresh" --body "$(cat <<'EOF'
## Description

Add refresh-token flow so sessions survive access-token expiry, and surface re-auth errors in the API client.

## Related issue

Fixes #42

## How has this been tested?

- Log in, wait for access token expiry, confirm silent refresh
- Revoke refresh token and confirm user is sent to login
- Ran auth client unit tests

## Checklist

- [x] I have performed a self-review of my code
- [x] I have added tests that prove my fix is effective or that my feature works
- [x] New and existing tests pass locally with my changes
- [ ] I have made corresponding changes to the documentation (if appropriate)
- [x] Any dependent changes have been merged and published

EOF
)"
```

## Do not

- Invent a different PR body structure
- Submit empty Description or testing sections
- Skip the template when the user asks to open a PR
