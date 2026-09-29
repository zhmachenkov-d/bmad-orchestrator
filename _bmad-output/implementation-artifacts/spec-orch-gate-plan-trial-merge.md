---
title: 'orch-gate judges coordination setup and plan on a trial merge, matching findings structurally'
type: 'feature'
created: '2026-09-29'
status: 'done'
baseline_commit: '44974d7494140fefe6bc1071e4a25c4b2ac1679d'
route: 'dispatch'
review_loop_iteration: 0
context:
  - '{project-root}/_bmad-output/implementation-artifacts/spec-orch-gate-plan-regression.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** The plan-regression check of PR #25 compares the PR head with coordination main and the merge-base, so it misses a PR that breaks the plan only merged with main as it is now. It identifies findings by message text, so renumbering a story or renaming a subproject reports old defects as introduced. No test fails when the main baseline is dropped. In `--format text`, fail and warn lines inside one check look the same.

**Approach:** Build the post-merge tree with `git merge-tree --write-tree <base> <head>` and judge the setup "introduced by this PR" check and the plan comparison on it, against coordination main only. Give plan findings additive structured fields (`refs`, `value`) and compare them by those fields after mapping story ids and subproject names to main's by story title and registry location. Print each finding's level in text output. The trial merge sees main as of the run; that it is the merge that lands relies on a merge queue or up-to-date-branch rule.

## Boundaries & Constraints

**Always:**
- Trigger unchanged: `is_coord` and a changed path under `registry_dir/**` or `planning_artifacts/**`. `merge-tree` runs only when the trigger holds.
- Sides: merge `ctx.base` (the PR target) with `head_sha`; the baseline stays `ctx.stories`/`ctx.reg` (coordination main). When `ctx.base` and `ctx.coord.ref` resolve to different SHAs, add a notice saying the plan was merged into one and compared with the other.
- Merged tree: `git merge-tree --write-tree -z --name-only --no-messages <ctx.base> <head_sha>`, read through `Tree(repo, <tree oid>)`. rc 0 → clean; rc 1 with a hex OID as the first NUL field → conflicts, the remaining fields are the paths; anything else → `OrchError`. Before calling, `git version` below 2.38 is an environment error (exit 2) naming 2.38. The working tree, index and refs are never touched. `mb`/`mb_tree` stay for the other checks.
- Conflict (decision by user): only conflicted paths under `registry_dir/**` or `planning_artifacts/**` count. They fail `setup` with code `setup-merge-conflict`, the paths in the message, hint "merge or rebase onto <base>, then re-run". Then no merged-tree `setup_problems`, no `setup-repair`, and both the setup "introduced" loop and the plan comparison are skipped; main's own setup fails and warns are reported as usual. Conflicts elsewhere leave `setup` alone.
- Setup check: `setup_problems` on the merged tree replaces the head in the `setup-repair` decision and the "introduced" loop, which keeps comparing `(code, message)`. Reported messages replace ` at <tree oid>` with ` after merging into <base>`; the comparison strips both suffixes.
- Structured findings (decision by user): every finding of `stories` (parse and validate) and `plan.check` carries `refs: {"stories": [...], "subprojects": [...]}` — every story id and subproject name its message mentions besides `story`, in message order, never file paths — and, for findings about a rejected value (`bad-depends-on`, `bad-contract-change`, `duplicate-label`, `contract-change-outside-contracts`), `value`. Fields are additive; `message` is unchanged.
- Plan identity: `(severity, code, map(story), map(refs.stories), map(refs.subprojects), value)`, multiset difference against main; the message is not part of it. The reported finding keeps the merged tree's fields verbatim; `story=` on the setup finding is the merged id. Maps, built for the merged side only:
  - A story maps to the main story with the same title when that title is unique on both sides.
  - An unmapped story keeps its id if main has a story with that id and no other story mapped to it by title; otherwise `new:<id>`.
  - A subproject maps to the main subproject with the same (`normalize_repo(repo)`, `path`) when that pair is unique on both sides. An unmapped one keeps its name if main has that name and nothing mapped onto it; otherwise `new:<name>`.
  - Ids and names that are not merged-tree stories or subprojects pass through unchanged.
- `render_text` prefixes each fail or warn finding line with its level: `- [fail] ... [code]`.

**Never:**
- Change `stories.load`/`registry.load` behavior, check ids, finding codes or messages, or remove or rename any JSON field; new finding fields are only `refs` and `value`.
- Fail a PR for defects main already has, or in non-coordination repos.
- Change the planning templates or `render_markdown`.

## I/O & Edge-Case Matrix

