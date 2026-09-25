# Analysis Report: skills/orch-gate

Generated: 2026-09-25 · Schema: 2

**Grade: Poor**

> Well-placed script/prompt boundary and a lean SKILL.md, but the core promise 'same verdict locally and in CI' is broken: a CI merge-ref checkout makes the contract-freshness check fail open (determinism-1), and config is read from the PR's working tree, including gitignored personal layers (customization-1).

Every verdict rule lives in scripts/orch.py. The SKILL.md is a lean explain-and-fix layer that never judges the verdict itself, and the CLI is ready to run headless in CI. The main gap is input integrity. Checkout shape, working-tree config, ref freshness, network credentials and detector versions can all change the verdict, and the output does not record any of them. Two of these problems are fail-open holes in the boundary the gate exists to enforce.

| Severity | Count |
| --- | --- |
| Critical | 2 |
| High | 7 |
| Medium | 14 |
| Low | 10 |

## Themes

### 1. Verdict inputs escape git refs

- Root cause: The gate is designed to read everything through git refs, but some inputs come from elsewhere. Config comes from the working tree, including *.user.toml files that exist only locally. The atlas detector lints the checked-out directory. Git calls inherit GIT_* environment variables and quotePath. is_ci treats CI=false as CI. As a result a PR can repoint its own boundaries, and a local run cannot reproduce CI.
- Fix: Read every verdict-bearing input through gitio.Tree at the base or coordination ref. That covers the committed _bmad/config.toml and _bmad/custom/config.toml only (no *.user.toml) and the atlas migrations directory, which should be materialized from the head ref. Sanitize the git subprocess env (drop GIT_DIR, GIT_WORK_TREE, GIT_INDEX_FILE and GIT_OBJECT_DIRECTORY, set LC_ALL=C, pass -c core.quotePath=false). Parse CI strictly. Accept a single spelling for orch keys. Add a test in which a PR edits _bmad/custom/config.toml and the gate still uses the base-ref values.
- Findings:
  - `customization-1` Gate config comes from the PR's own working tree, so a PR can move its own boundaries — `scripts/orchlib/config.py:load; scripts/orch.py:Env.__init__`
  - `customization-2` Gitignored personal config layers can change the verdict locally but not in CI — `scripts/orchlib/config.py:LAYERS`
  - `determinism-2` Config is read from the working tree, including gitignored personal layers — `scripts/orchlib/config.py:12,65-91`
  - `determinism-6` The db-schema detector reads the working tree, not the PR head ref — `scripts/orchlib/detectors.py:62-63,85-90`
  - `determinism-9` is_ci treats any non-empty CI value as true — `scripts/orchlib/gate.py:259-260`
  - `determinism-10` Git calls inherit the user's git config and GIT_* environment — `scripts/orchlib/gitio.py:16-26,80-84`
  - `customization-5` orch keys accepted both with and without the orch_ prefix — `scripts/orchlib/config.py:load`

### 2. Checkout shape and ref state decide the verdict

- Root cause: The gate assumes head is the branch tip with full history and a fresh base. A GitHub or GitLab merge-ref checkout makes merge-base equal the base tip, so the 'branched from the latest contract' check can never fire. A shallow clone crashes with an empty message. Stale origin/main and uncommitted work silently change local results. The result does not record the SHAs that were actually used.
- Fix: Normalize head: when it is a merge commit whose first parent is base, gate the second parent, or exit 2 and ask for --head. Detect a shallow clone and name the fix (fetch-depth: 0). Emit base_sha, head_sha, merge_base, coord_sha and a dirty_worktree warning in every result. Add a test that the branch tip and the merge ref give identical verdicts.
- Findings:
  - `determinism-1` Verdict flips when head is the PR merge ref (the usual CI checkout) instead of the branch tip — `scripts/orchlib/gate.py:62-64,140-148; scripts/orch.py:254`
  - `determinism-3` Local ref freshness and uncommitted state change the verdict, and the output does not record either — `scripts/orchlib/gitio.py:41-45; scripts/orch.py:36-43,184-186; gate.py:69-71`
  - `enhancement-2` Add: actionable error for shallow CI checkouts (no merge-base) — `scripts/orchlib/gitio.py: merge_base / git(); SKILL.md: Gotchas`
  - `enhancement-3` Add: warn about uncommitted work before a local run — `SKILL.md: Gotchas; scripts/orchlib/gate.py: run`

