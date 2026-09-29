---
title: 'orch-gate fails coordination PRs that introduce plan-check failures'
type: 'feature'
created: '2026-09-28'
status: 'done'
baseline_commit: 'a41ce52c2b0874d06ba90b24525f69a1809a36c4'
route: 'dispatch'
review_loop_iteration: 1
context: []
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** A coordination PR can merge epics or registry changes that `plan-check` would FAIL. The gate fails plan defects only in a PR's own story and only warns on the others. The planning templates run `plan-check` as a prompt, so the LLM enforces it, and a defect in a later story shows up only when that story's PR runs.

**Approach:** When a coordination PR changes registry or planning files, the gate runs the `plan-check` story checks on the PR head, on coordination main and on the PR's merge-base. Every FAIL finding the head adds fails the `setup` check as "introduced by this PR". CONCERNS findings the head adds are warnings. A defect that main or the merge-base already has stays a warning, so a PR is blocked only for defects it introduces.

## Boundaries & Constraints

**Always:**
- Trigger: the same condition as the existing head setup check in `gate.run`: `is_coord` and at least one changed path under `registry_dir/**` or `planning_artifacts/**`. The check runs whether or not the PR carries a story marker.
- Findings come from `plan.check(stories, reg)` on both sides, with no `registry_issues` passed, because registry validity is already judged by `setup_problems`. Leave out `no-epics` and `epics-unreadable`, which are setup failures.
- Baselines (decision by user): coordination main (`ctx.stories`, `ctx.reg`) and the merge-base tree (`mb_tree`, loaded with `registry.load` and `stories.load`). A head finding is introduced only when neither baseline has it, so a branch behind main is not blocked for a defect main has since fixed. Head: `registry.load(head, cfg)` and `stories.load(head, cfg, head_reg)`, loaded once and shared with the existing head setup check.
- Identity (decision by user): a finding is `(severity, code, story, normalized message)`, where normalizing strips a trailing ` (<path>:<line>)` so a line shift is not a new finding. Compare as multisets: for each identity, the head reports `head_count - max(main_count, merge_base_count)` findings (never all of them), so a swapped defect (`1.9` -> `1.8`) is caught and an old finding is never labelled introduced.
- A FAIL is reported as `checks["setup"].fail(code, "introduced by this PR: " + message, story=…, hint=…)`, and the hint names `orch.py plan-check`. A new CONCERN is reported as `warn` with the same prefix. The finding codes are the same as in `plan-check`.
- `setup-repair` is unchanged. A repair PR that also introduces a plan FAIL fails.

**Never:**
- Change `plan.check`, `stories.load`, the check ids, the output schema, or the gate's refs-only reads.
- Fail a PR for defects main already has, or for defects in non-coordination repos.
- Change the planning templates. They stay as they are, and are now a convenience.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| New structural defect | coord PR adds a story that depends on an unknown story | exit 1; setup fail `unknown-dependency`, "introduced by this PR" | N/A |
| New plan-only defect | coord PR adds a `narrow` story with no consumer dependency | exit 1; setup fail `narrow-without-migration` | N/A |
| Registry breaks the plan | coord PR renames a registry file that stories name | exit 1; `unknown-subproject` introduced | N/A |
| Pre-existing defect, lines shift | main has a `bad-contract-change`; the PR inserts lines above it and adds a valid story | exit 0; the defect stays a warning | N/A |
| New concern | PR adds a dependency across subprojects that the registry `imports` do not connect | exit 0; setup warn `dependency-not-imported`, "introduced by this PR" | N/A |
| PR fixes defects | head has fewer findings than main | no introduced findings | N/A |
| Swapped defect | main has 1.5 `Depends on: 1.9`; PR changes it to `1.8` | exit 1; `unknown-dependency` for `1.8` only | N/A |
| Second defect of a kind | main has 1.5 `Depends on: 1.9`; PR adds `1.8` | exit 1; only the `1.8` finding is introduced | N/A |
| Branch behind main | main fixed a defect after the branch point; branch still has it and edits planning | exit 0; defect not introduced | N/A |
| Non-planning coord PR | changes only `README.md` | no plan comparison (same as today) | N/A |