Branch-side stories go after story 1.1 or in a new `epics-2.md` so the trial merge stays clean unless a row says otherwise.

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Concurrent main change | main later deletes story 1.2 (nothing on main depends on it after the edit); branch adds 1.5 `Depends on: 1.2` in `epics-2.md` | exit 1; `unknown-dependency` for 1.5 introduced | N/A |
| Main gained a defect | main adds 1.5 `Depends on: 1.9` after the branch point; branch adds a valid 1.6 in `epics-2.md` | exit 0; 1.9 defect stays a warning | N/A |
| Behind main, main fixed defect | main fixes a defect the merge-base has; branch adds 1.6 in `epics-2.md` | exit 0, nothing introduced | N/A |
| Planning conflict | branch and main edit the same epics lines differently | exit 1; `setup-merge-conflict` naming the epics file; nothing "introduced" | N/A |
| Conflict outside setup dirs | branch edits epics cleanly and conflicts on `README.md` | no `setup-merge-conflict`; comparisons run | N/A |
| Swapped defect | main 1.5 `Depends on: 1.9`; PR changes it to `1.8` | `unknown-dependency` for 1.8 introduced | N/A |
| Swapped bad value | main 1.5 `Contract change: maybe`; PR changes it to `perhaps` | `bad-contract-change` introduced | N/A |
| Renumbered story | main 1.5 "Refunds" `Depends on: 1.9`; PR renumbers it to 1.6 | exit 0; defect not introduced | N/A |
| Renamed subproject | PR renames `user-service` to `user-svc`, same repo and path; main has a concern on a user-service story | concern not introduced | N/A |
| Ambiguous titles | main has 1.5 "Refunds" `Depends on: 1.9`; PR adds 1.6 also titled "Refunds" | ids compared as they are; 1.5's defect not introduced | N/A |
| New story reuses a moved id | PR moves "Refunds" 1.5 -> 1.6 and adds a new 1.5 `Depends on: 1.9` | new 1.5 defect introduced | N/A |
| Old git / merge-tree error | `git version` 2.37, or merge-tree fails | exit 2 naming 2.38 / `OrchError` | monkeypatch `gitio.git` |
| Text output | check with a fail and a warn | lines read `- [fail] ...` and `- [warn] ...` | N/A |

</frozen-after-approval>

## Code Map

- `skills/orch-gate/scripts/orchlib/gitio.py:80` `merge_base`, `:161` `Tree` -- `Tree` reads via `cat-file blob <ref>:<path>` / `ls-tree <ref>`, both fine with a tree oid. Add `merge_tree(repo, base, head) -> (tree_oid, conflicted_paths)` and the version check next to `merge_base`. `normalize_repo` lives here.
- `skills/orch-gate/scripts/orchlib/stories.py:101-122,152-185` -- every `_issue(...)` call; extend `_issue` with `refs`/`value` keyword args (defaults: empty lists, no `value`).
- `skills/orch-gate/scripts/orchlib/plan.py` -- four finding dicts in `check` and `_narrow`: add `refs` (dependency ids, exporter and consumer names).
- `skills/orch-gate/scripts/orchlib/gate.py:225-251` -- setup block: merged `Tree` for `head_reg`, `head_stories`, `setup_problems`; drop only `mb_reg` and the merge-base baseline. `:142` `registry-empty` and `Tree.text` (`gitio.py:204`) put ` at <ref>` in messages.
- `gate.py:161-185` -- replace `_LOCATION_RE`/`_plan_identity` with the structured key; `introduced_plan_findings(base_stories, base_reg, stories, reg)`.
- `gate.py:527-545` `render_text` -- finding line. Notices: `result["notices"]` entries `{code, message, hint}`.
- `skills/orch-gate/scripts/tests/test_orch.py:913-1027` -- PR #25 plan tests; `test_a_branch_behind_main_is_not_blamed_for_a_defect_main_fixed` conflicts under a trial merge: move its 1.6 to `epics-2.md`. Fixtures in `tests/conftest.py`. Tests asserting whole finding dicts may need the new fields.
- `skills/orch-gate/SKILL.md:34` (setup row), `:57` (plan-check JSON).

## Tasks & Acceptance

**Execution:**
- [x] `skills/orch-gate/scripts/orchlib/gitio.py` -- `merge_tree` and git version check -- trial merge without a working tree.
- [x] `skills/orch-gate/scripts/orchlib/stories.py`, `plan.py` -- `refs`/`value` on every finding -- structured identity.
- [x] `skills/orch-gate/scripts/orchlib/gate.py` -- merged-tree setup and plan comparison, `setup-merge-conflict`, base/coord notice, structured key with maps, `[fail]`/`[warn]` in text -- the rework.
- [x] `skills/orch-gate/scripts/tests/` -- one test per matrix row; an invariant test that, over fixtures producing every story and plan finding code, each story id and registry subproject name in a message other than `story` appears in `refs`; keep PR #25 tests passing -- proves the rules.
- [x] `skills/orch-gate/SKILL.md` -- setup row: judged on a trial merge with main as of the run (drop "nor its merge-base" and the renumbering sentence); a merge queue or up-to-date-branch rule makes that the merge that lands; conflict in setup paths fails; renumbered stories and renamed subprojects are matched. Mention `refs`/`value` where plan-check JSON is described -- documents it.
- [x] `_bmad-output/implementation-artifacts/deferred-work.md` -- remove the three plan-regression entries; add one: orch-setup verifies a merge queue (GitHub) or an up-to-date / merged-results rule (GitLab) on the coordination repo, since the trial merge sees main only as of the run -- keeps the open half visible.
- [x] `_bmad-output/implementation-artifacts/spec-orch-gate-plan-regression.md` -- Spec Change Log line: superseded by this spec -- traceability.
- [x] Commit: all of it in one commit on `feat/orch-gate-plan-check` (decision by user), then push.

