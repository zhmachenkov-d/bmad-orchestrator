# Analysis Report: /workspaces/bmad-orchestrator/skills/orch-status

Generated: 2026-09-26 · Schema: 2

**Grade: Fair**

> Fair: the skill is a lean, well-wired layer over the library, but two library bugs write or report wrong shared state: pin convergence assumes merge order equals plan order (determinism-1), and the close record pass ignores unread repos (determinism-2).

orch-status is lean (1426 tokens), declines customization cleanly, and never derives state itself: every fact comes from orch.py JSON, and close runs as plan, confirm, push through gated PRs. The main opportunity is in the library: make close and drift independent of merge order and of partial reads, and stop heuristics from failing open. Beyond that, a handful of one-line SKILL.md fixes tighten the contract with the library and the handoffs between sessions.

| Severity | Count |
| --- | --- |
| Critical | 0 |
| High | 2 |
| Medium | 9 |
| Low | 13 |

## Themes

### 1. Library results depend on merge order and partial reads

- Root cause: status/close treat plan order as merge order and treat an unread repo or unread branches as if they were absent, so close can deadlock, sprint status can be demoted, and take-over can be offered on active work. Results also shift with the clone or the --epic filter.
- Fix: Order the pin chain and the latest pinner by merge order on main. Block the record pass (or keep unknown keys) while repos are unread. Mark claim-only activity and do not offer take-over there. Read only origin story refs, compute over all stories before filtering, and validate --epic.
- Findings:
  - `determinism-1` Pin chain and latest pinner assume merge order equals plan order, so an epic can be stuck in drift for good — `orch-gate/scripts/orchlib/markers.py: PinIndex, pin_drift`
  - `determinism-2` Close record pass rewrites sprint status while other repos are unread, demoting their done stories — `orch-gate/scripts/orchlib/close.py: plan/execute`
  - `determinism-3` stale-claim offers take-over when story branches could not be read — `orchlib/status.py: branch_activity, _story_anomalies`
  - `determinism-7` Branch activity reads clone-local refs, so idle times differ between clones — `orchlib/status.py: branch_activity; gitio.refresh`
  - `determinism-9` --epic filter changes the contract-bottleneck result — `orchlib/status.py: contract-bottleneck`
  - `determinism-10` status/report accept an epic that has no stories — `orchlib/status.py; orch.py cmd_report`

### 2. Heuristics that fail open or hand the prompt unusable output

- Root cause: Where the script cannot know something (which exporter a narrow breaks, whether a migration story can converge, which review host a repo uses, which contract stories were real bottlenecks), it guesses permissively or labels data too broadly, and the prompt acts on the guess.
- Fix: Fail closed or say 'ambiguous' (plan-check narrow target). Emit migration_draft only when it can converge, together with next_id and the rendered block. Paginate review lists and add notices on truncation. Label the retro fields for what they are.
- Findings:
  - `determinism-4` plan-check guesses which exporter a narrow story breaks, and passes when any guess fits — `orchlib/plan.py: _narrow`
  - `determinism-5` migration_draft is offered even for drift that a 'Contract change: none' story cannot fix — `orchlib/status.py: pin-drift anomaly`
  - `determinism-6` Agent must work out the new story's N.M and title to render the migration draft — `SKILL.md pin-drift bullet; status.py migration_draft`
  - `determinism-8` Review host chosen by hostname substring, and review lists silently truncated — `orchlib/status.py: _host_tool, _open_reviews`
  - `determinism-11` Retro data labels every contract story as a bottleneck — `orchlib/close.py: retro_data`

### 3. SKILL.md and the library disagree on a few contracts

- Root cause: The skill describes some script outputs differently from what the script does: exit 1, the sprint-status-lag action args, the pdf_error text, and the re-planned --push result.
- Fix: Document exit 1 as a negative result, return derive without --write from the lag anomaly, relay pdf_error verbatim, and have the --push run check that the pass matches the confirmed plan.
- Findings:
  - `architecture-1` sprint-status-lag action writes without the preview and confirmation the Rebuild section requires — `SKILL.md: Status; orchlib/status.py sprint-status-lag`
  - `architecture-2` Exit code 1 is undocumented, yet plan-check, close and claim actions return results through it — `SKILL.md: Resolution rules`
  - `architecture-4` The --push re-run re-plans without checking that the pass is the one the user confirmed — `SKILL.md: Close epic; orch.py cmd_epic`
  - `leanness-1` pdf_error line restates the script message and misstates it on browser failure — `SKILL.md: Epic report`

### 4. Handoffs across sessions and skills are unstated

