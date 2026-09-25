# Analysis Report: skills/orch-gate

Generated: 2026-09-25 · Schema: 2

**Grade: Fair**

> Critical issues from the first run are gone and the explain-and-fix loop is solid, but the gate still fails open on bad setup: an empty or malformed registry passes silently (determinism-2, enhancement-1), and directory contracts are compared as git tree listings (determinism-1).

The script/prompt boundary is right and SKILL.md stays lean at ~1.7k tokens: the model explains verdicts, never decides them, and all 47 tests pass with ref-based config, merge-ref head resolution and stable finding codes in place. The remaining risk is in script edge cases that quietly weaken the gate: registry and sprint-status inputs that parse wrongly or not at all, plus fix commands that do not carry the context they were computed with.

| Severity | Count |
| --- | --- |
| Critical | 0 |
| High | 3 |
| Medium | 10 |
| Low | 11 |

## Themes

### 1. Fail-open on bad or missing setup inputs

- Root cause: The gate treats an unreadable, empty, malformed or mis-shaped registry (and loose allowed_write patterns) as 'nothing protected' instead of an error, so setup and CI-wiring mistakes, which are the most likely early failures, produce a green gate.
- Fix: Add a setup/registry-invalid precondition in gate.run: run registry.validate at the trusted ref, fail on any issue, empty registry, no-epics, or unmatched repo_id; never default required fields permissively; define and validate wildcard-free allowed_write semantics. Cover each case with a test.
- Findings:
  - `determinism-2` The gate ignores registry problems, and a malformed entry fails open — `scripts/orch.py:cmd_gate; orchlib/registry.py:load; gate.py scope`
  - `enhancement-1` Add: fail closed when the gate has nothing to enforce (empty/unreadable registry, or this repo matches no subproject) — `scripts/orchlib/gate.py:run no-story branch; scripts/orch.py:cmd_gate`
  - `determinism-6` An allowed_write pattern without a wildcard matches only the exact path, so no-story protection fails open — `scripts/orchlib/globs.py:compile_glob; gate.py no-story scope; registry.py:validate`

### 2. Inputs parsed by the wrong primitive

- Root cause: Some checks read content with a primitive that does not match its shape: git show on directories, regex over YAML, a dict keyed by subproject, and an archive regex that ignores the epic number. Each gives a wrong or order-dependent answer for ordinary inputs.
- Fix: Make Tree.read blob-only (compare directories per file), take sprint-status semantics from yaml.safe_load, map consumers to dependency sets, and check the archive epic number; add a regression test for each.
- Findings:
  - `determinism-1` Directory canonicals and copies are compared as `git show` tree listings — `scripts/orchlib/gitio.py:Tree.read; gate.py conformance; detectors.py:run`
  - `determinism-3` Regex parsing of sprint-status misses `done` entries, so the done-without-marker check can be bypassed — `scripts/orchlib/sprint_status.py:split/ENTRY_RE/check`
  - `determinism-4` The narrow-story consumer check keeps only one dependency per consumer, and which one depends on list order — `scripts/orchlib/gate.py:252 (dep_subs)`
  - `determinism-11` The archive's epic number is never checked against the story's epic — `scripts/orchlib/markers.py:close_check/is_archive_move`

### 3. fix.command is not self-contained

- Root cause: Mechanical fixes are emitted as a bare `orch.py …` without the uv run path or the --coord/--coord-ref/--repo/--base the verdict used, and the text view the agent reads cannot tell mechanical fixes from hints.
- Fix: Build the full runnable argv (uv run <abs orch.py> + args + gate context flags) in gate.fix, and have SKILL.md run `gate --format text -o <tmp>.json` so the agent reasons from JSON.
- Findings:
  - `enhancement-2` Add: make fix.command runnable as printed and carry the gate's invocation context — `scripts/orchlib/gate.py:fix(), _fix_line, render_text, render_markdown`
  - `architecture-2` fix.command leaves out the repository flags the gate ran with, so the fix-and-rerun loop can act on the wrong coordination repo — `scripts/orchlib/gate.py:fix() (lines 45-46); SKILL.md Running the gate (line 24)`
  - `determinism-8` fix.command drops the context flags the verdict was computed with — `scripts/orchlib/gate.py:fix; SKILL.md`
  - `architecture-1` The agent reads the text view, but the fix offer and the CI comparison need JSON-only fields — `SKILL.md: Running the gate (lines 20, 24)`

