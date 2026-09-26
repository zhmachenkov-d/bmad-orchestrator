---
title: 'Module Plan'
status: 'complete'
module_name: 'BMad Orchestrator'
module_code: 'orch'
module_description: 'Deterministic multi-project (monorepo + polyrepo) delivery for BMad teams: parallel stories per subproject, contract-first, git-gated merges.'
architecture: 'workflows-only (4 skills, no agents)'
standalone: false
expands_module: 'bmm'
skills_planned: ['orch-setup', 'orch-next', 'orch-gate', 'orch-status']
config_variables: ['orch_coordination_repo', 'orch_registry_dir', 'orch_contracts_dir', 'orch_worktrees_dir', 'orch_stale_claim_hours', 'orch_review_wait_hours', 'orch_main_branch']
created: '2026-09-25'
updated: '2026-09-25'
---

# Module Plan

## Vision

`orch` lets several people (and agents) deliver one BMad epic across many subprojects, whether packages in a monorepo or separate repos in a polyrepo, in parallel and without merge hell or stale artifacts. It is BMad-native. Teams keep their PRD → architecture → epics/stories → `bmad-build` flow. `orch` adds a subproject registry, canonical contracts, story claims, scoped node context, and a deterministic pre-merge gate.

Source of hardened decisions: `_bmad-output/forge/bmad-orchestrator/forged-idea.md` (locked decisions 1–16, rejected list, open questions). This plan does not re-litigate them.

## Architecture

**Decision: four workflow skills, no agents.** `orch-setup`, `orch-next`, `orch-gate`, `orch-status`.

Rationale:

- The module's value is **deterministic enforcement** (git gating, claims, pins, derived sprint status). That belongs in scripts, not in a persona. Prompts only explain and route; every decision that must hold under concurrency or in CI is made by a `uv run` PEP 723 script in the skill's `scripts/`.
- No persistent persona or conversational memory is needed between invocations; all state lives in git and is shared by every clone.
- The skills serve different moments and different runners: `orch-setup` (once per project), `orch-next` (each developer, each story), `orch-gate` (CI and pre-push, must be identical in both), `orch-status` (anyone, anytime; also owns epic close). `orch-gate` in particular must run with no LLM at all.
- Stock BMad does planning and building. `orch` never forks stock skills; it plugs in through `_bmad/custom/*.toml` overrides (see Integration).

Script-first rule: each skill's SKILL.md is a thin layer over its scripts. Scripts emit JSON; the skill renders it (table, choice list, report) and asks the user when a decision is needed. This keeps a future headless `orch-next -H` cheap to add.

### Memory Architecture

**No agent memory.** The module has no agents, so no `_bmad/memory/` folders. All state is **git-native and shared**:

| State | Where | Written by | Notes |
| --- | --- | --- | --- |
| Subproject registry | coordination repo `orch/subprojects/<name>.yaml` | `orch-setup` (draft), humans via PR | CODEOWNERS-protected |
| Canonical contracts | coordination repo `contracts/<subproject>/...` | contract stories (`bmad-build`) | CODEOWNERS + branch protection |
| Claims | coordination repo refs `refs/heads/claim/<story>` | `orch-next` (atomic `update-ref` / push) | take-over rewrites the ref |
| Story merge marker / completion shard | code repo `.orch/stories/<story>.yaml` (`story`, `epic`, `contract_pins`) | `bmad-build` `on_complete` override | "merged" = file in main; archived on epic close |
| `sprint-status.yaml` | coordination repo, `{implementation_artifacts}` | derived from shards by `orch` script; custom merge driver | `epic-N: done` only after close check |
| Merge-status cache | local, per clone (`<git common dir>/orch-cache/`, never committable) | `orch-next`, `orch-status` | discardable; pull-based reads of code repos' main |

### Memory Contract

Not applicable — no agents, no curated memory files. The git-native state table above is the contract; each skill brief lists what it reads and writes.

### Cross-Agent Patterns

Not applicable (no agents). **Skill handoffs:**

