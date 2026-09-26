---
title: 'orch override templates for stock planning skills'
type: 'feature'
created: '2026-09-26'
status: 'done'
baseline_commit: 'db3fa89d4adbf7f3b947c43e3304e95fb7b5192e'
route: 'dispatch'
review_loop_iteration: 0
context: []
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Stock `bmad-create-epics-and-stories` knows nothing about the orch registry, so it writes stories without the `**Subproject:**`, `**Depends on:**` and `**Contract change:**` labels the gate reads. Stock `bmad-sprint-planning` passes plans that `orch.py plan-check` would fail, and it flags orch-shaped plans (dependency chains, contract stories) as defects. Both problems then surface only when a story PR hits the gate.

**Approach:** Ship two templates as orch assets next to the `bmad-build` one, for `orch-setup` to install in the coordination repo later. Each template uses only `persistent_facts`. The epics template loads the registry and the orch story rules, and it runs `plan-check` before the stock final menu. The sprint-planning template folds `plan-check` into the readiness gate. `plan-check --working-tree` lets both check planning files that are not yet committed. Tests keep everything consistent with the stock skills, the `orch.py` CLI and the BMad customization resolver.

## Boundaries & Constraints

**Always:**
- Only stock keys, with stock types. Each template sets only `persistent_facts`, so there is no `on_complete`: a string would replace an existing team one, and the sprint-planning `on_complete` runs for every intent.
- Call `orch.py` only as `uv run @ORCH_CLI@ …`, with the same substitution rule as `bmad-build.toml`. `@ORCH_REGISTRY_DIR@` is the normalized `registry_dir` that `orch.py config` prints: repo-relative, with no `{project-root}` and with `{project_name}` expanded. Registry facts are `file:{project-root}/@ORCH_REGISTRY_DIR@/*.yaml` and `…/*.yml`.
- The epics rules fact says it overrides the stock story format and the "user value only" examples. It requires:
  - Label lines right after the "So that" line and before `**Acceptance Criteria:**`, outside code fences and before any other heading. Every story gets all three.
  - `Subproject`: the exact registry file stem, or `contracts`. `contracts` is an implicit pseudo-subproject with no registry file; it writes only the contracts dir.
  - `Depends on`: backward story ids (`N.M`, letter suffix allowed), comma-separated, or `none`. A cross-subproject dependency should follow the registry `imports`.
  - `Contract change`: `none`, except on `contracts` stories, which may use `expand` or `narrow`.
  - The contract story comes before the stories that use it. A breaking change goes expand → migrate (one story per consumer, meaning each registry entry whose `imports` names the exporter) → a `narrow` story that comes after the `expand` and depends on every migration story. Contract and migration stories are valid even without direct user value.
  - Epics are read only from `epic*.md` under `planning_artifacts`, one current file per epic set (no leftover `epics-v1.md`).
- Epics flow: the fact requires running `plan-check --working-tree` during Step 4 validation and showing the verdict before offering [C]. On FAIL or CONCERNS, offer to fix the epics first.
- Sprint-planning fact:
  - It applies only to the **readiness** and **sprint-planning** intents, never to status, validate or fix. Before stating the gate verdict, run `plan-check --working-tree`.
  - Mapping: FAIL → FAIL, CONCERNS → at least CONCERNS, PASS → no change. List findings with their `code`, story and message, and name `bmad-create-epics-and-stories` or `bmad-correct-course` as the fix. Headless runs put them in `findings`.
  - Under orch, backward `Depends on` chains and contract or migration stories are expected. They are not "not independently completable" or orphan findings, because plan-check owns those checks.
- Exit 2, or output without JSON, is an error, never a verdict. Show the `code`/`error` or the raw output, and report the orch check as not run. For readiness, that means at least CONCERNS. Never re-implement the checks in prose.

**Decision (working tree):** `plan-check` gets `--working-tree`. After coord resolution, it snapshots `Env.coord_root`'s working tree into a tree object:
- Use a temp `GIT_INDEX_FILE`, seeded by copying the real index (`git rev-parse --git-path index`) or, when there is none, `read-tree --empty`. Then run `git add -A` and `git write-tree`.
- Tracked and untracked files are included, and `.gitignore` is respected. The real index and HEAD stay untouched.
- Config, registry and epics are read from that tree. The JSON gets `"read_from": "working-tree"`.
- It cannot be combined with `--coord-ref` (exit 2, `bad-args`). The gate still trusts refs only.

**Never:**
- Edit stock skill files or `_bmad/custom/` in this repo.
- Change orch library behavior beyond `plan-check --working-tree`.
- Build the `orch-setup` merge logic or the CI templates (deferred).

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Epics created | registry present | every story has the three labels; plan-check verdict shown before [C] | N/A |
| Epics, no registry | no registry YAML | say orch has no registry and point to `orch-setup`; still write labels, asking the user for `Subproject` | findings shown as is |
| Sprint planning, clean | plan-check PASS | stock verdict; dependency chains and contract stories are not flagged | N/A |
| Sprint planning, orch defects | FAIL / CONCERNS | readiness FAIL / CONCERNS with findings and fix skill | stock FAIL flow |
| Sprint planning, no registry or `no-epics` | empty registry, or epics not named `epic*.md` | FAIL; point to `orch-setup`, or explain the `epic*.md` rule | N/A |
| Status / validate / fix intent | any | no plan-check | N/A |
| CLI cannot run | uv missing, wrong path, exit 2 | show output; orch check not run | no invented verdict |
| Uncommitted planning | new or edited epics and registry, never committed; repo with no commits | plan-check sees them; index and HEAD unchanged | N/A |
| Ignored planning dir | `_bmad-output/` gitignored | `no-epics`; documented gotcha | N/A |