### 3. 'Could not verify' is reported as 'verified false'

- Root cause: Cross-repo read failures are swallowed by a broad except, and some checks drop the warnings, so a missing credential shows up as 'consumers not migrated' or 'done-without-marker'. Unexpected exceptions exit 1, which callers read as a failing verdict. close-check and deps exit 1 on warnings too, and the documentation does not say so.
- Fix: Add a distinct 'repo-unreadable' finding code and pass merged() warnings through to every check that uses them. Record offline mode in the result and reject --offline in CI. Catch all unexpected exceptions in main and return exit 2 with JSON. Document the exit-1-on-warning cases for close-check and deps.
- Findings:
  - `determinism-4` Cross-repo read failures and --offline silently change the verdict, and the failure message blames the wrong cause — `scripts/orchlib/markers.py:111-130; gate.py:186-213,227-229`
  - `determinism-8` Unexpected exceptions exit 1, which callers read as a failing verdict — `scripts/orch.py:267-277; config.py:64; gitio.py:66-68`
  - `architecture-4` The exit-code summary does not cover the warning-only failures of epic close-check and deps — `SKILL.md: Library; scripts/orch.py cmd_epic, cmd_deps`

### 4. Coordination repo locator and caller wiring are unresolved

- Root cause: orch_coordination_repo is resolved but never read, orch_main_branch is not in the plan, and nothing tells other orch skills where orch-gate's orch.py is installed. Locating things is left to the user or the LLM even where config already has the answer.
- Fix: Choose a single locator precedence (--coord > ORCH_COORD > committed orch_coordination_repo > '.'), or drop the key. Add orch_main_branch to the plan's config table or remove it. Publish one canonical CLI path for callers, for example an orch_cli config key written by orch-setup. List every input that affects the verdict in one short SKILL.md note.
- Findings:
  - `customization-3` orch_coordination_repo is declared and resolved but does nothing — `scripts/orchlib/config.py:DEFAULTS; scripts/orch.py:Env.__init__`
  - `customization-4` orch_main_branch is a config key that the module plan does not list — `scripts/orchlib/config.py:DEFAULTS; registry.py:131; orch.py:38,109,185`
  - `determinism-11` Finding the coordination repo is left to the user or LLM even though config has the answer — `scripts/orch.py:33-34; config.py:6; SKILL.md: Running the gate`
  - `architecture-1` Other orch skills are told to call a bare path they cannot resolve — `SKILL.md: Library for other orch skills`
  - `customization-6` Where config comes from and which env vars affect the verdict is not documented in one place — `SKILL.md: Running the gate / Library / Gotchas`

### 5. Explain/fix layer relies on free text and local re-runs

- Root cause: The model maps findings to fixes by reading free-text hints. It always re-runs the gate locally instead of explaining the CI JSON, and it does not re-run after applying a fix. A conformance failure carries no diff.
- Fix: Give every gate finding a stable code and a structured fix object ({mechanical, command, precondition}), and attach a truncated diff to conformance failures. Then have SKILL.md explain the CI JSON when the user has it, offer fix.command when a fix is mechanical, commit the result and re-run the gate. Add --format markdown for CI step summaries.
- Findings:
  - `determinism-7` The LLM has to classify fixable findings from free-text hints — `SKILL.md: Running the gate; scripts/orchlib/gate.py Check.fail/warn`
  - `enhancement-1` Add: explain a CI failure from its gate JSON instead of always re-running locally — `SKILL.md: Running the gate`
  - `enhancement-4` Add: re-run the gate after applying a mechanical fix — `SKILL.md: Running the gate (mechanical fixes list)`
  - `enhancement-5` Add: show the conformance diff (the plan brief promises 'pass/fail with diff') — `scripts/orchlib/gate.py: conformance check`
  - `enhancement-6` Add: a CI report format (plan: 'optional HTML report for CI artifacts') — `scripts/orch.py: gate --format; gate.py: render_text`