**Acceptance Criteria:**
- Given the full orch-gate suite, when it runs, then every test passes with no skips.
- Given the comparison baseline replaced by the merge-base, or the merged tree replaced by `head`, when the suite runs, then at least one test fails.

## Implementation Notes

## Spec Change Log

## Review Triage Log

- Spec review (pre-approval, 12 findings): all patched into the draft — merge-tree `-z` parsing and rc/version handling; conflict-free fixtures (`epics-2.md`); trigger scope of `merge-tree`; conflict interplay with `setup-repair`; comparison key vs reported finding; subproject key and `new:` names; base vs coord notice; tree oid in messages; observable ambiguous-title row, old-git row, second AC.
- Party review (pre-approval), decisions by user: structured `refs`/`value` replace regex rewriting of message text (drops the token-delimiter and single-pass rules); one commit; subproject key through `normalize_repo`; the guarantee is stated as "main as of the run", with a deferred orch-setup check for merge queue / up-to-date rules. `setup-merge-conflict` fires in CI mainly on GitLab MR pipelines (GitHub does not run `pull_request` workflows on conflicting PRs; `merge_group` heads already contain the base).

Code review, pass 1 (blind, edge-case, verification-gap):

- low — `merge_tree` gets the `ctx.base` ref name, not `result["base_sha"]`; a ref moving between resolve and merge makes the trial merge and the reported base differ (blind, edge-case) -> patch: pass the SHA.
- low — concurrent-change test puts branch stories in `epics-2.md`, which sorts before `epics.md`, so the head alone already fails and "fine alone" is not modelled -> patch: a file sorting after `epics.md`.
- low — registry-issue findings in `plan.check` carry only their own subproject in `refs`; `unknown-import`, `write-overlap`, `import-cycle` messages name others (blind, edge-case) -> patch: refs from the registry call sites; invariant test covers them.
- low — git >= 2.38 requirement undocumented -> patch: one sentence in SKILL.md Gotchas.
- medium — setup half of the trial merge untested; judging `head` there passes the suite (verification-gap, pre-verified; blind) -> patch: test.
- medium — `bare()` suffix stripping and the tree-oid rewrite untested (verification-gap, pre-verified; blind on oid leaks) -> patch: tests.
- low — conflict path untested on a broken main (verification-gap, pre-verified) -> patch: test.
- medium — `subproject_map` name fallback untested; forcing `new:` passes (verification-gap, pre-verified) -> patch: test.
- low — baseline is coordination main while the merge side is `ctx.base`; with an explicit different `--coord-ref`, a defect only the target has is labelled introduced -> reject: frozen decision with a notice; CI resolves coord ref from `--base`; rare, fix adds branches.
- low — invariant test checks refs ⊇ message names, not order or extras -> reject: both sides are built by the same code, so extras cannot make an old finding look new.
- low — `bad-contract-change` `value` is raw, not normalized -> reject: a different spelling is a different rejected value; cosmetic.
- low — `setup-merge-conflict`, `trial-merge-base-differs` and the text line format not all documented -> reject: the setup row names `setup-merge-conflict`; notices self-describe; text format is for humans.
- low — known limits not filed in deferred-work -> reject: accepted design limits recorded in Design Notes.
- false — spec status/tasks inconsistent: the spec is committed in `970bad0`, `in-review` is the expected status here, and the fix would edit this spec.
- low — rename/delete conflict moving a planning file out of setup dirs escapes `setup-merge-conflict` (edge-case) -> reject: main deleted the file, so main and the merge both lack it; contrived.
- false — 7 skipped tests: they appear only in the reviewer's scratch copy; the suite here runs 244 passed, no skips.

## Design Notes

An unmapped id falls back only when main's story with that id is unclaimed: when "Refunds" moves 1.5 -> 1.6 and a new story takes 1.5, main's 1.5 is claimed by the title match, so the new 1.5 keys as `new:1.5` and its defects cannot hide behind the old ones. A story whose title changed but whose id did not still matches by id.

Known limits: a story replaced under the same id with a changed title hides its defects behind identical old ones; renaming and renumbering a story at once reports its old defects as introduced; the setup "introduced" loop still compares message text, so a subproject rename relabels main's registry problems (main is failing then anyway).

## Verification

**Commands:**
- `uv run --with pytest --with pyyaml pytest -q skills/orch-gate/scripts/tests` -- expected: all pass, no skips.