- `orch-setup` → user runs stock planning (`bmad-prd` → `bmad-architecture` → `bmad-create-epics-and-stories`, with the orch override) → `bmad-sprint-planning` (with the orch plan-validation override).
- `orch-next` → hands the claimed story to `bmad-build` inside the prepared worktree, with node context injected by the `bmad-build` override.
- `bmad-build` completes → writes `.orch/stories/<story>.yaml` → PR → `orch-gate` in CI (and optionally pre-push) → merge.
- `orch-status` → shows progress and anomalies; offers **take over** for stale claims; when every story of an epic is merged, offers **close epic** → then offers `bmad-retrospective`.
- The user is the router between skills; there is no orchestrator agent.

## Skills

Shared context for every brief: module `orch` expands BMad Method (`bmm`). Hardened decisions live in `_bmad-output/forge/bmad-orchestrator/forged-idea.md`; state layout is in **Memory Architecture** above; config in **Configuration**. All skills are workflows, script-first: `uv run` PEP 723 scripts in `scripts/` emit JSON, SKILL.md renders and asks. Shared script logic (registry loading, story/DAG parsing, claim refs, shard reading) should live in one place — decided: **owned by `orch-gate`** and invoked by the other skills (see orch-gate brief). Take over is one script (in `orch-gate`'s library) with two entry points: `orch-next` warnings and `orch-status` anomalies.

### orch-setup

**Type:** workflow (scaffolded by Create Module, then extended)

**Core Outcome:** A project goes from "orch installed" to "ready to plan and build in parallel" in one sitting: the user understands the flow, has a confirmed subproject registry, and every mechanical piece (merge driver, overrides, CI job, ignore rules, dependency checks) is in place.

**The Non-Negotiable:** Never clobber existing user files — merge into existing `_bmad/custom/*.toml`, `.gitattributes`, `.gitignore`, CI files; every step idempotent and safely re-runnable.

**Capabilities:**

| Capability | Outcome | Inputs | Outputs |
| ---------- | ------- | ------ | ------- |
| Intro | User knows what orch does and the daily flow in a few lines | — | on-screen explanation |
| Configure | Module config collected (see Configuration) | user answers or inline values | `_bmad` module config entries |
| Draft registry | Proposed registry from repo structure; user edits and confirms | repo tree, manifests (`package.json`, `go.mod`, `pyproject.toml`, `Cargo.toml`, workspace files, `services/*`, `packages/*`, `apps/*`) | `<registry_dir>/<name>.yaml` per subproject |
| Validate registry | Registry is well-formed and consistent (paths exist, `allowed_write` sets don't overlap, `imports` reference known subprojects, no import cycles, contract `canonical` paths under `<contracts_dir>`) | registry | pass/fail list |
| Install merge driver | `sprint-status.yaml` conflicts are rebuilt from shards | — | `.gitattributes` entry + `git config merge.orch-sprint-status.*` |
| Write overrides | Stock skills learn orch rules | registry, templates | `_bmad/custom/bmad-create-epics-and-stories.toml`, `bmad-build.toml`, `bmad-sprint-planning.toml` |
| CI templates | `orch-gate` runs on every PR | platform choice (GitHub Actions, GitLab CI, both) | workflow/job files; suggested CODEOWNERS lines |
| Dependency check | Missing tools reported with install hints | registry contract types | check report |

**Activation Modes:** interactive; headless (`-H`) with inline values for re-runs (e.g. a new clone only needs the merge driver re-registered).

**Tool Dependencies:** git, uv; contract detectors checked per registry types.

**Design Notes:** Create Module generates the base setup skill (`assets/module.yaml`, `module-help.csv`); orch-specific steps are added after scaffolding. The merge driver is per-clone `git config`, so setup must be cheap to re-run and `orch-gate` must detect clones without it (decision 11). Registry draft is a proposal, never written without confirmation.

**Relationships:** first; before stock planning. Re-run when subprojects are added.

---

### orch-next

**Type:** workflow

**Core Outcome:** A developer asks "what should I work on?" and within one exchange has a claimed story in its own worktree with node context loaded, handed to `bmad-build`.

**The Non-Negotiable:** Two people (or agents) can never claim the same story — the claim is an atomic git ref operation (`update-ref` / push of `refs/heads/claim/<story>` to the coordination repo), and a lost race is reported, not retried silently.

**Capabilities:**

| Capability | Outcome | Inputs | Outputs |
| ---------- | ------- | ------ | ------- |
| Warnings | Problems surfaced before the choice (stale claims, stuck reviews, pin drift, contract bottleneck) | shards, claim refs, code repos' main (pull), cache | short warning list |
| Ready list | "N stories ready — here is what each unblocks": stories whose `depends_on` are all merged to main and unclaimed, ranked by downstream count / critical path | epics/stories, story DAG, merge status | choice list |
| Claim | Atomic claim of the chosen story | story id, user identity | claim ref; failure if lost race |
| Take over | Claim a stale story from someone else (offered, never automatic) | stale claim | rewritten claim ref, existing branch reused |
| Prepare worktree | Story branch `story/<N-M>` + worktree in the right repo (coordination repo for `contracts` stories, code repo otherwise); `orch-status` measures claim activity and finds reviews by this branch name | registry `repo`, `path`; `orch_worktrees_dir` | worktree path, branch |
| Node context | Scoped context: story, subproject `allowed_read`, imported canonical contracts, pinned contract/PRD hashes | registry, contracts, story | context file consumed by the `bmad-build` override |
| Hand off | Start `bmad-build` for the story in the worktree | — | `bmad-build` session |

**Activation Modes:** interactive in v1. **Headless `-H` is post-v1** (auto-pick top-ranked story → claim → worktree → `bmad-build-auto`); keep ranking, claim and worktree prep script-backed so it is cheap to add.

**Tool Dependencies:** git; git host CLI optional for review age.

**Design Notes:** Dependency satisfied = merged to main (no stacked branches). Merge status is pulled from each code repo's main (`.orch/stories/<story>.yaml` present), only for in-progress stories, cached locally. Read isolation is for context economy, not security. Contract stories are ordinary stories in the `contracts` pseudo-subproject built with plain `bmad-build`.

**Relationships:** after `bmad-sprint-planning`; hands off to `bmad-build`; followed by PR + `orch-gate`.

---

### orch-gate

**Type:** workflow (primarily a deterministic script; the skill wrapper explains results)

**Core Outcome:** A PR merges only if it respects its story's boundaries and contracts. Same verdict locally and in CI.

**The Non-Negotiable:** Fully deterministic — no LLM in the verdict path; the CI job runs the script directly and fails the build on violation.

**Capabilities:**

| Capability | Outcome | Inputs | Outputs |
| ---------- | ------- | ------ | ------- |
| Scope check | Diff touches only the story subproject's `allowed_write` (plus its own `.orch/stories/<story>.yaml`) | PR diff, story, registry | pass/fail with offending paths |
| Marker check | `.orch/stories/<story>.yaml` present in the diff; `contract_pins` match current canonical contracts / PRD | diff, contracts | pass/fail |
| Contract conformance | Service copies / generated code match the canonical contract | registry `exports[].copy`, canonical | pass/fail with diff |
| Breaking-change detection | Contract diffs vs previous version classified; breaking changes rejected unless this is a contract-narrowing story and every registry consumer has migrated | contract before/after, registry `imports` | pass/fail; per-type detectors: `oasdiff` (OpenAPI), `buf breaking` (protobuf), `asyncapi diff` (AsyncAPI), `atlas migrate lint` (DB schema) |
| Sprint-status consistency | `sprint-status.yaml` equals the derivation from shards; `epic-N: done` only if close check passed; detects clones without merge driver | sprint-status, shards | pass/fail |
| Explain | Human-readable explanation of failures and how to fix | gate JSON | text; optional HTML report for CI artifacts |

**Activation Modes:** headless (CI, pre-push hook) and interactive (explain).

**Tool Dependencies:** git; contract detectors per type. Missing detector for a type present in the registry = fail.

**Design Notes:** Detectors are adapters keyed by `contracts.exports[].type`. Optimistic contract concurrency: a contract story must be rebased onto the latest contract and re-approved (gate checks pins against main). Client hooks are optional hardening, not the guarantee.

**Shared library owner:** `orch-gate/scripts/` owns the shared orch library (registry loading and validation, story/DAG parsing, claim refs, shard reading, sprint-status derivation, and for `orch-status` and `orch-next`: `status` with anomalies, `plan-check`, `epic close`, `report`, `next`, `worktree`, `context`). `orch-setup`, `orch-next` and `orch-status` invoke `orch-gate` scripts rather than duplicating logic. Consequence: `orch-gate` is always installed and is built first. Calling convention, which each consuming skill states in its own SKILL.md: `uv run <calling skill's directory>/../orch-gate/scripts/orch.py <command>` (the orch skills install side by side). Output is JSON on stdout. Exit 1 is a verdict or validation result, and exit 2 is an error, never a verdict.

**Relationships:** runs on every PR; library dependency of every other orch skill.

---

### orch-status

**Type:** workflow

**Core Outcome:** Anyone can see where every epic stands across all subprojects, what is blocked and why, and act on it — including taking over stale claims and closing finished epics.

**The Non-Negotiable:** An epic becomes `done` only through the close check: all stories merged, `.orch/stories/*` archived, all pins converged.

**Capabilities:**

| Capability | Outcome | Inputs | Outputs |
| ---------- | ------- | ------ | ------- |
| Status table | Per epic / story: state, subproject, claimant, age, blocked-by | shards, claims, story DAG, code repos' main | terminal table |
| Anomalies | Stale claims (> `orch_stale_claim_hours`), stuck reviews (> `orch_review_wait_hours`), contract bottlenecks, pin drift, clones without merge driver | same + git host (optional) | flagged list with suggested action |
| Take over | Offer to reassign a stale claim to the current user | stale claim | claim ref rewrite (via `orch-next` claim script) |
| Close epic | When all stories merged: archive `.orch/stories/*`, verify pins, set `epic-N: done`, offer `bmad-retrospective` | epic id | archived shards, updated sprint-status, close summary |
| Plan validation | Before implementation starts: each story has exactly one `subproject`, `depends_on` consistent with registry `imports`, breaking contract changes split expand → migrate → contract | epics/stories, registry | PASS/CONCERNS/FAIL with findings — invoked from the `bmad-sprint-planning` override's readiness check; also runnable on demand |
| Rebuild sprint status | Derive `sprint-status.yaml` from shards (also the merge driver's entry point) | shards | sprint-status.yaml |
| Epic report | Full report as **HTML or PDF** on request: story DAG coloured by state, critical path, bottlenecks, anomalies, per-subproject progress | epic id, format | self-contained file in `{implementation_artifacts}/orch/reports/` |

**Activation Modes:** interactive; headless for rebuild and report generation.

**Tool Dependencies:** git; git host CLI optional (`gh`/`glab` for review waits); headless Chromium/Chrome optional for PDF (`ORCH_CHROME` overrides), otherwise the HTML is printed from a browser.

**Design Notes:** Pull-only notification model — no bots or push. Stock BMad never sets `epic-N: done`, so orch owns that transition. Close epic runs as two gated PR passes: `orch/close-epic-N` archives markers in every repo, then `orch/close-epic-N-record` adds the close record, `epic-N: done` and the retro data file `orch-epic-N.json` that `bmad-retrospective` can consume. Pins converge when, per subproject and canonical, the story pinning the newest version equals main or every later contract version (in the order their final markers landed on coordination main; that order is read only when a canonical changed more than once, and then a shallow clone cut through the markers deepens itself from origin or the gate fails with the fetch-depth fix) is compatible or a `narrow` that depends on a story of that subproject; `pin-drift` offers one drafted migration story per subproject when a new story can converge it, numbered last in the latest open epic it depends on, or in a new epic when that one is closed. The record pass waits until every registry repo was read.

**Relationships:** anytime; close epic precedes `bmad-retrospective`.

## Configuration

| Variable | Prompt | Default | Result Template | User Setting |
| -------- | ------ | ------- | --------------- | ------------ |
| `orch_coordination_repo` | Where is the coordination repo? `.` for a monorepo, or a git URL / local path for a polyrepo | `.` | `{value}` | no |
| `orch_registry_dir` | Folder for the subproject registry (one YAML per subproject) | `_bmad-output/orch/subprojects` | `{project-root}/{value}` | no |
| `orch_contracts_dir` | Folder for canonical contracts (`<dir>/<subproject>/...`) | `_bmad-output/orch/contracts` | `{project-root}/{value}` | no |
| `orch_worktrees_dir` | Where to create per-story git worktrees | `../{project_name}-worktrees` | `{value}` | yes |
| `orch_stale_claim_hours` | Hours without branch activity before a claim is considered stale | `48` | `{value}` | no |
| `orch_review_wait_hours` | Hours a PR may sit in review before it is flagged | `24` | `{value}` | no |
| `orch_main_branch` | Integration branch of every repo: the gate's default base and the ref orch config, registry and epics are read at (registry `branch` overrides it per subproject) | `main` | `{value}` | no |

Paths are relative to the coordination repo. Registry and contracts are siblings under `_bmad-output/orch/` so CODEOWNERS / branch protection can target `_bmad-output/orch/contracts/**` cleanly. Every skill falls back to these defaults when config is missing.

## External Dependencies

| Tool | Needed by | Purpose | Setup handling |
| --- | --- | --- | --- |
| `git` (≥ 2.25, worktree + `update-ref`) | all | claims, worktrees, merge driver, reading code repos' main | check version; hard fail if missing |
| `uv` | all | runs PEP 723 scripts | check; point to install docs |
| `oasdiff` | `orch-gate` | OpenAPI breaking-change detection | check only if registry has `type: openapi`; offer install command |
| `buf` (`buf breaking`) | `orch-gate` | protobuf breaking-change detection | check only if `type: protobuf` |
| `@asyncapi/cli` (`asyncapi diff`) | `orch-gate` | AsyncAPI breaking-change detection | check only if `type: asyncapi` |
| `atlas` (`migrate lint`, destructive-change analyzers) | `orch-gate` | DB schema breaking-change detection (shared databases used as a contract between subprojects) | check only if `type: db-schema`; offer install command |
| Git host access | `orch-next`, `orch-status` | polyrepo: read access to each code repo's main; PR/review age for review-wait detection (`gh` / `glab` optional) | detect `gh`/`glab`; without them, review-wait detection degrades to branch age |

v1 contract types with breaking-change detection: **OpenAPI, protobuf, AsyncAPI, DB schema** (`type: db-schema`, via `atlas`; relevant when subprojects share a database that acts as a contract). `orch-gate` dispatches by `contracts.exports[].type`; detectors are pluggable, so a new type is one adapter. A missing detector for a type that is present in the registry is a gate **failure**, not a silent skip.

## UI and Visualization

- Default: terminal tables (`orch-status`, `orch-next` choice list with "what it unblocks").
- On request: `orch-status` generates a full **epic report as HTML or PDF** — story DAG coloured by state (done / in progress / ready / blocked), critical path, bottlenecks, anomalies (stale claims, stuck reviews, pin drift), per-subproject progress. Self-contained single file under `{implementation_artifacts}/orch/reports/`. PDF rendered from the same HTML.

## Setup Extensions

Beyond config collection, `orch-setup` (after Create Module scaffolds it) must:

1. **Intro:** short explanation of what `orch` is and the day-to-day flow (setup → plan → `orch-next` → `bmad-build` → PR + `orch-gate` → `orch-status` / close epic).
2. **Draft registry:** scan the repo (workspaces, `package.json`, `go.mod`, `pyproject.toml`, `Cargo.toml`, `services/*`, `packages/*`, `apps/*`), propose `<registry_dir>/<name>.yaml` per subproject with `path`, `allowed_read`, `allowed_write`, detected contracts; user edits and confirms. Validate the result.
3. **Merge driver:** add `.gitattributes` entry for `sprint-status.yaml` and register the driver in `git config` (per clone; re-runnable, idempotent).
4. **Stock-skill overrides:** write `_bmad/custom/bmad-create-epics-and-stories.toml`, `_bmad/custom/bmad-build.toml`, `_bmad/custom/bmad-sprint-planning.toml` (merge with existing files, never clobber).
5. **CI templates:** generate `orch-gate` jobs for **GitHub Actions and GitLab CI** (user picks which to write); suggest CODEOWNERS entries for `<contracts_dir>/**`, `<registry_dir>/**`, planning artifacts. Each job passes `--ci`, writes `-o orch-gate.json` and uploads it as a job artifact, and adds `--format markdown` to the step summary.
6. **Hygiene:** check external dependencies per registry contract types.
7. **Pre-push hook (optional):** offer a `pre-push` hook that runs `orch.py gate --format text` and blocks the push on a failing verdict (exit 1). It must not block on exit 2, which is an environment problem, and it never replaces the CI gate.

Bootstrap order: the gate fails `setup` while the coordination main has no registry or no epics. Land the registry and epics in a coordination PR that changes only registry and planning files. The gate accepts that PR as a setup repair when its own head resolves the problems.

## Integration

**Expansion of `bmm` (BMad Method).** `orch` assumes the BMM flow and plugs into it only through `_bmad/custom/*.toml`:

| Stock skill | Override | What `orch` adds |
| --- | --- | --- |
| `bmad-create-epics-and-stories` | `persistent_facts` | registry list; rules: one `subproject:` per story, explicit backwards `depends_on`, contract story first, breaking change = expand → migrate → contract; emit exactly the bold-label story metadata that `orch-gate` reads (`**Subproject:**`, `**Depends on:**`, `**Contract change:**`, see its SKILL.md) |
| `bmad-sprint-planning` | `persistent_facts` / activation step | plan validation against the registry in the readiness check (PASS/CONCERNS/FAIL), backed by an `orch-status` script |
| `bmad-build` | `activation_steps_prepend`, `persistent_facts`, `on_complete` | node context (allowed_read, imported contracts, pins); write `.orch/stories/<story>.yaml` |
| `bmad-build-auto` | (post-v1) | node context for headless runs |

Budget: ≤ ~4 overrides per stock skill, else reconsider forking.

**Independent value without BMM:** the registry + `orch-gate` (allowed_write, contract conformance, breaking-change detection) work on any mono/polyrepo PR even with no BMad planning artifacts. *Post-v1:* the v1 gate requires a story marker for every subproject change, so registry-only adoption needs an opt-in mode (for example `orch_story_mode: off`) in which a PR without a marker is scope-checked for each subproject it touches and still gets the conformance and breaking checks.

## Creative Use Cases

- **Gate without BMad planning:** a team adopts only the registry + `orch-gate` in CI to enforce package boundaries and contract compatibility in a monorepo, before ever using BMM planning.
- **Parallel agent swarm (post-v1):** several `bmad-build-auto` loops fed by headless `orch-next -H`; humans only review and merge; claims keep agents apart.
- **Retro fuel:** `orch-status` data (review waits, take-overs, contract bottlenecks, critical path slips) feeds `bmad-retrospective` with evidence instead of recollection.
- **Contract change impact preview:** before writing a contract story, `orch-status` plan validation + registry `imports` show every consumer a change will touch, sizing the expand → migrate → contract chain.
- **Stakeholder report:** the HTML/PDF epic report doubles as a status update for people outside the repo.

## Ideas Captured

### Carried over from the forge (locked, see forged-idea.md)

- 4 workflow skills, no agents: `orch-setup`, `orch-next`, `orch-gate`, `orch-status`; deterministic `uv run` PEP 723 scripts in each skill's `scripts/`.
- Integration with stock BMad only via `_bmad/custom/*.toml` (`bmad-create-epics-and-stories` persistent_facts; `bmad-build` activation_steps_prepend / persistent_facts / on_complete); at most ~4 overrides per stock skill.
- Registry `orch/subprojects/<name>.yaml`; canonical contracts `contracts/<subproject>/...`; pseudo-subproject `contracts`.
- Claims as git refs `refs/heads/claim/...`; merge marker `.orch/stories/<story>.yaml` in the code repo; pull-based merge detection; `sprint-status.yaml` derived from shards with a custom merge driver.
- Breaking contract changes only via expand → migrate → contract; gate diffs contracts against their previous version.

### Open from the forge

- First user already lives in BMad: unverified, no pilot named.
- Monorepo + polyrepo both in v1 (risk accepted).
- Breaking-change detector per contract type — resolved in ideation: `oasdiff` (OpenAPI), `buf breaking` (protobuf), `asyncapi diff` (AsyncAPI), `atlas migrate lint` (DB schema).
- Override count per stock skill vs the fork threshold.

### New in ideation

- Identity confirmed: BMad Orchestrator, code `orch`, expansion of `bmm`.
- **Onboarding (first 10 minutes):** `orch-setup` opens with a short explanation of what the module is and how to use it (the flow in a few lines), then **proposes a draft registry** by scanning the repo (workspaces, `package.json`, `go.mod`, `pyproject.toml`, `services/*`, `packages/*`, etc.). The user edits and confirms rather than writing YAML by hand.
- **`orch-next` UX:** short choice, not a single auto-pick: "N stories ready — here is what each one unblocks" (downstream count / critical path from the story DAG). User picks, then claim → worktree → node context → `bmad-build`.
- **Health / anomaly detection — `orch` notices all of these itself:**
  - stale / abandoned claims (claim ref with no branch activity for X);
  - stories stuck in review for too long (PR open, dependents waiting);
  - contract stories that block many dependents (bottleneck on the critical path);
  - (implied) pins drifting from the latest merged contract/PRD.
- **Surfacing problems is pull-only:** `orch-status` shows them in full; `orch-next` prints warnings before the ready list. No bots, no push notifications, no extra tokens.
- **Stale claim → "take over":** `orch` never auto-releases; it offers the user to take the claim over (reassign the claim ref, continue from the existing branch/worktree).
- **Reporting:** `orch-status` defaults to a terminal table; on request it generates a full epic report as **HTML or PDF** (story DAG with states, critical path, bottlenecks, anomalies).
- **Epic close gate (revised):** archiving `.orch/stories/*` and verifying all pins converged must happen **before the epic transitions to `done`** — not inside the retrospective. Finding: stock BMad has no step that sets `epic-N: done` (`bmad-build` sync only moves the epic `backlog → in-progress`; `sprint_plan.py` just preserves the highest status rank; `bmad-retrospective` only marks `epic-N-retrospective`). So there is no stock hook to attach to — `orch` must own the `epic → done` transition itself (candidate: the sprint-status derivation from shards only emits `done` once the close check passes). **Decided: `orch-status` owns it** — when all stories of an epic are merged, it offers "close epic": archive `.orch/stories/*`, verify pins converged, set `epic-N: done`, then offer `bmad-retrospective`. No separate `orch-close` skill. Derivation rule: `epic-N: done` is emitted only when the close check passed; `orch-gate` rejects a PR that sets `done` bypassing it.
- **Contract stories use plain `bmad-build`** with the node context of the `contracts` pseudo-subproject — no special contract-authoring mode in `orch-next`.
- **Plan validation lives in `bmad-sprint-planning` via override** (`_bmad/custom/bmad-sprint-planning.toml`): each story has exactly one `subproject`; `depends_on` is consistent with registry `imports`; breaking contract changes are split into expand → migrate → contract. Runs as part of the readiness check (PASS/CONCERNS/FAIL), backed by an `orch-status` script.
- **Headless `orch-next -H` → `bmad-build-auto` — deferred (post-v1).** Auto-pick the top ready story (most unblocked), claim, worktree, node context, hand off to `bmad-build-auto`; several agents in parallel, claims prevent collisions. v1 design must keep this easy to add (selection logic and claim step are script-backed, not prompt-only). Will likely need a `bmad-build-auto` override for node context.
- (Superseded) Epic close as part of `bmad-retrospective` via override. The retro may still consume orch data (claims history, review wait times, bottlenecks) — optional.

## Build Roadmap

1. **`orch-gate`** (BW) — first: owns the shared library (registry, story DAG, claims, shards, sprint-status derivation) that every other skill calls, and is the deterministic core that must be right. Includes the four contract detector adapters.
2. **`orch-status`** (BW) — builds on the library: status table, anomalies, plan validation, sprint-status rebuild (merge-driver entry point), close epic, HTML/PDF report.
3. **`orch-next`** (BW) — ranking, atomic claim, take over, worktree, node context, hand-off to `bmad-build`. Needs the library and the anomaly logic from `orch-status`.
4. **Override templates** — the three `_bmad/custom/*.toml` bodies (`bmad-create-epics-and-stories`, `bmad-build`, `bmad-sprint-planning`) plus CI templates (GitHub Actions, GitLab CI). Stored as assets for `orch-setup`; test them against stock skills in a sample project.
5. **Create Module (CM)** — scaffolds `orch-setup` with `assets/module.yaml` + `module-help.csv` and the config variables above.
6. **Extend `orch-setup`** (BW, edit) — intro, registry draft + validation, merge driver, overrides, CI templates, dependency check (see Setup Extensions).
7. **Validate Module (VM)** — structure and capability registration check.

Pilot: run the whole flow on one real monorepo and one two-repo polyrepo before calling v1 done (forge open question: first user already in BMad is unverified).

**Next steps:**

1. Build each skill using **Build an Agent (BA)** or **Build a Workflow (BW)** — share this plan document as context
2. When all skills are built, return to **Create Module (CM)** to scaffold the module infrastructure