### 4. Unrecorded or environment-dependent verdict inputs

- Root cause: Some inputs that decide the verdict depend on the environment or are not recorded in the result: the registry-repo SHAs, a default base taken from the coordination repo's branch, the origin/HEAD bootstrap and a project_name taken from the directory. A local run and a CI run can therefore differ without any field showing why.
- Fix: Record repos_read shas and ref/base sources in the result, resolve the default base from the registry branch, make the bootstrap order environment-independent, and add a --compare mode that diffs two results.
- Findings:
  - `determinism-5` The result omits the registry-repo SHAs that decide the cross-repo checks — `scripts/orchlib/markers.py:merged/fetch_tree; gate.py result; SKILL.md Running the gate`
  - `determinism-7` The default base for a code repo uses the coordination repo's main branch, not the subproject's registry branch — `scripts/orch.py:cmd_gate (default_base with env.cfg.main_branch)`
  - `determinism-10` How the default ref and project_name are found depends on the environment — `scripts/orchlib/config.py:resolve/load`
  - `determinism-9` The prompt compares two gate results field by field — `SKILL.md: Running the gate`

### 5. Config layers and SKILL.md drift

- Root cause: The config layers and some SKILL.md wording no longer match what the code does. Personal layers are read at the ref, and a tracked one reaches the verdict. The user setting has nowhere to live. Some prose is addressed to other skills.
- Fix: Drop *.user.toml from verdict layers and resolve user settings separately; then tighten SKILL.md: define {planning_artifacts}/{implementation_artifacts}, fix the monorepo-only registry sentence and the language layers, and trim caller-side library/override prose.
- Findings:
  - `customization-1` Personal-layer TOML files are read at the git ref, so committed ones reach the verdict and SKILL.md's claim is false — `scripts/orchlib/config.py:LAYERS; SKILL.md Gotchas`
  - `customization-2` The declared user setting orch_worktrees_dir cannot be overridden personally through the shared `orch.py config` — `scripts/orchlib/config.py:load/resolve; orch.py config`
  - `customization-3` The communication_language rule skips the custom override layers — `SKILL.md: Resolution rules`
  - `architecture-3` The library contract sits in a file its consumers don't load — `SKILL.md: Library for other orch skills (line 54)`
  - `architecture-4` `{planning_artifacts}` and `{implementation_artifacts}` appear in SKILL.md but the resolution rules don't define them — `SKILL.md: The checks, Story metadata, Resolution rules`
  - `architecture-5` "Registry is read at the base ref" holds only for monorepos — `SKILL.md: Running the gate (line 22)`
  - `leanness-1` Library section is mostly addressed to other skills' authors, not this skill's reader — `SKILL.md: Library for other orch skills (line 54)`
  - `leanness-2` Story-metadata section contains a directive aimed at another skill's override — `SKILL.md: Story metadata (line 42)`

## Strengths

- The prompt only explains; every verdict comes from the script, with stable finding codes and exit codes that separate verdict (1) from error (2).
- Config, registry and epics are read at a trusted git ref, and merge-ref heads are resolved to the PR tip, so the previous critical issues are closed and covered by tests.
- SKILL.md is lean (~1.7k tokens, no waste patterns) and states the story-metadata contract explicitly.
- Fail-closed handling for unread repos, missing detectors on changed contracts, --offline in CI and shallow clones, with specific messages.
- 47 pytest cases including polyrepo via fetch cache, remote claim race and a real git merge through the driver.

## Recommendations