</frozen-after-approval>

## Code Map

- `skills/orch-gate/scripts/orchlib/gate.py:193-213` -- setup block. The `is_coord and any(... setup_dirs)` branch already loads `head_reg` and the head stories: reuse them and add the plan comparison right after the introduced-setup loop. `Check.fail/warn` accept `**extra` (story, hint).
- `skills/orch-gate/scripts/orchlib/gate.py:261-270` -- existing own-story fail / other-story warn loop over main's issues. Keep it unchanged.
- `skills/orch-gate/scripts/orchlib/plan.py:15` `check(stories, reg, registry_issues=())` -> `{"verdict", "findings"}`, each finding with `severity` (`fail`/`concern`), `code`, `story`, `message`. Import `plan` in gate.py.
- `skills/orch-gate/scripts/orchlib/stories.py:101-116` -- messages ending in ` ({path}:{lineno})`, which normalization strips.
- `skills/orch-gate/scripts/orchlib/gate.py:162` -- `mb_tree = Tree(ctx.repo, mb)` already exists in `run`; in the coordination repo it is the coordination merge-base.
- `skills/orch-gate/scripts/orchlib/gate.py:264` -- plan-issues loop repeats `("no-epics", "epics-unreadable")`: use the shared constant there.
- `skills/orch-gate/scripts/tests/test_orch.py:825-837,902-910` -- plan-issue and "cannot break a healthy setup" tests to follow. Fixtures: `mono`, `EPICS`, `R`, `registry_yaml`, `gate`, `check` in `tests/conftest.py`. The `mono` registry has `user-service` import `payment-service`.
- `skills/orch-gate/SKILL.md:34` -- setup row of the checks table.

## Tasks & Acceptance

**Execution:**
- [x] `skills/orch-gate/scripts/orchlib/gate.py` -- add `introduced_plan_findings(baselines: list[tuple[StorySet, Registry]], head_stories, head_reg) -> list[dict]` implementing the identity and multiset rules, call it in the coordination setup branch with main and merge-base, report fails and warns as above; one constant for the two setup story codes, used in both places; a comment that says fails block and concerns warn -- makes the plan guarantee deterministic.
- [x] `skills/orch-gate/scripts/tests/test_orch.py` -- one test per I/O matrix row, plus a planning PR that makes the epics unreadable yields exactly one `epics-unreadable` setup finding -- proves the introduced-only rule.
- [x] `skills/orch-gate/SKILL.md` -- setup row: a coordination PR that changes registry or planning files fails on `plan-check` FAIL findings neither main nor its merge-base has (a `setup-repair` PR too) and warns on such CONCERNS; renumbering a story reports its existing defects as introduced -- documents the new guarantee.
- [x] `_bmad-output/implementation-artifacts/deferred-work.md` -- remove the "Make plan validation deterministic in orch-gate" entry.

**Acceptance Criteria:**
- Given the full orch-gate test suite, when it runs, then every test passes with no skips.
- Given the existing setup-repair and healthy-setup tests, when they run unchanged, then they still pass.

## Implementation Notes

## Spec Change Log

- Superseded by `spec-orch-gate-plan-trial-merge.md`: setup and plan are judged on a trial merge against coordination main only, and plan findings are matched by structured `refs`/`value` instead of message text.
- Review pass 1, intent_gap (two findings): count-by-key hid a swapped defect and relabelled old findings; the main-only baseline blocked branches behind main. Human decided: identity by normalized message, multiset difference; introduced only when absent on both main and merge-base. Amended the frozen Always rules, matrix (three rows), tasks, Code Map, Design Notes. Avoids: a new FAIL passing when it replaces one of the same code, and stale branches failing on fixed defects. KEEP: helper in gate.py reusing the head registry/stories already loaded for the head setup check; reporting under `setup` with the "introduced by this PR: " prefix, `story=` and a hint naming `orch.py plan-check`; the seven tests of v1 (the README one monkeypatches the helper to prove it does not run); the SKILL.md setup-row sentence.

## Review Triage Log

Code review, pass 1 (blind, edge-case, verification-gap):

