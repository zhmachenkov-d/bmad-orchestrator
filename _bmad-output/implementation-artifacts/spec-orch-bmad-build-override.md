---
title: 'orch override template for stock bmad-build'
type: 'feature'
created: '2026-09-26'
status: 'done'
baseline_commit: '3e7257ac5e3fe6d9b367d0cbd4f5ac7b6018c1b1'
route: 'dispatch'
review_loop_iteration: 0
context: []
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** `orch-next` hands a claimed story to stock `bmad-build` in its worktree. Nothing makes `bmad-build` load the node context or close the story the orch way: write the marker `.orch/stories/<key>.yaml`, then run `orch-gate`. Without that, the builder ignores `allowed_write` and pins, and a PR without a marker fails the gate.

**Approach:** Ship a `_bmad/custom/bmad-build.toml` template as an orch asset that `orch-setup` installs later. It uses only stock customization keys. An activation step, gated on the branch, refreshes and loads the node context. A literal persistent fact holds the builder to it. `on_complete` writes and commits the marker, then runs the gate. Tests keep the template consistent with stock `bmad-build`, the `orch.py` CLI and the BMad renderer.

## Boundaries & Constraints

**Always:**
- Use only keys present in stock `bmad-build` `customize.toml`, with the same types. Override at most 4 keys.
- An orch run is one whose current branch (`git symbolic-ref --short HEAD`) matches `^story/`. Every orch instruction applies only to an orch run. On any other branch the override does nothing and `bmad-build` behaves stock.
- The template calls `orch.py` only as `uv run @ORCH_CLI@ …`. `orch-setup` replaces `@ORCH_CLI@` with a `{project-root}`-relative path, by default `{project-root}/.claude/skills/orch-gate/scripts/orch.py`. It never uses an absolute path, because the file is committed team config that every clone and worktree shares. It never uses `{skill-root}`, because the renderer binds that to the render snapshot.
- The marker key comes from `story.key` in `.orch/context/node-context.json`. Only `.orch/stories/<key>.yaml` goes into the marker commit, with the message `chore(orch): add marker for story <key>`.
- The gate runs as `gate --format text`, matching the context's Finish section.
- `on_complete` overrides the stock push/PR offer. Nothing is pushed until the gate passes. Push and PR are offered, never done automatically.

**Decision (location):** The template lives at `skills/orch-gate/assets/custom/bmad-build.toml`. Create Module deletes the `orch-setup` folder, and this location survives that. It also sits next to the library whose CLI it calls. `orch-setup` will read it through `../orch-gate/assets/`.

**Never:**
- Edit stock skill files or `_bmad/custom/` in this repo.
- Re-implement orch logic in prose.
- Override review layers or `implementation_handoff`.
- Use a `file:` persistent fact for the context. It is not branch-gated, and a stale context file would leak into non-orch builds.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Orch story, dispatch or oneshot route | branch `story/1-2` | context refreshed and read at activation; after the stock commit: marker written and committed, gate run, verdict shown, push/PR offered on pass | N/A |
| Non-orch build | any other branch, even with a stale `.orch/context/` | stock behavior; no orch command runs, no context read | N/A |
| Context refresh fails | `context` exit 2 (e.g. `coord-not-local`) | show `code`/`error`; use the existing `node-context.md` if present, else HALT and ask (e.g. for `--coord`) | never invent context |
| Marker write fails | exit 2 (`story-not-found` …) | report it; no hand-written marker | stop |
| Gate verdict fail | exit 1 | failing checks with hints; no push; code fixes need a new commit and a rewritten marker (pins at HEAD) | stop, no loop |
| Gate error | exit 2 (e.g. `driver-missing` fix, fetch error) | show `code`/`error`/fix; claim no verdict | stop |
| Override missing on branch | story branch cut before the override reached base | stock behavior; the orch-next handoff still points to the context | N/A |

</frozen-after-approval>

## Code Map

- `.claude/skills/bmad-build/customize.toml` -- stock surface. `activation_steps_prepend` is a list that runs in Activation Step 1. `persistent_facts` is a list loaded in Step 2; the renderer passes entries through as Markdown and resolves nothing. `on_complete` is a string that runs last in `step-05-present.md` and `step-oneshot.md`, after their commit. Strings replace (a `.user.toml` can override the team string); lists append. Empty list items are rejected (`render_skill.py:55-69`).
- `_bmad/scripts/render_skill.py:232-243,322` -- `{skill-root}` is bound to the snapshot; `{project-root}` and `@ORCH_CLI@` pass through. `render(project_root, skill_dir)` writes under `<project_root>/_bmad/render`. Run it as `uv run render_skill.py --project-root <tmp> --skill <stock dir>`.
- `skills/orch-gate/scripts/orch.py:400` -- `build_parser()`, used to check subcommands and flags. `context [--story] --write` (:302-317) infers the key from the branch. `marker write --story` is required (:419-423) and pins at HEAD (:149). `gate --format text` needs no `--base` in a monorepo worktree (:340-351).
- `skills/orch-gate/scripts/orchlib/work.py:172-180,276-281` -- `.orch/context/` is excluded from commits; the Finish section order.
- `skills/orch-gate/scripts/orchlib/gate.py:163,273` -- `implementation_artifacts/**` is exempt from scope only in the coord repo (see the known limit).
- `skills/orch-gate/scripts/tests/conftest.py` -- pytest harness.