1. Add a registry/setup precondition to the gate that fails on validate issues, empty registry, no epics or unmatched repo_id, and define wildcard-free allowed_write semantics. (resolves: determinism-2, enhancement-1, determinism-6)
2. Emit full runnable fix.command argv with gate context flags and have SKILL.md read the JSON via -o. (resolves: enhancement-2, architecture-2, determinism-8, architecture-1)
3. Fix the parsing primitives: blob-only Tree.read (per-file directory compare), YAML-based sprint-status check, dependency sets per consumer, and an archive epic check. (resolves: determinism-1, determinism-3, determinism-4, determinism-11)
4. Drop *.user.toml from verdict layers and split out user settings; tidy SKILL.md resolution rules and caller-side prose. (resolves: customization-1, customization-2, customization-3, architecture-3, architecture-4, architecture-5, leanness-1, leanness-2)
5. Record repos_read shas and base/ref sources, resolve default base from the registry branch, and add a result --compare mode. (resolves: determinism-5, determinism-7, determinism-10, determinism-9)
6. Decide pre-push: add it to orch-setup or record it as post-v1 in the plan. (resolves: enhancement-3)

## Experience

- **Developer checks a PR locally** — Commit, run `gate --format text`, read the verdict, apply a mechanical fix.command, commit, re-run until PASS. The fix command is not runnable as printed in polyrepo runs (enhancement-2).
- **Developer asks why CI failed** — Provide the CI JSON/log, the agent explains per stable code, and compares input SHAs if a local run differs. Registry-repo SHAs are missing from that comparison (determinism-5).
- **First-time adopter wires CI** — Adds the workflow before the registry or coordination checkout is right, and the gate reports PASS instead of flagging the setup (enhancement-1).
- Headless: Fully headless: orch.py gate is script-only with JSON/markdown output and exit codes, and the LLM layer is only needed for explanations.

## Findings

### High (3)

#### determinism-1 — Directory canonicals and copies are compared as `git show` tree listings

- Lens: determinism
- Location: `scripts/orchlib/gitio.py:Tree.read; gate.py conformance; detectors.py:run`
- Evidence: Tree.read returns `git show ref:path` stdout for any object, so a directory yields a tree listing whose header contains the ref name. Directory exports always report copy-drift (and miss real content drift); openapi/protobuf/asyncapi detectors get listings, error, and count as breaking, so a protobuf package directory fails every contract PR.
- Recommendation: Make Tree.read return None/raise for non-blobs (cat-file -t); require blob canonicals for file adapters in registry.validate, or compare directories file-by-file and materialize them for detectors. Test identical and drifted directory exports.

#### determinism-2 — The gate ignores registry problems, and a malformed entry fails open

- Lens: determinism
- Location: `scripts/orch.py:cmd_gate; orchlib/registry.py:load; gate.py scope`
- Evidence: cmd_gate never inspects reg.issues or runs registry.validate. load() skips YAML-error files, defaults missing repo to '.', and turns non-list allowed_write into [], removing the subproject from `protected`, so a markerless PR into it passes. The writes-contracts overlap is only an issue, never a failure.
- Recommendation: Run registry.validate at the trusted ref inside the gate and fail a `registry-invalid` check with a stable code; never default required fields to permissive values. Test: broken registry file + no-story PR into that subproject fails.

#### enhancement-1 — Add: fail closed when the gate has nothing to enforce (empty/unreadable registry, or this repo matches no subproject)

- Lens: enhancement
- Location: `scripts/orchlib/gate.py:run no-story branch; scripts/orch.py:cmd_gate`
- Evidence: Reproduced: a fresh repo with no _bmad config, registry or epics, committing services/pay/a.py, gets `PASS - non-story change`, exit 0. Same silent pass for a missing/wrong coordination checkout in CI, a forgotten --coord, or an origin URL that does not match the registry's `repo`.
- Recommendation: Add a `setup` precondition: fail (stable code) when the registry is empty, reg.issues is non-empty, stories report no-epics, or (polyrepo) repo_id matches no subproject. Name the fix (orch-setup, --coord, --repo-id). Add a Gotchas line and tests.

### Medium (10)