</frozen-after-approval>

## Code Map

- `.claude/skills/bmad-create-epics-and-stories/SKILL.md:54`, `steps/step-04-final-validation.md:125-143` -- customization is loaded through `resolve_customization.py` (JSON merge only, no `{project-root}` substitution or `file:` expansion; the LLM does both). The final menu [C] is followed by `bmad-help`, then `on_complete`. The epics file is saved as `epics.md`, which matches `epic*.md`.
- `.claude/skills/bmad-create-epics-and-stories/steps/step-03-create-stories.md:75-100` -- stock STORY FORMAT and "independent / user value" examples that the rules fact overrides.
- `.claude/skills/bmad-sprint-planning/SKILL.md:12-26,31-33` -- facts load before intent detection. `on_complete` runs "whatever the intent", which is why it is not used. `references/readiness-gate.md` -- "independently completable" and "orphans" checks that the fact qualifies. Headless JSON carries `gate`/`findings`.
- `_bmad/scripts/resolve_customization.py` -- used by the tests: `--skill <dir> --project-root <root> --key workflow` gives merged JSON.
- `skills/orch-gate/scripts/orch.py:36-61` `Env` (add the snapshot after coord resolution), `:262-266` `cmd_plan_check`, `:451` parser. `plan-check` is not in `REFRESHING`.
- `skills/orch-gate/scripts/orchlib/gitio.py:21-29` `git_env` (keeps `GIT_INDEX_FILE` passed via `env=`), `:128-194` `Tree`: a tree id works as a ref (only `cat-file`, `ls-tree` and `rev-parse <ref>:<path>` are used).
- `skills/orch-gate/scripts/orchlib/config.py:67-78` `_rel` -- normalization that `@ORCH_REGISTRY_DIR@` must match. `registry.py:88-95,150` -- `contracts` pseudo, `.yaml`/`.yml`. `stories.py:23-30` label regexes and `CONTRACT_CHANGES`. `plan.py` -- finding codes.
- `skills/orch-gate/assets/custom/bmad-build.toml`, `skills/orch-gate/scripts/tests/test_overrides.py`, `…/tests/conftest.py` (`mono` fixture, `run_cli`) -- pattern to follow.
- `skills/orch-gate/SKILL.md:41-51,55-60` -- story metadata and Gotchas.

## Tasks & Acceptance

**Execution:**
- [x] `skills/orch-gate/scripts/orchlib/gitio.py`, `skills/orch-gate/scripts/orch.py` -- add a `gitio.worktree_tree(repo)` snapshot helper and `plan-check --working-tree` per the Decision.
- [x] `skills/orch-gate/scripts/tests/test_orch.py` -- tests: uncommitted and untracked epics and registry are seen; a repo with no commits works; the real index and HEAD are unchanged; `read_from` is set; combining with `--coord-ref` exits 2.
- [x] `skills/orch-gate/assets/custom/bmad-create-epics-and-stories.toml` -- header (what it is, both placeholders, merge note, install in the coordination repo), then `persistent_facts`: the two registry `file:` entries, the rules fact and the Step 4 plan-check fact.
- [x] `skills/orch-gate/assets/custom/bmad-sprint-planning.toml` -- header, then `persistent_facts`: one readiness fact per the Always rules.
- [x] `skills/orch-gate/SKILL.md` -- document `--working-tree` in CLI behavior and Gotchas (gitignored files are invisible).
- [x] `skills/orch-gate/scripts/tests/test_overrides.py` -- generic checks over all three templates: TOML parses; keys and types match stock; ≤4 keys; placeholders only, with no `{skill-root}`, absolute path or bare `orch.py`; every `uv run @ORCH_CLI@ …` call parses with `build_parser()`. Keep per-template assertions: `bmad-build` keeps its exact key set and its no-`file:` check, and the planning templates have exactly `{persistent_facts}`. Exact coverage phrases per template: the three labels, `CONTRACT_CHANGES` values, `contracts` pseudo-subproject, `epic*.md`, "overrides the stock story format", "before offering [C]", the intent restriction, the verdict mapping, `orch-setup`, "never a verdict". A resolver test per planning skill: install the substituted template in a tmp project, run `resolve_customization.py`, and assert the orch text is in the merged `persistent_facts` with no `@…@` left. A `{project-root}/…` `orch_registry_dir` must normalize to the substituted `@ORCH_REGISTRY_DIR@` via `orch.py config`.