- high — count-by-key hides a swapped defect: 1.5 `Depends on: 1.9` -> `1.8` keeps key `(fail, unknown-dependency, 1.5)` and exits 0 (reproduced by edge-case hunter); when a count grows, every head finding of that key, old ones included, is reported as introduced (blind, verification-gap) -> intent_gap: the count rule is in the frozen block.
- medium — baseline is the coordination main tip: a branch behind main that fixed a defect still carries it and fails as "introduced by this PR" (reproduced, exit 1) -> intent_gap: the baseline is in the frozen block.
- medium — a PR fine alone can create a plan defect once merged with a concurrent main change (no trial merge) -> defer: the setup "introduced" check has the same limit; branch-up-to-date rules on the platform cover it.
- low — renumbering reports old defects as introduced; SKILL.md does not say so -> patch: one sentence in SKILL.md.
- low — `("no-epics", "epics-unreadable")` literal duplicated in the plan-issues loop next to the new `SETUP_STORY_ISSUES` -> patch: use the constant.
- low — comment says only FAIL findings are handled, code also warns on concerns -> patch: reword.
- low — SKILL.md still reads as if a setup-repair PR always passes -> patch: say a repair PR that introduces a plan FAIL fails.
- low — head registry concerns (path-missing) are not compared -> reject: a missing path for a new subproject is expected until its first story; setup already warns on main's.
- low — no test for a planning PR that corrupts the epics (exclusion filter) -> patch: add a test asserting a single `epics-unreadable`.
- low — no test for the non-coordination repo path -> reject: `is_coord` guard unchanged from the existing setup check, which already has polyrepo tests.
- low — setup row overloaded; `story` field on setup findings undocumented -> reject: style; fields are additive.
- false — spec missing from the diff: the spec is the claims file, excluded on purpose.

Code review, pass 2 (blind, edge-case, verification-gap):

- false — carried: spec missing from the diff; it is the claims file, excluded on purpose.
- low — `_LOCATION_RE` does not strip a location whose path has parentheses (`epics (v2).md`), so a line shift looks introduced -> patch: allow one nested paren level; test.
- medium — no test proves a `setup-repair` PR is still judged on its plan (verification-gap, pre-verified) -> patch: add test.
- low — no test for a defect main has and the merge-base lacks; dropping the main baseline passes every test (verification-gap, pre-verified, filed defer) -> defer.
- low — the setup "introduced" loop still compares with main only, so a branch behind main can be blamed for a registry problem main fixed -> defer: pre-existing behavior of the setup check.
- low — repairing an unreadable/no-epics main flags every plan defect of the repaired epics as introduced -> reject: consistent with "a repair PR that introduces a plan FAIL fails"; rare; relaxing it adds branches.
- low — messages carry subproject names and file paths (`duplicate-story`, `dependency-not-imported`), so renaming a subproject or epics file relabels old findings -> reject: renames change the plan's meaning and are rare; warn-level for concerns.
- low — head that breaks one epic file also yields cascaded `unknown-dependency` fails -> reject: verdict is fail either way; noise only.
- low — plan hint omits `fix_flags` -> reject: the comparison runs only in the coordination repo, where plan-check's default coordination repo is the repo itself; `--coord-ref` cannot combine with `--working-tree`.
- low — hint says "fix it in this PR" on non-blocking concerns; SKILL.md row long; templates do not point to the gate -> reject: wording only; templates are out of scope (Never).
- low — own-story defect reported under marker and setup in a coordination story PR that edits epics -> reject: such a PR already fails scope.
- low — merge-base plan loaded even when it equals the base tip -> reject: negligible cost.
- low — unreadable-epics test covers one epic file only -> reject: exclusion is by code, independent of file count.
- false — "a defect only the merge-base has gets no warning": main fixed it, so the merge takes main's fix; nothing to warn about.

## Design Notes

A PR that renumbers stories moves their existing defects to new ids, so those defects are reported as introduced. This is accepted: the defects are real in the head, and marker keys make renumbering rare.

Normalization: `re.sub(r" \([^()]*:\d+\)$", "", message)`. Messages without a location are compared as they are.

## Verification

**Commands:**
- `uv run --with pytest --with pyyaml pytest -q skills/orch-gate/scripts/tests` -- expected: all pass, no skips.
