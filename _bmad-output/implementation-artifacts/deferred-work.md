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
