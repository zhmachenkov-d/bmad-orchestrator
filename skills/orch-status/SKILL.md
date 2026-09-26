---
name: orch-status
description: Shows orch epic progress, anomalies and closes epics. Use when the user says 'orch status', 'where does the epic stand', 'close epic', 'take over a stale claim', 'validate the plan for orch' or 'orch epic report'.
---

# orch-status

Act as the delivery lead's view of an orch project. Anyone who asks should see, in one screen, where every epic stands across all subprojects, what is blocked and why, and what needs a human, and should be able to act on it without leaving the conversation. Every fact comes from the shared library, `orch.py`, which reads git state that all clones share. Your job is to turn its JSON into a view someone can act on, and to carry out the action the user chooses. Never infer a state yourself. When the script cannot read something, say that, and never call it "not merged".

An epic becomes `done` only through the close check: every story merged, every marker archived, every pin converged. Never write `epic-N: done` or a close record by hand. Never commit orch records to main directly either, because the gate protects them through PRs.

## Resolution rules

- `{skill-root}` → this skill's installed directory. `orch.py` → `uv run {skill-root}/../orch-gate/scripts/orch.py`. The orch skills install side by side, and orch-gate owns the library.
- `{communication_language}`, `{implementation_artifacts}` → the values `orch.py config` prints. A null language means the user's language.
- Run from the coordination repo, or pass `--coord <path>` from a code repo. Every command prints JSON. Exit 1 is a negative result (a FAIL, a blocked close, a lost race), so render its JSON as usual. Exit 2 is an environment error, so report it with the flag it names. When it says the config, registry or epics are missing, point to `orch-setup` or planning.

## Status (default)

Run `orch.py status` (add `--epic N` when the user names one). Render a compact table per epic: story, subproject, state, claimant, idle time, blocked-by, and an arrow on the `critical` stories. List `closed` epics on one line instead of a table. Then list `anomalies`, most actionable first, and `unread_repos` and `notices` as caveats. Non-empty `plan_issues` are stories the epics do not define cleanly: show them and offer Plan validation. Offer the actions each anomaly carries.

- `actions[].args` are `orch.py` arguments. Run them as `orch.py <args>` plus the same `--coord` you used. Take over and release change a shared claim ref, so confirm first. A `claim-changed` or `lost-race` result means someone else moved first. Show the new holder. Do not retry. After a take over, the new holder continues the work from `origin/story/<N-M>`: offer `orch.py worktree --story <N-M>`, which checks it out with the node context, then `bmad-build` there (see orch-next).
- A `pin-drift` with `migration_draft` needs a new story of that subproject: offer its `markdown` once (one draft covers every drifting contract of the subproject), placed last in epic `epic` (with `new_epic`, its markdown opens that epic), and suggest a better title if one fits. The user adds it to the epics, because the plan is theirs; then run `plan-check`, and `status` once it passes. Without a draft, no new story converges the drift: name the contract and its owner.
- `sprint-status-lag` → rebuild (below).
- An epic in state `closable` or `archive-needed` → offer Close epic. `drift` means close is blocked until the drift stories land.

## Close epic

Closing takes two passes, and each one is a PR the gate checks. Without an epic number, offer the `closable` and `archive-needed` epics from `status`. Run `orch.py epic close --epic N` first. It changes nothing and returns the `pass`:

- `archive`: branch `orch/close-epic-N` in each listed repo moves the live markers to `.orch/archive/epic-N/`.
- `record`: the archive PRs are merged. Branch `orch/close-epic-N-record` in the coordination repo adds the close record, sprint status with `epic-N: done`, and the retro data file `{implementation_artifacts}/orch/reports/orch-epic-N.json`.
- `blocked`: show the `problems`. `closed`: nothing to do.

Show the plan, and after the user confirms, re-run with `--push --expect-pass <pass>`. A `pass-changed` result means the state moved since the user confirmed: show the new plan instead. For each `published` entry with status `pushed`, open a PR in its `repo` from its `branch` into its `base`: `gh pr create --repo` for GitHub, `glab mr create --repo` for GitLab, with a Conventional Commits title such as `chore(orch): archive epic N markers`. Without those tools, give the branch names. `created-local` means there is no remote, so the user merges it locally. `exists` means an earlier run pushed the branch: open its PR if none is open. `updated` means that branch lacked this pass's files and was moved: open a PR if its earlier one is merged or closed. After the archive pass, tell the user to merge those PRs and then ask to close epic N again. After the record PR merges, offer `bmad-retrospective` and point it at the retro data file.

## Plan validation

Run `orch.py plan-check`. It returns PASS, CONCERNS or FAIL with coded findings. Explain each finding and the fix in the plan: one subproject per story, dependencies that follow registry imports, and breaking changes split into expand → migrate → narrow, where the narrow story depends on a story of every consumer.

## Rebuild sprint status

Run `orch.py sprint-status derive`, show the `changes`, and after the user confirms, run it again with `--write`. It refuses while registry repos are unread, since it would demote stories it cannot see. Commit it on a branch such as `orch/sprint-status-rebuild`, never on main, and open a PR as in Close epic, titled `chore(orch): rebuild sprint status`.

## Epic report

Without an epic number, offer the epics from `status`, open ones first. Run `orch.py report --epic N`, adding `--pdf` when the user asks for PDF. The report is a self-contained HTML file under `{implementation_artifacts}/orch/reports/`. Give the path. When `pdf_error` is set, relay it; the HTML still prints to PDF from any browser.

## Headless

With `-H`, run the one command the invocation names: status, plan-check, rebuild (with `--write`, which writes but does not commit) or report. Print the script's JSON as the only output. Take over, release and close need a human, so an ambiguous or interactive request prints `{"ok": false, "error": ...}` instead.

## Gotchas

- A story's work branch is `story/<N-M>` in its subproject's repo. Idle time counts from the later of the claim commit and that branch's tip, so a claim with a pushed branch that is still getting commits is not stale.
- Review waits come from open `gh` or `glab` PRs whose head is `story/<N-M>`. Without either tool, they fall back to the idle time of stories marked `review` in sprint status.
- Pins freeze at merge. For each subproject and canonical, only the latest merged story that pins it counts. A later `narrow` strands those pins unless it depends on a story of that subproject. That is why `pin-drift` asks for a migration story, never an edit to archived markers, which the gate forbids.
- `--offline` skips other repos and shows claims as last fetched. Their stories show as `unknown`, and close refuses to run.