#### customization-1 — Personal-layer TOML files are read at the git ref, so committed ones reach the verdict and SKILL.md's claim is false

- Lens: customization
- Location: `scripts/orchlib/config.py:LAYERS; SKILL.md Gotchas`
- Evidence: LAYERS includes _bmad/config.user.toml and _bmad/custom/config.user.toml. Only the custom one is gitignored; _bmad/config.user.toml is tracked in this repo (verified with git ls-files), so its keys set scope/paths for everyone, contradicting '`*.user.toml` never affects the verdict'.
- Recommendation: Drop both *.user.toml entries from the verdict layers; read only _bmad/config.toml and _bmad/custom/config.toml at the ref.

#### customization-2 — The declared user setting orch_worktrees_dir cannot be overridden personally through the shared `orch.py config`

- Lens: customization
- Location: `scripts/orchlib/config.py:load/resolve; orch.py config`
- Evidence: The plan marks orch_worktrees_dir as a user setting, but the library reads only committed layers at a ref, so a value in gitignored _bmad/custom/config.user.toml is never seen and silently falls back to the default.
- Recommendation: Split verdict config (ref-read team layers) from local-convenience keys resolved from working-tree layers incl. user layers and never fed to gate.run; or drop the key from the gate library and let orch-next read it.

#### architecture-1 — The agent reads the text view, but the fix offer and the CI comparison need JSON-only fields

- Lens: architecture
- Location: `SKILL.md: Running the gate (lines 20, 24)`
- Evidence: SKILL.md runs `gate --format text`, then says to offer fix.command only when fix.mechanical is set and to compare base_sha/head_sha/coord_sha. The text renderer prints mechanical fixes and free-text hints identically as `fix:` and prints no SHAs.
- Recommendation: Run `gate --format text -o <tmp>.json` and reason from the JSON file; state in one clause that the fix offer and local-vs-CI comparison read the JSON.

#### enhancement-2 — Add: make fix.command runnable as printed and carry the gate's invocation context

