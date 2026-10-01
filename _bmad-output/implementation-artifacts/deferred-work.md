- source_spec: `_bmad-output/implementation-artifacts/spec-orch-bmad-build-override.md`
  summary: Make stock bmad-build work in polyrepo code repos — orch-gate exempts `implementation_artifacts/**` from scope only in the coordination repo (gate.py:163,273), so a code-repo PR carrying the stock bmad-build spec fails `out-of-scope`; a code repo without BMad installed never loads the orch bmad-build override.
  evidence: Found by spec review of the bmad-build override; fixing it needs a gate change, which is outside that spec's single goal.

- source_spec: `_bmad-output/implementation-artifacts/spec-orch-planning-overrides.md`
  summary: Test `plan-check --working-tree` with a separate coordination repo (`--coord` / `ORCH_COORD` pointing away from `--repo`) to prove the snapshot is taken of the coordination repo, not the acting repo.
  evidence: Every working-tree test uses a single repo where `coord_root == repo`, so snapshotting `Env.repo` instead would pass; the shipped planning templates never pass `--coord`, so only direct CLI use is exposed.

- source_spec: `_bmad-output/implementation-artifacts/spec-orch-gate-ci-templates.md`
  summary: Polyrepo support for the orch-gate CI templates — an optional `registry-auth` block (secret `ORCH_READ_TOKEN` via a git credential helper, never in a URL; `insteadOf` rewrites of `git@host:` and `ssh://git@host/` to HTTPS; fail early naming the secret and its usual causes — unset, protected-only, fork or Dependabot PR) kept whenever the registry has a repo other than `.`, including the coordination repo and the acting repo itself, plus an optional `coord-clone` block (full clone of the coordination repo into `.orch-coord`, `ORCH_COORD=.orch-coord`) kept only in a polyrepo code repo, with tests for every keep/strip combination.
  evidence: Split at the spec token gate; spec review found the gate fetches every non-`.` registry repo by URL into a bare cache (markers.py:103-110, orch.py never fills `local_repos`), so auth is needed in the coordination repo too and fetch errors can leak a URL-embedded token into `orch-gate.json`.

- source_spec: `_bmad-output/implementation-artifacts/spec-orch-setup-protection-check.md`
  summary: `setup.py protection` also verifies that the CODEOWNERS file the platform reads (first of `CODEOWNERS_FILES[platform]` at the remote main) owns every pattern of the rendered CODEOWNERS template — last matching line has an owner, gitignore-subset matching, GitLab sections with default owners and optional `^[Section]` not counting, unsupported syntax → `unknown` — and optionally GitHub owner validity via `codeowners/errors`.
  evidence: Split at the spec token gate (about 1850 tokens); the CODEOWNERS matcher is the largest, independently shippable part, and until then ownership stays a checklist item.

- source_spec: `_bmad-output/implementation-artifacts/spec-orch-setup-protection-check.md`
  summary: `setup.py protection` warns (`bypass-possible`) when the rules can be bypassed — GitHub ruleset bypass actors or classic `enforce_admins: false`, GitLab push access to the main branch other than "No one" — naming who can bypass; bypass lists readable only by admins are `unknown`.
  evidence: Party review of the protection spec: a direct push to main skips the gate while all three checked settings read `ok`; deferred by user.

- source_spec: `_bmad-output/implementation-artifacts/spec-orch-setup-protection-check.md`
  summary: GitLab half of `setup.py protection` (`--platform gitlab`, `glab api --include --hostname`), on the same origin parsing, response parsing and ok/missing/unknown rules — sources `GET projects/{enc path}` and `GET projects/{enc path}/protected_branches?per_page=100` (exactly 100 → unreadable), protected-branch rules being those whose `name` matches the branch by `fnmatch` (none = not protected); `required-check` = `only_allow_merge_if_pipeline_succeeds` true and `allow_merge_on_skipped_pipeline` false (a `[skip ci]` pipeline would merge past the gate, decision by user); `code-owner-review` = `code_owner_approval_required` true on any matching rule, absent on all → `unknown`; `merge-onto-gated-main` = `merge_method` `ff` or `rebase_merge`; warning `merge-trains` when `merge_trains_enabled` and `merge_pipelines_enabled`; a 404 on the project is unreadable; SKILL step 7 runs it when the GitLab CI file is installed.
  evidence: Split at the spec token gate after review (about 2100 tokens); needed by the user, so the next item after the GitHub half. glab's `--include` output on 4xx is not yet verified against a live GitLab.