**Acceptance Criteria:**
- Given the full test suite, when run, then all tests pass with no skips in this repo.
- Given the repo after the change, when `git status --porcelain _bmad .claude` runs, then it is empty.

## Implementation Notes

- Normal `plan-check` runs now also emit `"read_from": "ref"` with `coord_ref`, for symmetry with `--working-tree`.
- An empty registry has no dedicated plan-check finding (out of scope). Both templates tell the model to detect a missing registry file itself and point to `orch-setup`.
- `git add -A` in the throwaway index writes loose objects to the coordination repo's object store; the real index and HEAD are untouched (tested).
- Suite: 181 passed, 0 skipped (after review pass 1 patches).

## Spec Change Log

## Review Triage Log

- high — `@ORCH_REGISTRY_DIR@` raw config may include `{project-root}` → defined as normalized `registry_dir`, tested.
- high — planning skills use `resolve_customization.py`, not the renderer → resolver test replaces render test.
- high — stock step-03 format and "user value" examples conflict with orch rules → fact overrides them explicitly.
- medium — stock readiness flags dependency chains and orphan contract stories → sprint fact qualifies them.
- medium — temp index seeded from HEAD fails with no commits and loses the stat cache → seed from copied index, else empty.
- medium — `.yml` registry files missed → second `file:` entry.
- medium — rules incomplete (contracts pseudo, consumers, expand order, imports, suffix ids, fences, `epic*.md`) → added.
- medium — contradiction on `Contract change` presence → every story gets it, `none` by default.
- medium — no rows for no registry, `no-epics` or old epic files → added.
- medium — sprint `on_complete` runs for every intent; the fact may be skipped → fact scoped to two intents and required before the verdict; no `on_complete`.
- medium — "covering phrase" untestable; generalized test would break `bmad-build` asserts → exact phrases, per-template asserts.
- low — epics plan-check ran after "ready for development" → moved before [C]; `on_complete` dropped.
- low — snapshot repo and flag name unclear → `Env.coord_root`, `--working-tree`, `read_from`.
- low — gitignored planning dir invisible → gotcha documented.
- low — test paths → full paths.

Code review, pass 1 (blind, edge-case, verification-gap):

- medium — an untracked nested git repo with no commit makes `git add -A` exit 128, so `plan-check --working-tree` exits 2 (reproduced in scratch) → patch: `add -A --ignore-errors`, tolerate rc 1, still `write-tree`.
- medium — no test where only the working-tree `_bmad/custom/config.toml` moves the registry or planning dir; reading config at the ref would pass every test (verification-gap, pre-verified) → patch: add test.
- medium — no test for a tracked epics file deleted on disk only; the copied index could keep it (verification-gap, pre-verified) → patch: add test.
- medium — planning-template tests never check that the stock anchors they name still exist (step-04 `[C] Complete`, step-03 `STORY FORMAT`, sprint intents) → patch: add a stock-anchor test.
- low — rule 6 says "beside it", but `epic_files` reads recursively and case-insensitively (`stories.py:126-128`), so an `archive/epics-old.md` is read too → patch: reword to "anywhere under `planning_artifacts`, subfolders included".
- low — sprint rule 2 checks the registry dir on disk; an ignored registry dir is visible there but not to plan-check → patch: mention an ignored registry dir in the parenthetical.
- low — gotcha names only `.gitignore`; `.git/info/exclude` and `core.excludesFile` hide files too, and the loose-object cost goes unmentioned → patch: extend the gotcha.
- low — working-tree JSON omits the snapshot tree id, while errors cite that id → patch: emit `coord_ref` (tree id) in working-tree mode too.
- low — registry facts glob top level only, but `registry.load` lists recursively → reject: nested registry files are unusual, and the glob is fixed in the frozen Always rules.
- low — scope `git add` to planning pathspecs for speed and object growth → reject: untracked subproject paths would be missing from the snapshot and trip registry path checks; cost is documented instead.
- low — assume-unchanged bits in the copied index could hide edits → reject: rarely set on planning files; the fix adds refresh logic.
- low — headless `findings` on CONCERNS / CONCERNS when the CLI cannot run → reject: matches the frozen verdict mapping; the extra array is additive.
- low — no contracts-dir placeholder → reject: planning names the `contracts` subproject, never the path.
- low — `ABSOLUTE` regex covers a fixed set of roots; ≤4 cap unexplained → reject: the cap is the module-plan override budget; regex is a backstop to the `uv run @ORCH_CLI@` check.
- low — `coord_ref_source` "working tree" vs `read_from` "working-tree" → reject: different fields, no consumer compares them.
- false — resolver test with an empty registry dir: the resolver does not expand `file:` globs (`resolve_customization.py` merges TOML only).
- false — linked worktree index path: `rev-parse --git-path index` resolves under `.git/worktrees/<name>/`.
- defer — no `--working-tree` test with a separate `--coord` repo: shipped templates never pass `--coord`.

## Verification

**Commands:**
- `uv run --with pytest --with pyyaml pytest -q skills/orch-gate/scripts/tests` -- expected: all pass, no skips.
- `git status --porcelain _bmad .claude` -- expected: empty.