## Tasks & Acceptance

**Execution:**
- [x] `skills/orch-gate/assets/custom/bmad-build.toml` -- write the template, in three parts:
  - Header comment: what the file is; the `@ORCH_CLI@` substitution rule; that `orch-setup` merges it into an existing `_bmad/custom/bmad-build.toml` and must combine an existing `on_complete` rather than replace it; that the file must reach the base branch before worktrees see it; that a personal `.user.toml` `on_complete` replaces the orch one.
  - Keys: `activation_steps_prepend` (branch check, `context --write`, read `node-context.md`), `persistent_facts` (one literal fact: on an orch run, stay within the context's `allowed_write`, build against pinned contracts, and close via `on_complete`), and `on_complete` (branch check, marker, commit, gate, verdict, push/PR offer).
- [x] `skills/orch-gate/scripts/tests/test_overrides.py` -- tests:
  - The template parses as TOML.
  - Its keys and types match stock `customize.toml`. Look for it in `.claude/skills/bmad-build/` or `.agents/skills/bmad-build/`, and `skip` if neither exists.
  - It has at most 4 keys, contains `@ORCH_CLI@`, and has no `{skill-root}` and no `file:`.
  - Each `orch.py` call it names (`context --write`, `marker write --story`, `gate --format text`) parses with `build_parser()`.
  - Render: in a tmp project (copy `_bmad/config.toml` and the stock skill; install the substituted template), rendering succeeds. `workflow.md`, `step-05-present.md` and `step-oneshot.md` contain the orch text, and no `@ORCH_CLI@` remains. Skip if `_bmad/scripts/render_skill.py` is absent.

**Acceptance Criteria:**
- Given the full test suite, when run, then all tests pass, including the render test.
- Given the repo after the change, when `git status` runs, then nothing under `_bmad/` has changed.

## Design Notes

Marker and gate go in `on_complete` because the stock completion commits the work first and `marker write` pins at HEAD. Known limit: in a polyrepo code repo, stock `bmad-build` commits its spec under `implementation_artifacts`. The gate exempts that path only in the coord repo, so such a PR fails `out-of-scope`. A code repo without BMad never loads the override at all. Both cases are recorded in `deferred-work.md` and not fixed here.

## Verification

**Commands:**
- `uv run --with pytest --with pyyaml pytest -q skills/orch-gate/scripts/tests` -- expected: all pass.
- `git status --porcelain _bmad` -- expected: empty.

## Implementation Notes

- Render test copies `_bmad/config.user.toml` too (or writes a one-line stand-in): the renderer needs `communication_language`, which lives in the user layer.
- `on_complete` step 1 asks the builder to commit leftover changes with the user before the marker, since the marker pins HEAD.
- Added `test_template_covers_failure_paths` so every I/O matrix row has a covering assertion; suite: 151 passed, 0 skipped.

## Spec Change Log

## Review Triage Log

- medium — stale/mismatched node context (prepend fallback, on_complete key read): `.orch/context/` is untracked and survives branch switches; no story.key vs branch check → patch.
- medium — context `## Finish` (work.py:276-281) says fix-and-push, conflicting with on_complete stop/offer → patch (fact states precedence).
- medium — template literals (marker path, context paths, `story.key`/`base_branch`) only self-checked in tests; drift in orch.py undetected → patch (verification-gap).
- low — `not-story-branch`/`story-not-found` routed to a `--coord` request → patch.
- low — exit codes outside 0/1/2 (uv missing) unhandled in context/marker/gate → patch.
- low — dirty tree after user declines; commits outside allowed_write → patch.
- low — unchanged-marker detection and failed marker commit unhandled → patch.
- low — absolute-path test only covers `uv run \S+`; redundant asserts → patch.
- low — render test raises bare FileNotFoundError on missing stock step → patch.
- false — "gate/marker ignore base_branch": `_gate_base` (orch.py:340-351) uses the registry branch for code repos and prefers `origin/<branch>`; passing raw `--base <base_branch>` would drop the origin preference.
- false — render test should use `uv run`: render_skill.py's PEP 723 block has no dependencies, `sys.executable` is equivalent.
- false — persistent_facts not checked in step files: facts render only into workflow.md, which the test checks.
- false — `{skill-root}` in header comment: comments are not rendered values and orch-setup substitutes only `@ORCH_CLI@`.
- low (rejected) — branch-gating test passes on a mention: prose behavior is LLM-executed; a stronger check needs parsing prose.
- low (rejected) — `_find_up` may walk past the repo root: installed copies need the upward walk; an unrelated `_bmad` above the project is unlikely.
- defer — stock bmad-build spec/sprint-status in polyrepo code repos fail gate scope (already a known limit in Design Notes).
- defer — orch-setup merge rules untested (on_complete combination, prepend ordering, `.user.toml` override detection): belongs to the orch-setup build.