- Root cause: The flows span days and skills (close pass 1 then pass 2, take over then orch-next, drift draft then plan-check), but the skill ends each step without naming the next one.
- Fix: Add one line per handoff: come back after the archive PRs merge; continue a taken-over story with orch-next; run plan-check after pasting the draft; offer candidate epics or point to orch-setup.
- Findings:
  - `enhancement-1` Add: tell the user how to resume between close passes — `SKILL.md: Close epic`
  - `enhancement-2` Add: next step after a successful take over — `SKILL.md: Status actions`
  - `enhancement-4` Add: re-validate after the user adds a pin-drift migration story — `SKILL.md: pin-drift bullet`
  - `enhancement-5` Add: route users who invoke the skill without an epic or before setup — `SKILL.md: Close epic / Epic report / Resolution rules`

### 5. Headless scope is narrower than it safely could be

- Root cause: Headless allows file-writing rebuild but leaves out the read-only status and plan-check.
- Fix: Allow status and plan-check under -H, take the action from the invocation, and print error JSON when it is ambiguous.
- Findings:
  - `architecture-3` Headless mode leaves out plan-check and gives no way to choose the action — `SKILL.md: Headless`
  - `enhancement-3` Opportunity: extend -H to the read-only commands status and plan-check — `SKILL.md: Headless`

## Strengths

- Script-first: SKILL.md never infers state, and unread repos surface as unknown, never as not merged
- Close epic is plan, confirm, push, split into two passes the gate checks, with plumbing commits that work in the polyrepo fetch cache
- Actions carry exact orch.py args and claim leases (--expect), so take over and release are race-safe
- No customization surface; thresholds come from module config as in orch-gate
- Lean SKILL.md (1426 tokens) with gotchas the model could not infer

## Recommendations

1. Fix the two high library bugs: pin chain and latest pinner in merge order on main, and no record pass (or unknown keys kept) while repos are unread, each with a regression test (resolves: determinism-1, determinism-2)
2. Tighten the SKILL.md/library contract: document exit 1, lag action without --write, relay pdf_error, verify the pass on --push, define {skill-root} (resolves: architecture-1, architecture-2, architecture-4, architecture-5, leanness-1)
3. Close the fail-open gaps in status and plan-check: claim-only activity, ambiguous narrow target, converging-only migration drafts with next_id, origin-only story refs, compute before filtering, validate --epic (resolves: determinism-3, determinism-4, determinism-5, determinism-6, determinism-7, determinism-9, determinism-10)
4. Add the handoff lines and widen headless to status/plan-check (resolves: enhancement-1, enhancement-2, enhancement-3, enhancement-4, enhancement-5, architecture-3)
5. Small cleanups: review-host pagination and notices, retro field naming, the two meta sentences and the redundant notice clause (resolves: determinism-8, determinism-11, leanness-2, leanness-3)

## Experience

- **Delivery lead checks progress** — Says 'orch status' → table per epic with critical path → anomalies with actions and caveats for unread repos; on a non-orch project, exit 2 without a pointer to orch-setup (gap)
- **Take over a stale claim** — stale-claim anomaly → confirm → claim take-over with --expect lease → lost race shows the new holder; after success no next step toward a worktree (gap)
- **Close an epic over several days** — closable → plan (archive) → confirm → --push, PRs per repo → no reminder to return after the merges (gap) → re-run gives record → record PR → offer bmad-retrospective with orch-epic-N.json
- **Pin drift** — pin-drift with migration_draft → story block for the epics → no plan-check afterwards (gap); a draft is also offered where it cannot converge (determinism-5)
- Headless: Rebuild and report already work headless with pure JSON output; status and plan-check are read-only and could join; take over, release and close stay interactive by design.

## Findings

### High (2)

#### determinism-1 — Pin chain and latest pinner assume merge order equals plan order, so an epic can be stuck in drift for good

- Lens: determinism
- Location: `orch-gate/scripts/orchlib/markers.py: PinIndex, pin_drift`
- Evidence: When two contract stories on one canonical merge in reverse plan order (the later one rebased), versions[-1] != main. Reproduced: both stories and a consumer get 'unrecorded-change', so close stays blocked.
- Recommendation: Order the chain and the latest pinner by merge order on main (the commit that added each marker), or at least accept current when any chain entry equals it. Add a reverse-merge test.

#### determinism-2 — Close record pass rewrites sprint status while other repos are unread, demoting their done stories

