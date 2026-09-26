# Analysis Report: skills/orch-status

Generated: 2026-09-26 · Schema: 2

**Grade: Good**

> Good: the 24 findings of the first run are fixed, but the new merge-order rule still depends on clone depth and merge style (determinism-1, determinism-2), so pin drift can differ between a shallow CI clone and a full one.

The skill stays a lean judgment layer over a script-first library: SKILL.md matches the CLI on flags, codes and exit semantics, and the fix pass added only lines that carry a contract or a handoff. The main opportunity is in the library: merge_order must refuse shallow history and follow what main actually holds rather than the first add of each marker, and the migration draft should be placed and grouped so it passes plan-check as pasted.

| Severity | Count |
| --- | --- |
| Critical | 0 |
| High | 1 |
| Medium | 4 |
| Low | 6 |

## Themes

### 1. Merge order read from history that may be cut or fast-forwarded

- Root cause: merge_order takes the first add of each marker on first-parent history. A shallow clone makes the boundary commit look like a root that adds every marker in path order, and a fast-forward merge exposes branch commit order instead of landing order, so the contract chain and pin drift depend on the clone rather than on main.
- Fix: In merge_order, raise the fetch-depth OrchError on a shallow repository, and order the chain by the first-parent position where each pinned canonical version first appears on main; add shallow-clone and fast-forward regression tests.
- Findings:
  - `determinism-1` merge_order returns path order in shallow clones, bringing back stuck drift — `skills/orch-gate/scripts/orchlib/markers.py:185`
  - `determinism-2` Chain order uses the marker's first add, so fast-forward merges order by branch commit time — `skills/orch-gate/scripts/orchlib/markers.py:199`

### 2. Migration draft is not placed or grouped the way the plan needs it

- Root cause: The draft takes its id from the drifting story's epic while depending on the latest contract story, which may sit in a later epic, and it is emitted once per drifting path although one story of the subproject converges all of them.
- Fix: Group drift by subproject into one draft listing every contract path, allocate its id in the epic of its latest dependency (excluding merged keys), and test that plan-check passes on the pasted draft.
- Findings:
  - `determinism-3` migration_draft places the story in the drifting story's epic but depends on a later epic's contract story — `skills/orch-gate/scripts/orchlib/status.py:307`
  - `determinism-4` One migration draft per drifting canonical, though one story converges them all — `skills/orch-gate/scripts/orchlib/status.py:256`

### 3. Close PR handoff overstates what publish knows

- Root cause: publish reports only whether the branch exists in a given repo; SKILL.md reads `exists` as an open PR and does not say which repo each PR belongs to.
- Fix: Say: open the PR in each entry's `repo` from its `branch` into its `base`; for `exists`, open one if no PR is open for that branch.
- Findings:
  - `leanness-1` Added 'exists' clause claims a PR exists, but the script only knows the branch exists — `skills/orch-status/SKILL.md:35`
  - `architecture-1` The PR step does not say to target each published entry's repo, and it reads 'exists' as an open PR — `skills/orch-status/SKILL.md:35`

### 4. Status output and flows the prompt leaves unrouted

- Root cause: Some status output (plan_issues, closed epics) and two entry points (report without an epic, committing a rebuild) have no named next step.
- Fix: Render plan_issues and offer plan-check, collapse closed epics to one line, offer epics when report has no number, and name the branch and PR for a sprint-status rebuild.
- Findings:
  - `architecture-2` status emits plan_issues but the Status section never renders or routes them — `skills/orch-status/SKILL.md:20`
  - `enhancement-1` Add: collapse closed epics in the default status view — `skills/orch-status/SKILL.md:20`
  - `enhancement-2` Add: choose an epic when the report is requested without one — `skills/orch-status/SKILL.md:47`
  - `enhancement-3` Add: name the branch and PR step when committing the rebuilt sprint status — `skills/orch-status/SKILL.md:43`

## Strengths

- All 24 findings of the first run are resolved, and each library fix has a regression test that fails on the old code (108 tests pass).
- SKILL.md stays at 1624 tokens and owns only judgment: every state, order and code comes from orch.py JSON.
- Two-pass close with --expect-pass never pushes a pass the user did not confirm, and the record pass waits for every repo to be read.
- No customize.toml by design; the new constants are internal limits that surface as notices when they bite.

## Recommendations

1. Make merge_order fail loud on shallow history and order by when main first held each pinned version (resolves: determinism-1, determinism-2)
2. Fix the close PR sentence in SKILL.md: target each entry's repo and treat exists as 'open a PR if none is open' (resolves: leanness-1, architecture-1)
3. One migration draft per subproject, placed in the epic of its latest dependency (resolves: determinism-3, determinism-4)
4. Route plan_issues, closed epics, report without epic and the rebuild commit in SKILL.md, and shorten the no-draft sentence (resolves: architecture-2, enhancement-1, enhancement-2, enhancement-3, leanness-2)

## Experience

- **Delivery lead checks progress** — orch status -> per-epic table and anomalies -> take over or migration draft -> status again
- **Closing an epic** — close epic N -> confirm archive pass -> merge PRs -> close again -> confirm record pass -> merge
- Headless: -H runs status, plan-check, rebuild --write without commit, and report; anything ambiguous prints an error JSON.

## Findings

### High (1)

