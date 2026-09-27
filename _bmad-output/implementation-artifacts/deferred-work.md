- source_spec: none
  summary: Planning override templates for orch — `_bmad/custom/bmad-create-epics-and-stories.toml` (registry list and story rules via persistent_facts) and `_bmad/custom/bmad-sprint-planning.toml` (registry plan validation in the readiness check via `orch.py plan-check`).
  evidence: Split from Build Roadmap step 4 of skills/reports/orch-module-plan.md; independently shippable from the bmad-build override, which was built first because it links orch-next and orch-gate.

- source_spec: none
  summary: orch-gate CI templates for GitHub Actions and GitLab CI (`--ci`, `-o orch-gate.json` uploaded as a job artifact, `--format markdown` step summary) plus suggested CODEOWNERS entries, stored as orch-setup assets.
  evidence: Split from Build Roadmap step 4 of skills/reports/orch-module-plan.md; independently shippable from the stock-skill override templates.

- source_spec: `_bmad-output/implementation-artifacts/spec-orch-bmad-build-override.md`
  summary: Make stock bmad-build work in polyrepo code repos — orch-gate exempts `implementation_artifacts/**` from scope only in the coordination repo (gate.py:163,273), so a code-repo PR carrying the stock bmad-build spec fails `out-of-scope`; a code repo without BMad installed never loads the orch bmad-build override.
  evidence: Found by spec review of the bmad-build override; fixing it needs a gate change, which is outside that spec's single goal.

- source_spec: `_bmad-output/implementation-artifacts/spec-orch-bmad-build-override.md`
  summary: orch-setup must merge the bmad-build override safely — combine an existing team `on_complete`, define ordering of appended `activation_steps_prepend` entries, and warn when a `bmad-build.user.toml` `on_complete` would drop the orch marker and gate steps; cover the merge with tests.
  evidence: Code review of the override template; merge behavior is documented only in the template header and orch-setup does not exist yet.

- source_spec: `_bmad-output/implementation-artifacts/spec-orch-planning-overrides.md`
  summary: Test `plan-check --working-tree` with a separate coordination repo (`--coord` / `ORCH_COORD` pointing away from `--repo`) to prove the snapshot is taken of the coordination repo, not the acting repo.
  evidence: Every working-tree test uses a single repo where `coord_root == repo`, so snapshotting `Env.repo` instead would pass; the shipped planning templates never pass `--coord`, so only direct CLI use is exposed.

- source_spec: `_bmad-output/implementation-artifacts/spec-orch-planning-overrides.md`
  summary: Make plan validation deterministic in orch-gate — a coordination PR that changes epics or the registry fails when it introduces new `plan-check` FAIL findings compared with its base, so a broken plan never reaches main and the planning-template prompts become a convenience, not the guarantee.
  evidence: Walkthrough of PR #14: the sprint-planning override enforces the plan-check verdict only through an activation step and a persistent fact (LLM-executed); today the gate fails plan defects only in a PR's own story and warns on other stories, so a defect in a later story is found only when that story's PR runs.

- source_spec: `_bmad-output/implementation-artifacts/spec-orch-planning-overrides.md`
  summary: orch-setup must verify, at install and on every re-run (for example after a BMad update), that the stock anchors the orch override templates name still exist in the installed stock skills — epics step 3 `STORY FORMAT` and `So that`, step 4 `[C] Complete`, the sprint-planning intents and its append-after-intent activation order — and warn the user when one is gone.
  evidence: Walkthrough of PR #14: the BMad installer overwrites stock skills but never `_bmad/custom/`, and `test_stock_anchors_named_by_the_planning_facts_still_exist` runs only in this repo, so an installed project drifts silently; the templates now carry fallbacks by meaning, which soften but do not detect the drift.

- source_spec: `_bmad-output/implementation-artifacts/spec-orch-gate-ci-templates.md`
  summary: Polyrepo support for the orch-gate CI templates — an optional `registry-auth` block (secret `ORCH_READ_TOKEN` via a git credential helper, never in a URL; `insteadOf` rewrites of `git@host:` and `ssh://git@host/` to HTTPS; fail early naming the secret and its usual causes — unset, protected-only, fork or Dependabot PR) kept whenever the registry has a repo other than `.`, including the coordination repo and the acting repo itself, plus an optional `coord-clone` block (full clone of the coordination repo into `.orch-coord`, `ORCH_COORD=.orch-coord`) kept only in a polyrepo code repo, with tests for every keep/strip combination.
  evidence: Split at the spec token gate; spec review found the gate fetches every non-`.` registry repo by URL into a bare cache (markers.py:103-110, orch.py never fills `local_repos`), so auth is needed in the coordination repo too and fetch errors can leak a URL-embedded token into `orch-gate.json`.