- Lens: determinism
- Location: `orch-gate/scripts/orchlib/close.py: plan/execute`
- Evidence: plan() blocks only on the epic's own unmerged or unknown stories. execute() runs derive with a partial merged_map, which demotes done stories of unread repos to review. cmd_sprint_status refuses in the same situation.
- Recommendation: Block the record pass while any repo is unread, or make derive leave unknown keys untouched. Add a polyrepo test.

### Medium (9)

#### leanness-1 — pdf_error line restates the script message and misstates it on browser failure

- Lens: leanness
- Location: `SKILL.md: Epic report`
- Evidence: SKILL.md hard-codes 'no Chromium or Chrome was found'. report.to_pdf already returns that guidance when no browser exists, and returns '<browser> failed' or '<browser> exited N' when one exists but fails. The hard-coded wording is wrong in those cases.
- Recommendation: Replace with: 'When pdf_error is set, relay it; the HTML still prints to PDF from any browser.'

#### architecture-1 — sprint-status-lag action writes without the preview and confirmation the Rebuild section requires

- Lens: architecture
- Location: `SKILL.md: Status; orchlib/status.py sprint-status-lag`
- Evidence: The anomaly action args are ['sprint-status','derive','--write']. The generic 'run the action' rule would write the file without showing the changes, which contradicts Rebuild's derive, show, confirm, write sequence.
- Recommendation: Have status.py return derive without --write, or route the anomaly to the Rebuild flow explicitly.

#### architecture-2 — Exit code 1 is undocumented, yet plan-check, close and claim actions return results through it

- Lens: architecture
- Location: `SKILL.md: Resolution rules`
- Evidence: Only exit 2 is described. orch.py exits 1 on plan-check FAIL, a blocked close, close-check problems, and a claim lost-race or claim-changed. All of these are results the skill must render.
- Recommendation: Add: exit 1 is a negative result (FAIL, blocked, lost race); render its JSON as usual.

#### determinism-3 — stale-claim offers take-over when story branches could not be read

- Lens: determinism
- Location: `orchlib/status.py: branch_activity, _story_anomalies`
- Evidence: A branches-unreadable repo leaves activity at claim time only, so an active claim is presented as stale with take-over actions.
- Recommendation: Mark such rows activity_source=claim-only and skip stale-claim for them, or emit it without actions and say activity is unknown.

#### determinism-4 — plan-check guesses which exporter a narrow story breaks, and passes when any guess fits

- Lens: determinism
- Location: `orchlib/plan.py: _narrow`
- Evidence: With exporters A{B,C} and D{B}, a narrow of A that depends only on B passes, because D's consumers are covered. The gate will later fail it.
- Recommendation: Fail closed: require coverage of every related exporter, or emit CONCERNS ambiguous-narrow-target when more than one fits.

#### determinism-5 — migration_draft is offered even for drift that a 'Contract change: none' story cannot fix

- Lens: determinism
- Location: `orchlib/status.py: pin-drift anomaly`
- Evidence: The draft is emitted for every drift, including unrecorded-change and drift of contracts stories, where a new story pins nothing and cannot converge.
- Recommendation: Emit migration_draft only for reason not-migrated outside contracts. Otherwise emit a distinct code explained in SKILL.md.

#### enhancement-1 — Add: tell the user how to resume between close passes

- Lens: enhancement
- Location: `SKILL.md: Close epic`
- Evidence: Pass 2 may run days later in a new session. The skill never says to come back once the archive PRs merge, and a re-run that returns only 'exists' entries is not explained.
- Recommendation: End pass 1 with 'merge these PRs, then ask to close epic N again'. When every archive entry is 'exists', say the PRs are waiting to merge.

#### enhancement-2 — Add: next step after a successful take over

- Lens: enhancement
- Location: `SKILL.md: Status actions`
- Evidence: After a take over, the user holds a claim but has no worktree or bmad-build session, and the skill says nothing about what to do next.
- Recommendation: Point to orch-next, or until it ships, to git worktree add on origin/story/<N-M> and then bmad-build.

#### enhancement-3 — Opportunity: extend -H to the read-only commands status and plan-check

- Lens: enhancement
- Location: `SKILL.md: Headless`
- Evidence: status and plan-check only read and already print JSON. CI, digests and the readiness step could use them headless.
- Recommendation: Headless runs rebuild, report, status or plan-check. Only take over, release and close need a human. Note that headless rebuild writes but does not commit.

### Low (13)

#### leanness-2 — Meta-explanation sentences that change none of the model's moves

