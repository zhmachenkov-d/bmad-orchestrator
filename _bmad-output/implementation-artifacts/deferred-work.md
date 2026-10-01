- source_spec: `_bmad-output/implementation-artifacts/spec-orch-bmad-build-override.md`
  summary: Make stock bmad-build work in polyrepo code repos — orch-gate exempts `implementation_artifacts/**` from scope only in the coordination repo (gate.py:163,273), so a code-repo PR carrying the stock bmad-build spec fails `out-of-scope`; a code repo without BMad installed never loads the orch bmad-build override.
  evidence: Found by spec review of the bmad-build override; fixing it needs a gate change, which is outside that spec's single goal.

- source_spec: `_bmad-output/implementation-artifacts/spec-orch-gate-ci-templates.md`
  summary: Polyrepo support for the orch-gate CI templates — an optional `registry-auth` block (secret `ORCH_READ_TOKEN` via a git credential helper, never in a URL; `insteadOf` rewrites of `git@host:` and `ssh://git@host/` to HTTPS; fail early naming the secret and its usual causes — unset, protected-only, fork or Dependabot PR) kept whenever the registry has a repo other than `.`, including the coordination repo and the acting repo itself, plus an optional `coord-clone` block (full clone of the coordination repo into `.orch-coord`, `ORCH_COORD=.orch-coord`) kept only in a polyrepo code repo, with tests for every keep/strip combination.
  evidence: Split at the spec token gate; spec review found the gate fetches every non-`.` registry repo by URL into a bare cache (markers.py:103-110, orch.py never fills `local_repos`), so auth is needed in the coordination repo too and fetch errors can leak a URL-embedded token into `orch-gate.json`.

- source_spec: `_bmad-output/implementation-artifacts/spec-orch-setup-protection-check.md`
  summary: `setup.py protection` also verifies that the CODEOWNERS file the platform reads (first of `CODEOWNERS_FILES[platform]` at the remote main) owns every pattern of the rendered CODEOWNERS template — last matching line has an owner, gitignore-subset matching, GitLab sections with default owners and optional `^[Section]` not counting, unsupported syntax → `unknown` — and optionally GitHub owner validity via `codeowners/errors`.
  evidence: Split at the spec token gate (about 1850 tokens); the CODEOWNERS matcher is the largest, independently shippable part, and until then ownership stays a checklist item.

- source_spec: `_bmad-output/implementation-artifacts/spec-orch-setup-protection-check.md`
  summary: `setup.py protection` warns (`bypass-possible`) when the rules can be bypassed — GitHub ruleset bypass actors or classic `enforce_admins: false`, GitLab push access to the main branch other than "No one" — naming who can bypass; bypass lists readable only by admins are `unknown`.
  evidence: Party review of the protection spec: a direct push to main skips the gate while all three checked settings read `ok`; deferred by user.

- source_spec: `_bmad-output/implementation-artifacts/spec-orch-setup-protection-check-gitlab.md`
  summary: `setup.py protection` on GitLab warns (`merge-trains`, not a problem) when the project has `merge_trains_enabled` and `merge_pipelines_enabled` true, because the gate judges each MR against its target branch, not against the train ahead of it.
  evidence: Split at the spec token gate (about 2000 tokens) by user; an independent warning on top of the three GitLab settings.

- source_spec: `_bmad-output/implementation-artifacts/spec-orch-setup-protection-check-gitlab.md`
  summary: `setup.py protection` takes a repeatable `--platform` (`github`, `gitlab`), evaluating each platform against the one origin into `platforms.<platform>` of one JSON result, with problem/warning messages prefixed by the platform, so a repo with both CI files gets one check and headless one `protection` result.
  evidence: Split at the spec token gate (about 2000 tokens) by user; until then SKILL.md runs `protection` once per installed CI platform.