## Strengths

- The intelligence boundary is right. Every verdict rule is in scripts/orch.py, and the SKILL.md explicitly forbids deciding or bypassing the verdict. The CODEOWNERS escape hatch explains why.
- SKILL.md is lean and outcome-driven: 1557 tokens, no all-caps directives, a Rule/Why table per check, and non-inferable gotchas.
- The CLI contract suits headless use: JSON on stdout, exit codes 0/1/2, and -o for artifacts. CI needs no LLM.
- Good test coverage for a first build: 32 pytest cases, including polyrepo via the fetch cache, a remote claim race and a real git merge through the driver.
- Fail-closed defaults: a missing detector fails when a contract of that type changes, and markers may only be moved unchanged into the archive.
- The pre-pass flags (missing Overview/On Activation sections, .memlog.md at the root) are false positives for a script-first utility and the builder's memlog location. Do not 'fix' them.

## Recommendations

1. Normalize head for merge-ref checkouts and add the branch-tip vs merge-ref parity test. This closes the fail-open contract-freshness hole in CI. (resolves: determinism-1)
2. Load config through gitio.Tree at the base or coordination ref, from committed layers only, and record the layer SHAs in the result. (resolves: customization-1, customization-2, determinism-2)
3. Emit the resolved SHAs, a dirty-worktree warning and a shallow-clone diagnosis in every gate result. (resolves: determinism-3, enhancement-2, enhancement-3)
4. Separate 'repo-unreadable' from real failures, catch unexpected exceptions as exit 2, and document the exit-1-on-warning cases. (resolves: determinism-4, determinism-8, architecture-4)
5. Add stable finding codes and structured fix objects, then rewrite SKILL.md's fix list to: use the CI JSON if you have it, apply fix.command, commit, re-run. (resolves: determinism-7, enhancement-1, enhancement-4, enhancement-5)
6. Settle the coordination-repo locator and the caller CLI path, and add orch_main_branch to the plan or drop it. (resolves: customization-3, customization-4, determinism-11, architecture-1, customization-6)
7. Trim SKILL.md: shrink the library table to the exit-code line, drop {project-root}, dedupe the no-bypass rule, and add a {communication_language} source. (resolves: leanness-1, leanness-2, leanness-3, architecture-2, architecture-3)

## Experience

- **First-timer** — Runs 'run orch gate' on a fresh clone and gets a merge-driver FAIL. install-driver fixes it, but nothing prompts a re-run to confirm (enhancement-4).
- **Expert** — Runs orch.py gate directly and gets clean JSON with a hint per finding. Well served.
- **Wrong intent** — Asks 'why did CI fail' with a CI log in hand. The skill re-runs locally and may explain a different verdict (enhancement-1, determinism-1).
- **Unexpected but valid input** — A contract story that is not rebased gets a clear pins FAIL, but only on the branch tip. A conformance failure says the files differ, not what differs (enhancement-5).
- **Hostile environment** — A shallow CI checkout exits 2 with an empty message (enhancement-2). An unverified asyncapi/atlas binary may misclassify with nothing to catch it (enhancement-7). Unreadable sibling repos show up as story failures (determinism-4).
- **Automator** — CI runs 'uv run orch.py gate -o gate.json' and fails the job on exit 1. The missing piece is a human-readable CI summary (enhancement-6).
- Headless: Headless-ready: CI runs 'uv run scripts/orch.py gate -o gate.json' with no LLM. Only the explain/fix layer is interactive, and it should take that JSON as input.

## Findings

### Critical (2)

#### determinism-1 — Verdict flips when head is the PR merge ref (the usual CI checkout) instead of the branch tip