#### determinism-1 — merge_order returns path order in shallow clones, bringing back stuck drift

- Lens: determinism
- Location: `skills/orch-gate/scripts/orchlib/markers.py:185`
- Evidence: In a --depth 2 clone the boundary commit counts as adding every live marker in path order: the REVERSED fixture gives {1-1:0,1-2:1,1-3:2} instead of {1-2:0,1-1:1,1-3:2}, so status shows unrecorded-change drift and blocks close while a full clone passes; the gate's close_check with GitLab's default GIT_DEPTH is affected too.
- Recommendation: Check `git rev-parse --is-shallow-repository` in merge_order and raise the same fetch-depth OrchError as gitio.merge_base; add a shallow-clone regression test.

### Medium (4)

#### leanness-1 — Added 'exists' clause claims a PR exists, but the script only knows the branch exists

- Lens: leanness
- Location: `skills/orch-status/SKILL.md:35`
- Evidence: close.publish returns `exists` when ls-remote (or the local ref) finds the branch; it never checks for a PR. A branch pushed by an earlier session that never opened its PR is reported as 'waiting for review' and the close stalls.
- Recommendation: Say: '`exists` means an earlier run pushed the branch; open its PR if none is open.'

#### determinism-2 — Chain order uses the marker's first add, so fast-forward merges order by branch commit time

- Lens: determinism
- Location: `skills/orch-gate/scripts/orchlib/markers.py:199`
- Evidence: A story that commits its marker early, then merges main and rewrites the marker before main fast-forwards to it, keeps its early position: merge_order gives {1-1:0,1-2:1} and status reports unrecorded-change on 1-2 though main equals 1-1's pin.
- Recommendation: Order by the first-parent position where each pinned canonical version first appears on main; at minimum use the last add/modify of the marker. Add a fast-forward test.

#### determinism-3 — migration_draft places the story in the drifting story's epic but depends on a later epic's contract story

- Lens: determinism
- Location: `skills/orch-gate/scripts/orchlib/status.py:307`
- Evidence: With the narrow moved to Epic 2 as 2.1, the draft is 'Story 1.4 ... Depends on: 2.1'; pasted at the end of Epic 1, plan-check fails with forward-dependency. The id allocation also ignores merged keys that left the plan.
- Recommendation: Allocate the id in max(story epic, epic of the latest chain story), exclude merged_map keys from taken, and test plan-check on the pasted draft.

#### enhancement-1 — Add: collapse closed epics in the default status view

- Lens: enhancement
- Location: `skills/orch-status/SKILL.md:20`
- Evidence: status returns every epic including closed ones, and the skill renders a table per epic, so on a long project the actionable epics and anomalies fall below the fold.
- Recommendation: Render full tables only for open, drift, archive-needed and closable epics; list closed ones on one line.

### Low (6)

#### leanness-2 — Added 'without a draft' sentence repeats what the drift message already says

- Lens: leanness
- Location: `skills/orch-status/SKILL.md:23`
- Evidence: The pin-drift message already explains unrecorded-change; only 'name the contract and its owner; no new story converges it' changes behaviour.
- Recommendation: Cut to: 'Without a draft, no new story converges the drift: name the contract and its owner.'

#### architecture-1 — The PR step does not say to target each published entry's repo, and it reads 'exists' as an open PR

- Lens: architecture
- Location: `skills/orch-status/SKILL.md:35`
- Evidence: close.execute returns {repo, base, branch, status} per repo; for code repos the branch is pushed to the registry URL. Running gh pr create from the coordination repo opens the archive PRs of other repos against the wrong project or fails.
- Recommendation: Say: 'open a PR in its `repo` from its `branch` into its `base` (gh/glab with --repo, or from that repo)'.

#### architecture-2 — status emits plan_issues but the Status section never renders or routes them

- Lens: architecture
- Location: `skills/orch-status/SKILL.md:20`
- Evidence: cmd_status returns plan_issues next to anomalies, unread_repos and notices; SKILL.md lists only the last three, so a malformed story shows as an unexplained '?' row.
- Recommendation: Add plan_issues to the caveats and offer plan-check when it is non-empty.

#### determinism-4 — One migration draft per drifting canonical, though one story converges them all

- Lens: determinism
- Location: `skills/orch-gate/scripts/orchlib/status.py:256`
- Evidence: A new story of the subproject pins every pin_paths(reg, subproject), yet each drifting path gets its own draft with the next id, leaving the prompt to notice the overlap.
- Recommendation: Group drift by (subproject, epic) and emit one draft listing every contract path.

#### enhancement-2 — Add: choose an epic when the report is requested without one

- Lens: enhancement
- Location: `skills/orch-status/SKILL.md:47`
- Evidence: report requires --epic (orch.py argparse); 'orch epic report' without a number ends in an argparse error with no guidance.
- Recommendation: Without an epic number, offer the epics from status, open ones first.

#### enhancement-3 — Add: name the branch and PR step when committing the rebuilt sprint status

- Lens: enhancement
- Location: `skills/orch-status/SKILL.md:43`
- Evidence: 'Commit the result through a PR' names no branch; --write edits the coordination checkout, usually on main, and the sprint-status-lag anomaly routes here.
- Recommendation: Commit on a branch such as orch/sprint-status-rebuild, never main, and open a PR as in Close epic titled 'chore(orch): rebuild sprint status'.
