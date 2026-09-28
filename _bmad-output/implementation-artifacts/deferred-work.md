- source_spec: `_bmad-output/implementation-artifacts/spec-orch-bmad-build-override.md`
  summary: Make stock bmad-build work in polyrepo code repos — orch-gate exempts `implementation_artifacts/**` from scope only in the coordination repo (gate.py:163,273), so a code-repo PR carrying the stock bmad-build spec fails `out-of-scope`; a code repo without BMad installed never loads the orch bmad-build override.
  evidence: Found by spec review of the bmad-build override; fixing it needs a gate change, which is outside that spec's single goal.

- source_spec: `_bmad-output/implementation-artifacts/spec-orch-planning-overrides.md`
  summary: Test `plan-check --working-tree` with a separate coordination repo (`--coord` / `ORCH_COORD` pointing away from `--repo`) to prove the snapshot is taken of the coordination repo, not the acting repo.
  evidence: Every working-tree test uses a single repo where `coord_root == repo`, so snapshotting `Env.repo` instead would pass; the shipped planning templates never pass `--coord`, so only direct CLI use is exposed.

- source_spec: `_bmad-output/implementation-artifacts/spec-orch-planning-overrides.md`
  summary: Make plan validation deterministic in orch-gate — a coordination PR that changes epics or the registry fails when it introduces new `plan-check` FAIL findings compared with its base, so a broken plan never reaches main and the planning-template prompts become a convenience, not the guarantee.
  evidence: Walkthrough of PR #14: the sprint-planning override enforces the plan-check verdict only through an activation step and a persistent fact (LLM-executed); today the gate fails plan defects only in a PR's own story and warns on other stories, so a defect in a later story is found only when that story's PR runs.

- source_spec: `_bmad-output/implementation-artifacts/spec-orch-gate-ci-templates.md`
  summary: Polyrepo support for the orch-gate CI templates — an optional `registry-auth` block (secret `ORCH_READ_TOKEN` via a git credential helper, never in a URL; `insteadOf` rewrites of `git@host:` and `ssh://git@host/` to HTTPS; fail early naming the secret and its usual causes — unset, protected-only, fork or Dependabot PR) kept whenever the registry has a repo other than `.`, including the coordination repo and the acting repo itself, plus an optional `coord-clone` block (full clone of the coordination repo into `.orch-coord`, `ORCH_COORD=.orch-coord`) kept only in a polyrepo code repo, with tests for every keep/strip combination.
  evidence: Split at the spec token gate; spec review found the gate fetches every non-`.` registry repo by URL into a bare cache (markers.py:103-110, orch.py never fills `local_repos`), so auth is needed in the coordination repo too and fetch errors can leak a URL-embedded token into `orch-gate.json`.

- source_spec: `_bmad-output/implementation-artifacts/spec-orch-gate-ci-templates.md`
  summary: orch-setup must verify, at install and on every re-run, that the protection the CI templates rely on is actually on — the `orch-gate` check is required on the main branch (and in the merge queue on GitHub), required code-owner review is on, and the CODEOWNERS file the platform reads owns the CI files and orch paths — via the platform API (`gh api` rulesets/branch protection, GitLab protected branches and approval rules), and warn naming each missing setting; when the API is not reachable with the user's token, print the checklist instead.
  evidence: Walkthrough of PR #15: the base-CLI guarantee holds only when the job is required and the CI files are code-owned, but the templates only state this in their headers and the job cannot see repository settings, so a missed setting silently lets a PR run its own CLI.
