# Analysis Report: skills/orch-setup

Generated: 2026-09-28 · Schema: 2

**Grade: Poor**

> Sound shape and lean prompt, but two merge paths corrupt team files on re-run: headless config re-applies the result template (critical) and the on_complete merge drops team text (high).

orch-setup has the right shape: a lean 1842-token SKILL.md, all mechanical work in tested scripts, confirm-before-write on the registry and a never-clobber install policy. The main opportunity is making the re-run path — every teammate's `orch-setup -H` — actually safe: config values in effect are fed back through write-config's result template, and the override merge finds the orch block by phrase instead of by delimiter, so team edits are lost silently.

| Severity | Count |
| --- | --- |
| Critical | 1 |
| High | 1 |
| Medium | 11 |
| Low | 13 |

## Themes

### 1. Re-runs are idempotent for orch, not for the team

- Root cause: The merge scripts decide what belongs to orch by matching phrases and by re-rendering values, not by explicit boundaries, so a second run (headless, in every teammate's clone) rewrites or deletes team content and only reports 'updated'.
- Fix: Give every orch-owned piece an explicit boundary: write-config keeps the value in effect for an unanswered variable and never re-templates an already-rendered value; the on_complete block and list entries carry sentinels/markers; headless only adds missing orch content and reports drift instead of rewriting it.
- Findings:
  - `determinism-1` The model rebuilds config values in effect, and write-config.py applies the result template to them a second time — `SKILL.md:## 2, ## 3, ## Headless; scripts/write-config.py:render`
  - `determinism-2` The on_complete merge finds the orch block by a phrase match and silently drops team text — `scripts/setup.py:merge_override (string branch) and _identity`
  - `customization-1` Re-running overrides deletes team on_complete steps placed after the orch block — `skills/orch-setup/scripts/setup.py:merge_override (str branch)`
  - `customization-2` Team edits to orch entries in _bmad/custom/*.toml are silently reverted by any teammate's headless re-run — `skills/orch-setup/scripts/setup.py:merge_override (list branch); SKILL.md Headless`
  - `determinism-6` file: override entries from an old registry_dir pile up after reconfiguration — `scripts/setup.py:_identity / merge_override (list branch)`

### 2. No re-run route through the conversation

- Root cause: The skill is one first-install sequence; 'reconfigure orch' and teammate runs have no entry of their own, so they replay the intro, scan and CI questions, and the headless contract is a loose stream of JSON.
- Fix: After the install check, summarize what is already in place and ask what to revisit (default all); give headless one result object with a stop-on-exit-2 rule, and give teammates the exact `orch-setup -H hook` command.
- Findings:
  - `architecture-3` The 'reconfigure orch' trigger has no route; every run walks all eight steps — `skills/orch-setup/SKILL.md frontmatter description vs. sections 1-8`
  - `enhancement-1` Add intent routing on re-run: 'reconfigure orch' walks the full 8-step onboarding — `skills/orch-setup/SKILL.md: 1. Intro / 2. Check the install`
  - `enhancement-3` Define a single headless return and a failure rule — `skills/orch-setup/SKILL.md: Headless`
  - `architecture-4` Teammate onboarding line does not match the headless hook rule — `skills/orch-setup/SKILL.md:57 vs :61`
  - `enhancement-6` Detect defaults for the CI question and allow a headless registry from a plan file — `skills/orch-setup/SKILL.md: 6. CI gate / Headless`

### 3. Layered values resolved by the model instead of the CLI

- Root cause: SKILL.md hand-describes TOML layer precedence for config values and communication_language, while orch.py config and write-config already implement it deterministically.
- Fix: Read every effective value from a script (`orch.py config --working-tree`, a new `write-config.py --show`) and delete the hand-written layer lists.
- Findings:
  - `determinism-1` The model rebuilds config values in effect, and write-config.py applies the result template to them a second time — `SKILL.md:## 2, ## 3, ## Headless; scripts/write-config.py:render`
  - `customization-3` {communication_language} resolution ignores the team layers and conflicts with orch-gate's order — `skills/orch-setup/SKILL.md: Resolution rules`

### 4. Registry draft loop is fragile

- Root cause: The negotiated draft lives only in conversation, is validated after an irreversible write, and the scan/write scripts mishandle some inputs.
- Fix: Keep the draft in a plan file that write-registry consumes, validate it before writing (dry-run or replace-own-entries), reject duplicate names, expand `**` globs by manifest, and let ci reuse orch.py's normalized registry read.
- Findings:
  - `enhancement-2` Add a structured working artifact for the registry draft negotiation — `skills/orch-setup/SKILL.md: 4. Draft the registry`
  - `enhancement-5` Validate the registry plan before writing, not after — `skills/orch-setup/SKILL.md: 4; scripts/setup.py cmd_write_registry`
  - `determinism-3` scan collapses a `**` workspace glob into one subproject and crashes on globs outside the repo — `scripts/setup.py:_globs / _candidates`
  - `determinism-4` write-registry reports a duplicate name in the plan as an existing file — `scripts/setup.py:cmd_write_registry`
  - `determinism-7` ci treats an unparsable or repo-less registry entry as polyrepo — `scripts/setup.py:cmd_ci and _existing`

## Strengths

- SKILL.md is lean (1842 tokens) and its eight steps are a real dependency order.
- Deterministic work (scan, override merge, CI install, hook, checks) lives in setup.py with 20 tests; orch-gate's --working-tree reads keep the gate itself ref-only.
- Never-clobber policy for CI files and hooks: differs → diff, foreign hook → snippet, polyrepo → explicit refusal.
- Registry is written only after user confirmation, and imports are never guessed.

## Recommendations

1. Fix write-config for unchanged values: a missing answer keeps the value in effect, render() is idempotent on already-templated paths, and add `--show` so steps 2-3 and headless read effective values from JSON. (resolves: determinism-1, customization-3)
2. Bound the orch on_complete block with begin/end sentinels, mark orch list entries explicitly, and make headless add-only with drift reporting; add tests for trailing team steps and phrase mentions. (resolves: determinism-2, customization-1, customization-2, determinism-6)
3. Add a re-run route and a single headless result object with an exit-code rule; preflight uv and git in step 2; fix the teammate command. (resolves: architecture-3, enhancement-1, enhancement-3, enhancement-4, architecture-4)
4. SKILL.md touch-ups: step 3 reference, `uv run scripts/setup.py` form, orch-gate SKILL.md path, trim steps 2/7/8. (resolves: architecture-1, architecture-2, architecture-5, leanness-1, leanness-2, leanness-3)
5. Harden the registry loop: draft plan file, validate before write, duplicate-name check, `**` glob expansion, registry-invalid code in ci. (resolves: enhancement-2, enhancement-5, determinism-3, determinism-4, determinism-7)
6. Housekeeping: fix or drop the dead symlink warning, record the customize.toml and module.yaml rationales as user decisions in the memlog, preselect CI defaults. (resolves: determinism-5, customization-4, customization-5, enhancement-6)

## Experience

- **First install (lead)** — Intro → install check → batch config → scan, edit, confirm registry → driver, overrides, optional hook → CI + protection checklist → checks → commit list. Works; the registry negotiation is the fragile stretch.
- **Teammate clone (`orch-setup -H`)** — Config with values in effect → driver → overrides → checks. Today this path re-templates path config and can rewrite team override edits.
- **Reconfigure** — Replays all eight steps; no route to just the part the user wants.
- Headless: Easily adaptable, but outputs a stream of JSON without an overall status and currently corrupts path config on the values-in-effect path.

## Findings

### Critical (1)

#### determinism-1 — The model rebuilds config values in effect, and write-config.py applies the result template to them a second time

- Lens: determinism
- Location: `SKILL.md:## 2, ## 3, ## Headless; scripts/write-config.py:render`
- Evidence: Steps 2-3 and headless have the model merge TOML layers and pass 'the values in effect' as raw answers. Stored path values already carry the result template, and render() applies `{project-root}/{value}` again. Reproduced: answer '{project-root}/_bmad-output/orch/subprojects' plans `orch_registry_dir = "{project-root}/{project-root}/_bmad-output/orch/subprojects"`. Headless always takes this path, so every teammate's `-H` run would corrupt committed team config.
- Recommendation: Add a `--show` pre-pass to write-config.py emitting each variable's effective raw value and source layer; treat a missing answer as 'keep the value in effect'; make render() idempotent when the answer already matches the template prefix.

### High (1)

#### determinism-2 — The on_complete merge finds the orch block by a phrase match and silently drops team text

- Lens: determinism
- Location: `scripts/setup.py:merge_override (string branch) and _identity`
- Evidence: `before = cur[:cur.index(head)]` assumes the orch block is last and its lead phrase appears nowhere else. Reproduced: team steps after the orch block are deleted on re-run; a team string that merely mentions 'orch completion' is truncated to 'Before the' + orch block. Only note: "on_complete: updated 'orch completion'". Breaks the never-clobber rule.
- Recommendation: Wrap the orch block in explicit begin/end sentinel lines and replace only between them; with no sentinel, append and keep all existing text. Use an explicit marker for list-entry identity. Add tests for trailing team steps and a mention of the phrase.

### Medium (11)

#### architecture-1 — Stale step number: config is written in step 3, not step 2

- Lens: architecture
- Location: `skills/orch-setup/SKILL.md:18 (Resolution rules)`
- Evidence: 'so config written in step 2 applies before it is committed' — step 2 only reads config; write-config.py runs in step 3.
- Recommendation: Say 'step 3', or drop the number: 'so config written by write-config.py applies before it is committed'.

#### architecture-2 — setup.py invocations are not given in their exact runnable form

- Lens: architecture
- Location: `skills/orch-setup/SKILL.md steps 4-7`
- Evidence: Step 3 gives `uv run scripts/write-config.py ...` but every setup.py call is bare `setup.py <cmd>`. The file is not executable and gets pyyaml/tomlkit only via PEP 723, so `python setup.py` fails with ImportError.
- Recommendation: State once where the script is introduced: run it as `uv run scripts/setup.py <command> --project-root <project root>`; keep the short form in the steps.

#### architecture-3 — The 'reconfigure orch' trigger has no route; every run walks all eight steps

- Lens: architecture
- Location: `skills/orch-setup/SKILL.md frontmatter description vs. sections 1-8`
- Evidence: The description advertises 'reconfigure orch' and the preamble promises re-run safety, but the body is one sequence with no partial entry: changing one config value replays the intro, registry scan and CI questions.
- Recommendation: Add one line after the intro: on a re-run, say what is in place and do only what the user asks; 'reconfigure orch' goes straight to step 3.

#### determinism-3 — scan collapses a `**` workspace glob into one subproject and crashes on globs outside the repo

- Lens: determinism
- Location: `scripts/setup.py:_globs / _candidates`
- Evidence: `glob('components/**')` returns the base dir and every nested dir; the nested filter keeps only 'components', so a pnpm `components/**` workspace yields one proposal. A '../shared' pattern raises ValueError in relative_to and an absolute one NotImplementedError, ending the scan with exit 2 'internal'.
- Recommendation: Expand '**' to directories containing a manifest, never the glob base; skip and note members outside the checkout. Add a test with a `**` pnpm pattern.

#### customization-1 — Re-running overrides deletes team on_complete steps placed after the orch block

- Lens: customization
- Location: `skills/orch-setup/scripts/setup.py:merge_override (str branch)`
- Evidence: When on_complete already holds the orch lead phrase, everything from the orch head to the end is replaced; team steps after it are lost on every teammate's `orch-setup -H`. Tests cover only team steps before the block.
- Recommendation: Replace only the bounded orch block (end sentinel); if unbounded, report `manual`. Add a test with trailing team steps and assert team text is kept byte for byte.

#### customization-2 — Team edits to orch entries in _bmad/custom/*.toml are silently reverted by any teammate's headless re-run

- Lens: customization
- Location: `skills/orch-setup/scripts/setup.py:merge_override (list branch); SKILL.md Headless`
- Evidence: Orch list entries matched by lead phrase are overwritten whenever they differ from the template, so a team tweak is undone by the next `orch-setup -H` in a new clone; the only trace is an unread 'updated' note.
- Recommendation: In headless, only add missing entries and report changed ones as `drift`; rewrite changed entries only interactively after showing the diff, or overwrite only entries matching a previously shipped template hash.

#### customization-3 — {communication_language} resolution ignores the team layers and conflicts with orch-gate's order

- Lens: customization
- Location: `skills/orch-setup/SKILL.md: Resolution rules`
- Evidence: Reads config.user.toml then custom/config.user.toml, skipping both team layers and leaving the winner unclear; orch-gate settled custom user > custom team > user > team, exposed by `orch.py config`, which orch-setup already calls.
- Recommendation: Resolve it from `uv run {orch} config --working-tree` (null means the user's language) and delete the hand-written layer list.

#### enhancement-1 — Add intent routing on re-run: 'reconfigure orch' walks the full 8-step onboarding

- Lens: enhancement
- Location: `skills/orch-setup/SKILL.md: 1. Intro / 2. Check the install`
- Evidence: A returning user who wants one config change, one new subproject or GitLab CI still gets the pitch, the full scan and the CI questions again.
- Recommendation: When step 2 finds orch installed with a registry or overrides, skip the intro, summarize what is in place, and ask which parts to revisit (default all).

#### enhancement-2 — Add a structured working artifact for the registry draft negotiation

- Lens: enhancement
- Location: `skills/orch-setup/SKILL.md: 4. Draft the registry`
- Evidence: Renames, merges, allowed_read widening and imports answers live only in the conversation until confirmation; compaction or an interruption loses them, and re-running scan returns raw proposals.
- Recommendation: Write scan proposals to a draft plan file (e.g. `_bmad-output/orch/registry-draft.json`) in write-registry's shape, update it per decision, offer to resume it, delete after a successful write.

#### enhancement-3 — Define a single headless return and a failure rule

- Lens: enhancement
- Location: `skills/orch-setup/SKILL.md: Headless`
- Evidence: 'Print each script's JSON as the only output' yields four to six JSON documents with no overall status, and exit 1 vs exit 2 handling is unspecified.
- Recommendation: On exit 2 stop; on exit 1 record and continue. Emit one object `{ok, steps:{config, merge_driver, overrides, hook, ci, check, deps}}`.

#### enhancement-4 — Add a tool preflight before the first `uv run`

- Lens: enhancement
- Location: `skills/orch-setup/SKILL.md: 2. Check the install / 7. Check`
- Evidence: git and uv are checked only in step 7, but step 3 already calls `uv run`; `setup.py check` runs under uv so cannot report uv missing. The first failure is a shell 'command not found' mid-configuration.
- Recommendation: In step 2 check `uv --version` and `git --version`; if missing, stop with the install hint before any question.

### Low (13)

#### leanness-1 — Planning chain in step 8 repeats module_greeting

- Lens: leanness
- Location: `skills/orch-setup/SKILL.md:## 8. Next steps`
- Evidence: Step 8 spells out the bmad-prd → bmad-architecture → bmad-create-epics-and-stories → bmad-sprint-planning → orch-next chain, then says 'Finish with module_greeting', which already names the same skills; step 1 also narrates the daily flow. The user hears the chain up to three times.
- Recommendation: Cut the planning-chain sentence from step 8; keep only what the greeting lacks (bmad-sprint-planning if it matters) and the non-inferable bootstrap facts about the first setup PR and the epics PR.

#### leanness-2 — Step 7 restates what setup.py check already reports

- Lens: leanness
- Location: `skills/orch-setup/SKILL.md:## 7. Check`
- Evidence: 'That covers git older than 2.25, an orch dir that is gitignored, and a stock anchor that is gone.' cmd_check already returns self-describing codes and messages; the sentence previews the JSON. The following why (a missing anchor means a BMad update moved a stock skill) is the non-obvious part.
- Recommendation: Truncate step 7 to: run both commands, report each problem with its fix, keep the missing-anchor why, give deps install hints or offer --probe.

#### leanness-3 — Step 2 explains the installer pipeline beyond what the check needs

- Lens: leanness
- Location: `skills/orch-setup/SKILL.md:## 2. Check the install`
- Evidence: The opening sentence traces the installer end to end (marketplace.json, asking module.yaml questions, writing config, installing skills). The step needs only the command, where [modules.orch] lives, and the layering, which the bullet already states.
- Recommendation: Replace the opening sentence with one goal line naming the installer command and what it writes; keep both bullets.
- Proposed smallest: orch is registered only by the BMad installer (`npx bmad-method install --custom-source <orch git URL or local path>`), which writes `[modules.orch]` to `{project-root}/_bmad/config.toml` (`orch_worktrees_dir` to `config.user.toml`) and installs the skills.
- Predicted delta: Nothing expected; marketplace.json and the question pass inform no decision in steps 2-3. Route to variant eval to confirm.

#### architecture-4 — Teammate onboarding line does not match the headless hook rule

- Lens: architecture
- Location: `skills/orch-setup/SKILL.md:57 vs :61`
- Evidence: Step 8 says teammates run `orch-setup -H` for the merge driver 'and the hook, if the team uses it', but headless installs the hook only for an inline `hook`, and a polyrepo code-repo clone needs --coord which headless cannot take.
- Recommendation: Give the exact teammate command (`orch-setup -H hook`); accept inline `coord=<path>` in headless or say polyrepo code clones install the hook interactively.

#### architecture-5 — Cross-skill pointer to the registry entry format is not a resolvable path

- Lens: architecture
- Location: `skills/orch-setup/SKILL.md:39`
- Evidence: 'The entry format is in orch-gate's SKILL.md.' gives no path, though the resolution rules already define the sibling location.
- Recommendation: Write `{skill-root}/../orch-gate/SKILL.md`.

#### determinism-4 — write-registry reports a duplicate name in the plan as an existing file

- Lens: determinism
- Location: `scripts/setup.py:cmd_write_registry`
- Evidence: Validation is per entry; two entries with one name pass, the second lands in `kept_existing`, telling the user an existing file was kept when their own entry was dropped.
- Recommendation: Reject duplicate names during validation before writing anything.

#### determinism-5 — The symlink warning in cli_location never fires

- Lens: determinism
- Location: `scripts/setup.py:cli_location, ORCH_GATE`
- Evidence: Both ORCH_GATE and rel are fully resolved before the is_symlink() check, so no component can be a symlink; the memlog decision 'symlinked ... only warns' never happens.
- Recommendation: Compute rel from the unresolved --orch-gate path (os.path.relpath) and check components before resolving, or drop the dead branch and fix the memlog note.

#### determinism-6 — file: override entries from an old registry_dir pile up after reconfiguration

- Lens: determinism
- Location: `scripts/setup.py:_identity / merge_override (list branch)`
- Evidence: A file: entry's identity is its exact text; after orch_registry_dir changes, re-running overrides adds the new pair and leaves the old one, unreported.
- Recommendation: Match file: entries by a stable orch key and replace in place, or at minimum list stale ones in warnings.

#### determinism-7 — ci treats an unparsable or repo-less registry entry as polyrepo

- Lens: determinism
- Location: `scripts/setup.py:cmd_ci and _existing`
- Evidence: _existing maps unparsable YAML to {}, whose repo '' is not '.', so ci refuses with 'polyrepo'. orch-gate normalizes repo differently.
- Recommendation: Report a separate 'registry-invalid' code, or read the registry through `orch.py registry --working-tree` and use its normalized repo.

#### customization-4 — Opting out of customize.toml is logged as an assumption, and orch-gate's rationale does not carry over

- Lens: customization
- Location: `skills/orch-setup/.memlog.md`
- Evidence: The memlog marks the opt-out as an unasked assumption resting on orch-gate's verdict-determinism reason, which does not apply to an installer. The opt-out does hold on other grounds: teams customize installed outputs that setup never clobbers, and the templates stay orch-gate-owned because the gate depends on them.
- Recommendation: Record that real rationale as a user decision in the memlog; do not add template scalars.

#### customization-5 — module.yaml defines installer questions, a config mechanism besides customize.toml

- Lens: customization
- Location: `skills/orch-setup/assets/module.yaml; scripts/write-config.py`
- Evidence: The guide forbids install-time questions in a built skill, but these are module-scoped settings read by every orch skill and the gate, and a module's setup skill conventionally owns module.yaml; write-config.py writes only the _bmad/custom layers.
- Recommendation: Keep as an accepted exception and record it as a memlog decision; keep module.yaml limited to variables other orch skills or the gate read.

#### enhancement-5 — Validate the registry plan before writing, not after

- Lens: enhancement
- Location: `skills/orch-setup/SKILL.md: 4; scripts/setup.py cmd_write_registry`
- Evidence: Validation runs after write-registry, which never overwrites, so validation issues must be fixed by hand-editing the new YAML rather than correcting the plan.
- Recommendation: Add `write-registry --dry-run` validating a temp copy via `orch registry`, or let write-registry replace entries it created from the same draft.

#### enhancement-6 — Detect defaults for the CI question and allow a headless registry from a plan file

- Lens: enhancement
- Location: `skills/orch-setup/SKILL.md: 6. CI gate / Headless`
- Evidence: Existing .github/workflows, .gitlab-ci.yml, the origin host and CODEOWNERS usually answer the platform and owners questions; headless has no entry for an automator with a confirmed plan.
- Recommendation: Preselect platforms and owners from what exists so the user only confirms; optionally accept inline `registry=<plan.json>` under -H.