- Lens: determinism
- Location: `scripts/orchlib/gate.py:62-64,140-148; scripts/orch.py:254`
- Evidence: With a refs/pull/N/merge checkout, merge_base(base, head) equals the base tip, so the 'contract changed on main since the branch started' check never fires. The lens reproduced this from the existing test fixture: on the branch tip the gate exits 1 with pins=fail, and on the merge commit it exits 0 with pins=pass. This fails open in CI.
- Recommendation: When head is a merge commit whose first parent is base, gate the second parent and report head_resolved, or exit 2 and ask for --head. Add a pytest that asserts the branch tip and the merge ref give identical verdicts, and state in --help that fetch-depth 0 is required.

#### customization-1 — Gate config comes from the PR's own working tree, so a PR can move its own boundaries

- Lens: customization
- Location: `scripts/orchlib/config.py:load; scripts/orch.py:Env.__init__`
- Evidence: config.load opens _bmad/*.toml from the filesystem, while the registry, epics and markers are read at the base ref. A no-marker PR may edit _bmad/custom/config.toml (it is outside every allowed_write) and repoint orch_registry_dir or orch_contracts_dir, and the gate then uses those boundaries. Verified: load() uses Path.open.
- Recommendation: Read the config layers via gitio.Tree at the base or coordination ref, or protect the _bmad config files so only a registry PR can change them. Add a test where a PR edits the config and the gate still uses the base values.

### High (7)

#### customization-2 — Gitignored personal config layers can change the verdict locally but not in CI

- Lens: customization
- Location: `scripts/orchlib/config.py:LAYERS`
- Evidence: LAYERS includes _bmad/config.user.toml and _bmad/custom/config.user.toml, and both are gitignored. A personal override of any orch_* or bmm path key changes the local verdict only, which contradicts the 'identical in CI and locally' decision.
- Recommendation: Read only the committed layers for verdict-affecting keys. If personal layers stay for non-verdict keys, list which keys they may set.

#### customization-3 — orch_coordination_repo is declared and resolved but does nothing

- Lens: customization
- Location: `scripts/orchlib/config.py:DEFAULTS; scripts/orch.py:Env.__init__`
- Evidence: orch-setup will collect this key, but nothing reads cfg.coordination_repo. The coordination repo is located only by --coord or ORCH_COORD. The key is also circular, because it is read from the coordination repo itself.
- Recommendation: Either honor it from the acting repo's committed config (precedence flag > env > config > '.') or remove it from DEFAULTS, Config and the plan.

#### determinism-2 — Config is read from the working tree, including gitignored personal layers

- Lens: determinism
- Location: `scripts/orchlib/config.py:12,65-91`
- Evidence: Config layers are read with Path(coord_root)/layer, not through a ref. *.user.toml files exist only locally. project_name also falls back to the checkout directory name, which can differ between local and CI.
- Recommendation: Read only the committed team layers through gitio.Tree(coord_root, coord_ref). Emit a config_source object with the layer blob SHAs in the gate result.

#### determinism-3 — Local ref freshness and uncommitted state change the verdict, and the output does not record either

- Lens: determinism
- Location: `scripts/orchlib/gitio.py:41-45; scripts/orch.py:36-43,184-186; gate.py:69-71`
- Evidence: The base defaults to the local origin/main or main and nothing is fetched. The result records only ref names ('origin/main', 'HEAD'), not SHAs. The 'commit first' rule exists only as a SKILL.md gotcha.
- Recommendation: Emit base_sha, head_sha, merge_base, coord_ref and coord_sha in the result. Add --fetch or a staleness warning, and a dirty_worktree warning from git status --porcelain.

#### determinism-4 — Cross-repo read failures and --offline silently change the verdict, and the failure message blames the wrong cause

- Lens: determinism
- Location: `scripts/orchlib/markers.py:111-130; gate.py:186-213,227-229`
- Evidence: A broad except turns an unreadable repo into a warning. The narrow-story path drops these warnings and reports 'consumers not yet migrated', and sprint-status reports done-without-marker. Local SSH keys and the CI token differ in which repos they can read. --offline is not recorded in the result.
- Recommendation: Add a distinct repo-unreadable finding code that names the repo and the credential, pass the warnings through to every check that uses the merged map, record offline mode, reject --offline in CI, and narrow the except.

#### enhancement-1 — Add: explain a CI failure from its gate JSON instead of always re-running locally

- Lens: enhancement
- Location: `SKILL.md: Running the gate`
- Evidence: The plan's Explain capability takes the gate JSON (gate -o), but SKILL.md only re-runs the gate locally. That run can differ from CI because of a stale base, uncommitted work, the merge-driver check or --coord.
- Recommendation: If the user has CI output or the -o JSON, explain that. Otherwise run locally, and name the likely causes when the local and CI verdicts differ.

#### enhancement-2 — Add: actionable error for shallow CI checkouts (no merge-base)

- Lens: enhancement
- Location: `scripts/orchlib/gitio.py: merge_base / git(); SKILL.md: Gotchas`
- Evidence: The default fetch-depth 1 makes git merge-base exit 1 with empty stderr. The gate exits 2 with 'failed in <repo>: ' and nothing after the colon.
- Recommendation: On failure, check git rev-parse --is-shallow-repository and name the fix (fetch-depth: 0 / GIT_DEPTH: 0). Add a one-line gotcha.

### Medium (14)

#### leanness-1 — Library command table is paid on every gate run but serves other skills' authors

- Lens: leanness
- Location: `SKILL.md: Library for other orch skills (lines 53-67)`
- Evidence: Other orch skills call the CLI via uv run and never load this SKILL.md. The 9-row table repeats the argparse --help text. The only fact the explainer needs from it is the exit-code meaning.
- Recommendation: Replace the section with the smallest version below.
- Proposed smallest: ## Library for other orch skills

`scripts/orch.py` is the shared orch CLI (JSON on stdout; exit 1 = failing verdict, lost race or validation issues; exit 2 = usage/environment error — report it, do not explain a verdict). `uv run scripts/orch.py --help` lists the commands; `deps` shows which contract detectors are installed.
- Predicted delta: No loss expected in gate-explanation runs; the model reaches commands through --help, and the exit-code semantics and deps pointer are kept. Route to variant eval to confirm.

#### architecture-1 — Other orch skills are told to call a bare path they cannot resolve

- Lens: architecture
- Location: `SKILL.md: Library for other orch skills`
- Evidence: 'uv run scripts/orch.py' resolves from the calling skill's own directory, where no orch.py exists. Nothing defines how callers locate orch-gate's installed scripts.
- Recommendation: Publish one canonical locator, e.g. {project-root}/<skills dir>/orch-gate/scripts/orch.py or an orch_cli config key written by orch-setup and echoed by 'orch.py config'.

#### architecture-2 — {communication_language} is used but never loaded

- Lens: architecture
- Location: `SKILL.md: Running the gate`
- Evidence: The skill has no activation step or config load, and 'orch.py config' filters out core keys. The explanation language therefore depends on the language of the conversation.
- Recommendation: Add one resolution line that names the source of communication_language (_bmad config, else the user's language), or have 'orch.py config' emit it.

#### customization-4 — orch_main_branch is a config key that the module plan does not list

- Lens: customization
- Location: `scripts/orchlib/config.py:DEFAULTS; registry.py:131; orch.py:38,109,185`
- Evidence: orch_main_branch sets the base ref, the coordination ref and the registry branch default. It is not in the plan's config_variables, and SKILL.md does not mention it.
- Recommendation: Add it to the plan's config table and the future module.yaml, or remove it in favor of --base, --coord-ref and the registry branch.

#### determinism-5 — Detector tool versions are neither pinned nor recorded, so breaking-change rules can differ between machines

- Lens: determinism
- Location: `scripts/orchlib/detectors.py:29-34,43-51`
- Evidence: The install hints use @latest or unversioned installs, and deps only checks which(). The breaking-change rule sets of oasdiff, buf and atlas change between releases.
- Recommendation: Record <binary> --version in the result, support an optional pinned version per type, and pin the versions in the install hints.

#### determinism-6 — The db-schema detector reads the working tree, not the PR head ref

- Lens: determinism
- Location: `scripts/orchlib/detectors.py:62-63,85-90`
- Evidence: atlas migrate lint --dir file://<canonical> runs with cwd=repo, so it sees uncommitted and untracked files. dev_url can come from ORCH_ATLAS_DEV_URL.
- Recommendation: Materialize the migrations directory from the head ref into a temp directory, as the file adapters do, and record the source of dev_url.

#### determinism-7 — The LLM has to classify fixable findings from free-text hints

- Lens: determinism
- Location: `SKILL.md: Running the gate; scripts/orchlib/gate.py Check.fail/warn`
- Evidence: Gate findings have only a message and a free-text hint, with no stable code. The model maps them to fixes itself, although the script knows the answer, for example that a rebase is needed before marker write.
- Recommendation: Give every finding a stable code and a structured fix object {mechanical, command, precondition}, and shrink the SKILL.md list accordingly.

#### determinism-8 — Unexpected exceptions exit 1, which callers read as a failing verdict

- Lens: determinism
- Location: `scripts/orch.py:267-277; config.py:64; gitio.py:66-68`
- Evidence: main catches only OrchError. A TOMLDecodeError, a UnicodeDecodeError or a KeyError produces a traceback and exit 1, with no JSON.
- Recommendation: Catch Exception in main and emit JSON with exit 2, with the traceback under --verbose (currently unused). Map TOML and decode errors to OrchError, and add tests.

#### enhancement-3 — Add: warn about uncommitted work before a local run

- Lens: enhancement
- Location: `SKILL.md: Gotchas; scripts/orchlib/gate.py: run`
- Evidence: Uncommitted changes are invisible to the gate, and nothing detects them. marker write also computes its pins from HEAD.
- Recommendation: In local runs, add an informational finding when git status --porcelain is non-empty. It does not change the verdict.

#### enhancement-4 — Add: re-run the gate after applying a mechanical fix

- Lens: enhancement
- Location: `SKILL.md: Running the gate (mechanical fixes list)`
- Evidence: marker write and derive --write change the working tree, but the gate reads committed refs. The skill says neither 'commit' nor 're-run'.
- Recommendation: After each fix, offer to commit, then re-run gate --format text and report the new verdict.

#### enhancement-5 — Add: show the conformance diff (the plan brief promises 'pass/fail with diff')

- Lens: enhancement
- Location: `scripts/orchlib/gate.py: conformance check`
- Evidence: A conformance failure only says '<copy> differs from canonical <canonical>', with no detail.
- Recommendation: Attach a truncated difflib unified diff of the normalized files as detail.

#### enhancement-6 — Add: a CI report format (plan: 'optional HTML report for CI artifacts')

- Lens: enhancement
- Location: `scripts/orch.py: gate --format; gate.py: render_text`
- Evidence: Only json and text were built. In CI the raw log is the only human-facing view of a failure.
- Recommendation: Add --format markdown for $GITHUB_STEP_SUMMARY or MR notes, and wire it into the orch-setup CI template.

#### enhancement-7 — Add: a detector self-test so the pilot can verify the asyncapi and atlas adapters (known gap)

- Lens: enhancement
- Location: `scripts/orchlib/detectors.py; scripts/orch.py: deps`
- Evidence: deps only checks that the binary is on PATH. An adapter command line that is wrong shows up only on a real contract PR.
- Recommendation: Add orch.py deps --probe, which runs each adapter against built-in fixtures of one compatible and one breaking change.

#### enhancement-8 — Opportunity: gate-only adoption without BMad planning (plan's 'Independent value' use case)

- Lens: enhancement
- Location: `scripts/orchlib/gate.py: scope (no-story branch); stories.py: load`
- Evidence: The plan promises that the gate enforces boundaries without planning artifacts. The built gate fails every no-marker PR inside a subproject, so a team that adopts only the registry cannot merge.
- Recommendation: Either record gate-only mode as post-v1 in the plan and the memlog, or add an opt-in (e.g. orch_story_mode: off). State the choice in SKILL.md.

### Low (10)

#### leanness-2 — Unused `{project-root}` resolution rule

- Lens: leanness
- Location: `SKILL.md: Resolution rules (line 15)`
- Evidence: {project-root} is defined but never used.
- Recommendation: Delete the bullet and keep the bare-paths rule.

#### leanness-3 — The 'do not substitute your judgment / do not bypass' rule is stated three times

- Lens: leanness
- Location: `SKILL.md: line 10 and Gotchas (line 73)`
- Evidence: Line 10 and the asyncapi/atlas gotcha repeat the no-bypass rule.
- Recommendation: End line 10 at 'report that.' and cut the gotcha down to the fact that the adapters are unverified.

#### architecture-3 — The library command table is paid on every run but serves readers who never load this file

- Lens: architecture
- Location: `SKILL.md: Library for other orch skills`
- Evidence: SKILL.md is 1557 tokens against the pre-pass desired tier of 1500. The table's audience never loads this file.
- Recommendation: Shrink the table to the exit-code and JSON lines plus the caller locator, or move it to references/cli.md.

#### architecture-4 — The exit-code summary does not cover the warning-only failures of epic close-check and deps

- Lens: architecture
- Location: `SKILL.md: Library; scripts/orch.py cmd_epic, cmd_deps`
- Evidence: close-check exits 1 on warnings alone (for example an unreadable repo), and deps exits 1 when any detector is missing. A caller can misread this as unmerged stories.
- Recommendation: Document both cases so callers branch on problems and warnings, not on the exit code alone.

#### customization-5 — orch keys accepted both with and without the orch_ prefix

- Lens: customization
- Location: `scripts/orchlib/config.py:load`
- Evidence: registry_dir and orch_registry_dir both map to the same key. If both are set, the result depends on iteration order.
- Recommendation: Accept only the spelling the module installer writes, or report an error when both appear.

#### customization-6 — Where config comes from and which env vars affect the verdict is not documented in one place

- Lens: customization
- Location: `SKILL.md: Running the gate / Library / Gotchas`
- Evidence: The config layers and their order, the defaults, and the env vars CI, ORCH_COORD and ORCH_ATLAS_DEV_URL are spread across sections or missing.
- Recommendation: Add a short 'Inputs that determine the verdict' note, and point to 'orch.py config' for comparing local and CI values.

#### enhancement-9 — Add: a pre-push hook entry point (plan: 'CI and optionally pre-push')

- Lens: enhancement
- Location: `scripts/orch.py; SKILL.md: Running the gate`
- Evidence: The plan lists pre-push as an activation mode, but there is no way to install the hook and SKILL.md never mentions it.
- Recommendation: Add orch.py hook install next to install-driver, or name orch-setup as the owner in the plan.

#### determinism-9 — is_ci treats any non-empty CI value as true

- Lens: determinism
- Location: `scripts/orchlib/gate.py:259-260`
- Evidence: CI=false or CI=0 counts as CI, so the merge-driver check is skipped locally.
- Recommendation: Accept only 1, true or yes, and echo ci and its source in the result.

#### determinism-10 — Git calls inherit the user's git config and GIT_* environment

- Lens: determinism
- Location: `scripts/orchlib/gitio.py:16-26,80-84`
- Evidence: GIT_DIR, GIT_WORK_TREE and GIT_INDEX_FILE leak in from hooks. ls-tree runs without -z, so core.quotePath C-quotes non-ASCII paths.
- Recommendation: Strip the GIT_* variables, set LC_ALL=C, and use ls-tree -z or -c core.quotePath=false.

#### determinism-11 — Finding the coordination repo is left to the user or LLM even though config has the answer

- Lens: determinism
- Location: `scripts/orch.py:33-34; config.py:6; SKILL.md: Running the gate`
- Evidence: Without --coord or ORCH_COORD, a polyrepo code repo is treated as the coordination repo, which produces a confusing registry or epics error.
- Recommendation: Read orch_coordination_repo from the acting repo's committed config, or exit 2 with a precise message.