- Lens: leanness
- Location: `SKILL.md: Plan validation, Rebuild sprint status (last sentences)`
- Evidence: 'The bmad-sprint-planning readiness check calls this too.' and 'The merge driver uses the same derivation, so it never needs a hand fix.' describe the system to itself.
- Recommendation: Delete the first. Shorten the second to the instruction alone, or delete it.

#### leanness-3 — Gotcha restates a notice the script already emits

- Lens: leanness
- Location: `SKILL.md: Gotchas, bullet 2`
- Evidence: '...and a notice says so' repeats the review-host-unavailable notice that is already shown as a caveat.
- Recommendation: Shorten to: review waits come from open gh/glab PRs whose head is story/<N-M>; without either tool they fall back to branch idle time.

#### architecture-3 — Headless mode leaves out plan-check and gives no way to choose the action

- Lens: architecture
- Location: `SKILL.md: Headless`
- Evidence: -H allows rebuild and report but not read-only plan-check, and does not say how the action is chosen.
- Recommendation: Allow plan-check (and status) under -H. The action comes from the invocation; an ambiguous request prints error JSON.

#### architecture-4 — The --push re-run re-plans without checking that the pass is the one the user confirmed

- Lens: architecture
- Location: `SKILL.md: Close epic; orch.py cmd_epic`
- Evidence: close.plan runs again on --push. If state changed in between, a different pass or repo list could be pushed than the one approved.
- Recommendation: After --push, compare pass/repos with the confirmed plan and show any difference before opening PRs, or pass the expected pass to the script.

#### architecture-5 — The {skill-root} token is used but never defined

- Lens: architecture
- Location: `SKILL.md: Resolution rules`
- Evidence: orch.py resolves via {skill-root}/../orch-gate but the token has no definition.
- Recommendation: Define {skill-root} as this skill's installed directory.

#### determinism-6 — Agent must work out the new story's N.M and title to render the migration draft

- Lens: determinism
- Location: `SKILL.md pin-drift bullet; status.py migration_draft`
- Evidence: The draft has no id, so the model has to count the stories to pick a free N.M. A duplicate id becomes a FAIL.
- Recommendation: The script emits next_id and the rendered markdown block; the prompt only suggests the title.

#### determinism-7 — Branch activity reads clone-local refs, so idle times differ between clones

- Lens: determinism
- Location: `orchlib/status.py: branch_activity; gitio.refresh`
- Evidence: For '.', local refs/heads/story/* count, and fetching without prune keeps deleted remote branches.
- Recommendation: Read only refs/remotes/origin/story/* and prune story refs on refresh.

#### determinism-8 — Review host chosen by hostname substring, and review lists silently truncated

- Lens: determinism
- Location: `orchlib/status.py: _host_tool, _open_reviews`
- Evidence: Any host without 'gitlab' in its name goes to gh. glab --per-page 100 and gh --limit 500 truncate without warning, so a story whose PR is past the limit can look in-progress or stale.
- Recommendation: Paginate, and add a notice when a limit is hit; consider an explicit host config with the guess as default.

#### determinism-9 — --epic filter changes the contract-bottleneck result

- Lens: determinism
- Location: `orchlib/status.py: contract-bottleneck`
- Evidence: by_key holds only the filtered epic's rows, so dependents in other epics are ignored.
- Recommendation: Compute states for all stories, then filter the output.

#### determinism-10 — status/report accept an epic that has no stories

- Lens: determinism
- Location: `orchlib/status.py; orch.py cmd_report`
- Evidence: A mistyped epic gives an empty 'open' row and an empty report with ok:true, while close.plan raises an error for the same input.
- Recommendation: Validate --epic against the stories and exit with a clear message.

#### determinism-11 — Retro data labels every contract story as a bottleneck

- Lens: determinism
- Location: `orchlib/close.py: retro_data`
- Evidence: contract_bottlenecks lists all contract stories by downstream count, not the decided bottleneck definition.
- Recommendation: Rename it to contract_stories_by_downstream, or record real bottleneck anomalies.

#### enhancement-4 — Add: re-validate after the user adds a pin-drift migration story

- Lens: enhancement
- Location: `SKILL.md: pin-drift bullet`
- Evidence: Nothing checks the pasted story before close is blocked again.
- Recommendation: After the story is added, run plan-check, then status.

#### enhancement-5 — Add: route users who invoke the skill without an epic or before setup

- Lens: enhancement
- Location: `SKILL.md: Close epic / Epic report / Resolution rules`
- Evidence: --epic is required but the skill does not say how to choose one. A first-timer gets exit 2 with no pointer to orch-setup.
- Recommendation: Offer candidate epics from status. On a missing config, registry or epics, suggest orch-setup or planning.