- Lens: enhancement
- Location: `scripts/orchlib/gate.py:fix(), _fix_line, render_text, render_markdown`
- Evidence: fix() builds ['orch.py', *args], rendered as `run: orch.py marker write --story 1-2`: not runnable (not on PATH, no uv run), and missing the gate's --coord/--repo/--base, so in polyrepo or contract-story runs the fix resolves a different coordination repo or base and the fix-rerun loop stalls.
- Recommendation: Emit the full argv (uv run <abs orch.py> + args + the gate's explicit --coord/--coord-ref/--repo/--base); keep it a list for sibling skills. Test that a polyrepo run with --coord renders a fix line containing --coord.

#### architecture-2 — fix.command leaves out the repository flags the gate ran with, so the fix-and-rerun loop can act on the wrong coordination repo

- Lens: architecture
- Location: `scripts/orchlib/gate.py:fix() (lines 45-46); SKILL.md Running the gate (line 24)`
- Evidence: In a polyrepo run started with --coord PATH and no ORCH_COORD/local orch_coordination_repo, `marker write` resolves the coordination repo to the current repo, exits 2 or computes pins against the wrong registry; the re-run then shows the same failure.
- Recommendation: Same fix as enhancement-2: gate.run appends the resolved --coord/--coord-ref (and --base for contract stories) to every fix.command.

#### determinism-3 — Regex parsing of sprint-status misses `done` entries, so the done-without-marker check can be bypassed

- Lens: determinism
- Location: `scripts/orchlib/sprint_status.py:split/ENTRY_RE/check`
- Evidence: split() ends the block at the first non-indented line, so a column-0 comment truncates it; ENTRY_RE rejects quoted statuses. Verified: a block with `# Epic 2`, `2-1-b: done` and `1-2-c: "done"` yields no failures although both stories are unmerged.
- Recommendation: Take check semantics from yaml.safe_load(...)['development_status']; keep line-level parsing only for comment-preserving writes (derive, merge driver); fail when the two disagree. Test column-0 comment and quoted status.

#### determinism-4 — The narrow-story consumer check keeps only one dependency per consumer, and which one depends on list order

- Lens: determinism
- Location: `scripts/orchlib/gate.py:252 (dep_subs)`
- Evidence: dep_subs = {subproject: key for d in depends_on} overwrites earlier deps on the same consumer. With 2.1 merged and 2.3 unmerged in consumer X, `Depends on: 2.1, 2.3` fails but `2.3, 2.1` passes.
- Recommendation: Map each consumer to a set of dependency keys and state the rule (all merged, or any merged) explicitly; unit-test that reordering Depends on keeps the verdict.

#### determinism-5 — The result omits the registry-repo SHAs that decide the cross-repo checks

- Lens: determinism
- Location: `scripts/orchlib/markers.py:merged/fetch_tree; gate.py result; SKILL.md Running the gate`
- Evidence: merged() fetches each registry repo's branch live; those trees decide done-without-marker, consumers-not-migrated and epic-close-failed, but the result records no sha per repo, so a local/CI difference caused by a moved code-repo main is invisible in the fields SKILL.md tells the model to compare.
- Recommendation: Emit repos_read: [{repo, branch, sha}] (rev-parse of the fetched ref) and add it to SKILL.md's comparison list.

#### determinism-6 — An allowed_write pattern without a wildcard matches only the exact path, so no-story protection fails open

- Lens: determinism
- Location: `scripts/orchlib/globs.py:compile_glob; gate.py no-story scope; registry.py:validate`
- Evidence: `services/pay` compiles to ^services/pay$, so services/pay/x.py is unprotected for markerless PRs and out of scope for story PRs; validate does not warn.
- Recommendation: Either treat a wildcard-free directory pattern as covering its subtree, or have validate reject wildcard-free patterns without a trailing '/'; consider protecting sub.path/** for no-story PRs. Add a test.

#### determinism-7 — The default base for a code repo uses the coordination repo's main branch, not the subproject's registry branch

- Lens: determinism
- Location: `scripts/orch.py:cmd_gate (default_base with env.cfg.main_branch)`
- Evidence: In a polyrepo, omitting --base diffs a code repo against origin/<coord orch_main_branch> while merged() reads it at the registry `branch`; a repo whose branch is `develop` gets a different local verdict than CI with an explicit --base.
- Recommendation: Resolve the default base from the matching subproject's registry branch, fall back to orch_main_branch, and record the source in the result.

### Low (11)

#### leanness-1 — Library section is mostly addressed to other skills' authors, not this skill's reader

- Lens: leanness
- Location: `SKILL.md: Library for other orch skills (line 54)`
- Evidence: 'Every other orch skill calls this CLI…' and the <calling skill's directory>/../orch-gate/scripts/orch.py pattern are caller-side mechanics that sibling models never read here; only the exit-code split, --format markdown and deps --probe change this reader's moves.
- Recommendation: Rename to 'CLI behavior', keep only what the gate runner acts on, and move the invocation pattern into each calling skill or the module plan.
- Proposed smallest: ## CLI behavior

Output is JSON on stdout. Exit 1 = failing verdict, lost race, validation issues, missing detector, or `epic close-check` problems; exit 2 = usage, environment or internal error — report it, never explain it as a verdict. `orch.py --help` lists commands; `gate --format markdown` renders a CI step summary; `deps --probe` checks installed detectors against built-in fixtures.
- Predicted delta: No change for orch-gate runs; sibling skills must carry their own invocation line anyway. Route to variant eval to confirm.

#### architecture-3 — The library contract sits in a file its consumers don't load

- Lens: architecture
- Location: `SKILL.md: Library for other orch skills (line 54)`
- Evidence: orch-setup/next/status run from their own SKILL.md and never load this one, so the calling convention only reaches them if copied; every gate run pays for the paragraph.
- Recommendation: Keep only the exit-code line here; put the calling convention in orch.py --help and the module plan's shared-library section, and stamp it into consuming skills.

#### leanness-2 — Story-metadata section contains a directive aimed at another skill's override

- Lens: leanness
- Location: `SKILL.md: Story metadata (line 42)`
- Evidence: 'The orch override for bmad-create-epics-and-stories must emit exactly this' instructs a different artifact and does not change how the gate runner explains a marker/Subproject finding.
- Recommendation: Trim the lead-in to 'The gate reads these bold labels under each `### Story N.M: Title` heading in {planning_artifacts}/epic*.md:' and put the must-emit obligation in the epics override.

#### architecture-4 — `{planning_artifacts}` and `{implementation_artifacts}` appear in SKILL.md but the resolution rules don't define them

- Lens: architecture
- Location: `SKILL.md: The checks, Story metadata, Resolution rules`
- Evidence: Resolution rules cover only bare paths, {project-root} and {communication_language}; the model may guess the working-tree _bmad config, which the gate deliberately does not read.
- Recommendation: Add one rule: these resolve to the values in `orch.py config` output (read at coordination main).

#### architecture-5 — "Registry is read at the base ref" holds only for monorepos

- Lens: architecture
- Location: `SKILL.md: Running the gate (line 22)`
- Evidence: In a polyrepo the registry is read at the coordination main ref (falling back to HEAD); the Gotchas section says so correctly, so the two sections contradict.
- Recommendation: Say 'at the base ref in a monorepo, at coordination main otherwise', or drop the line and rely on Gotchas.

#### customization-3 — The communication_language rule skips the custom override layers

- Lens: customization
- Location: `SKILL.md: Resolution rules`
- Evidence: Only _bmad/config.user.toml and _bmad/config.toml are consulted; the documented durable override files under _bmad/custom/ are ignored for explanations (verdict unaffected).
- Recommendation: Resolve from _bmad/custom/config.user.toml, _bmad/config.user.toml, _bmad/custom/config.toml, _bmad/config.toml, else the user's language.

#### determinism-8 — fix.command drops the context flags the verdict was computed with

- Lens: determinism
- Location: `scripts/orchlib/gate.py:fix; SKILL.md`
- Evidence: fix() returns ['orch.py', *command] without --coord/--coord-ref/--base/--repo or the uv run path, so the model must rebuild the invocation.
- Recommendation: Put the full argv in fix.command (script path + context flags); SKILL.md then only says 'run fix.command'.

#### determinism-9 — The prompt compares two gate results field by field

- Lens: determinism
- Location: `SKILL.md: Running the gate`
- Evidence: 'compare base_sha, head_sha, coord_sha, notices and unread_repos in the two results' is a structured diff with one correct answer.
- Recommendation: Add `orch.py gate --compare <ci.json>` (or diff-results) that emits only differing input fields; the prompt explains that output.

#### determinism-10 — How the default ref and project_name are found depends on the environment

- Lens: determinism
- Location: `scripts/orchlib/config.py:resolve/load`
- Evidence: resolve() starts from origin/HEAD, which actions/checkout lacks, so repos whose default branch is not main work locally but exit 2 in CI without --base; project_name falls back to the checkout directory name, which differs between a local clone and a CI `path: coord` checkout.
- Recommendation: Use one bootstrap order everywhere or record coord_ref_source; fail clearly (or use repo identity) when project_name is unset.

#### determinism-11 — The archive's epic number is never checked against the story's epic

- Lens: determinism
- Location: `scripts/orchlib/markers.py:close_check/is_archive_move`
- Evidence: Both accept a move into any .orch/archive/epic-N/, so a marker archived under the wrong epic passes the close check despite its message.
- Recommendation: Compare ARCHIVE_RE group(1) with the story's epic in both functions; add a wrong-epic test.

#### enhancement-3 — Opportunity: give the pre-push entry point an owner

- Lens: enhancement
- Location: `skills/reports/orch-module-plan.md (Setup Extensions)`
- Evidence: The plan promises 'CI and optionally pre-push' but no setup extension installs a hook and no decision is recorded (raised before as enhancement-9).
- Recommendation: Either add a pre-push hook step to orch-setup (runs `orch.py gate --format text`, exit code blocks push) or record pre-push as post-v1 in the plan.
