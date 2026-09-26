# Analysis Report: /workspaces/bmad-orchestrator/skills/orch-next

Generated: 2026-09-26 · Schema: 2

**Grade: Good**

> A lean, script-first dispatcher whose every library claim checks out. The one real risk is the hand-off: nothing covers a host that cannot run bmad-build inside the worktree (enhancement-1). Beyond that, a few library outcomes have no route (take-over not-claimed, exit-2 prose).

orch-next keeps all ranking, claiming, worktree and context logic in orch.py. Every field, reason code, exit code and flag it names matches the library, and it stays at 1560 tokens with no over-built patterns. The main opportunity is at the edges of the flow. Make the hand-off safe when the session cannot move into the worktree, route the remaining library outcomes by code instead of prose, and cut about 250 tokens that restate what the library or the node context already says.

| Severity | Count |
| --- | --- |
| Critical | 0 |
| High | 1 |
| Medium | 7 |
| Low | 12 |

## Themes

### 1. Hand-off assumes the session can move into the worktree

- Root cause: Step 4 says to start bmad-build 'with the worktree as its working directory' but gives no mechanism and no fallback. When the host session is pinned to the checkout it was launched from, bmad-build edits the main checkout instead of story/<N-M>. That is exactly the mistake the worktree exists to prevent.
- Fix: In Hand off, start bmad-build against the absolute `worktree.path` when the host can switch there (a worktree or cwd tool, or a subagent rooted there). Otherwise do not start it in this session: print a copy-paste command that opens a session in the worktree and invokes bmad-build for <N-M>.
- Findings:
  - `enhancement-1` Add a fallback for when bmad-build cannot run inside the worktree — `SKILL.md:## 4. Hand off`
  - `architecture-3` Hand-off gives no way to switch the working directory — `SKILL.md:## 4. Hand off`

### 2. Prompt restates library and node-context mechanics

- Root cause: Several paragraphs describe what orch.py already does and reports (reuse order, ranking key, unblocks rule, context contents, exclude) or what node-context.md already tells bmad-build (the Finish steps, read isolation). This costs tokens and gives a second copy of the logic that can drift from the code.
- Fix: Cut the Finish paragraph, the context-contents list, the reuse-order sentence, the duplicated claim-race rule and the builder-facing gotchas. Point the model to the fields it acts on (`rank`, `worktree.status`, the context path) and to the context's own Finish section.
- Findings:
  - `leanness-1` Hand-off restates the node context's own Finish section — `SKILL.md:## 4. Hand off, second paragraph`
  - `leanness-2` Node-context paragraph lists file contents the dispatcher never acts on — `SKILL.md:## 3. Worktree and node context, final paragraph`
  - `leanness-3` Step 3 narrates the library's worktree-reuse order — `SKILL.md:## 3. Worktree and node context, first paragraph`
  - `leanness-4` Claim-race rule stated twice, with a mechanism aside — `SKILL.md:intro paragraph 2 and ## 2. Claim`
  - `leanness-5` Two Gotchas are aimed at the builder, not the dispatcher — `SKILL.md:## Gotchas, bullets 3-4`
  - `determinism-5` The prompt restates library algorithms (ranking, unblocks, worktree reuse order) — `SKILL.md:## 3. Worktree, ## Gotchas`

### 3. Library outcomes routed by prose or not at all

- Root cause: Exit-1 outcomes carry a `reason` the skill branches on, but exit-2 errors are free text the model has to interpret. Some outcomes have no route at all: take-over `not-claimed`, info-severity stale claims with no actions, `--offline` on claim, and the empty-ready case, which asks for blocked stories `next` never returns.
- Fix: In the library, add a `code` to OrchError JSON, reject `--offline` for claim, and return a `waiting` list when nothing is ready. In the skill, route `not-claimed` to `claim create`, offer take-over only when the warning's `actions` has it, and narrow the snapshot and refresh wording to code stories in a polyrepo (pass --coord).
- Findings:
  - `determinism-2` The model tells exit-2 errors apart by reading the message text — `SKILL.md:## Resolution rules and ## 3. Worktree`
  - `determinism-3` The empty-ready case asks for blocked stories that `next` does not return — `SKILL.md:## 1. Choose (paragraph after the list)`
  - `determinism-4` Offline claiming is a prompt rule, and the library ignores --offline for claim — `SKILL.md:## Gotchas (last bullet)`
  - `architecture-1` Take-over 'not-claimed' outcome has no route — `SKILL.md:## 2. Claim`
  - `architecture-2` Not every stale-claim warning has a take-over action — `SKILL.md:## 1. Choose (last paragraph)`
  - `architecture-5` Contract snapshot rule is stated too broadly — `SKILL.md:## 3. Worktree and node context (last paragraph)`

### 4. Missing user paths around one's own claims

- Root cause: The flow always starts from the full ranked list and offers only resume for your own stories. A user who names a story still has to go through the whole list. A user who wants to drop a claim has no path, so the claim sits until it goes stale and becomes everyone else's warning.
- Fix: When the invocation names a story, place it with `next` and jump straight to claim or worktree. Offer 'resume or release' for `mine` entries, using the `claim_sha` that `next` already returns. Optionally let `-H --story <N-M>` claim and prepare the worktree headlessly.
- Findings:
  - `enhancement-2` Add a direct path for users who name a story — `SKILL.md:## 1. Choose`
  - `enhancement-3` Add a release option to the 'Yours' list — `SKILL.md:## 1. Choose (Yours) / ## 2. Claim`
  - `enhancement-4` Make headless useful with an explicit --story — `SKILL.md:## Headless`

## Strengths

- Every fact and change goes through orch.py: the prompt never ranks, claims or writes refs itself, and states that as a hard rule.
- All JSON fields, reason codes, exit codes and flags named in SKILL.md match orch.py, work.py, claims.py and status.py.
- Race handling is explicit: lost claims are reported with the holder and never retried silently. Take-over requires confirmation and an --expect sha.
- Headless is safe by design: read-only, no claims without a human choice.
- 1560 tokens, no shouting, no scripts to maintain. The missing Overview/On Activation headings are the deliberate house style, already judged in orch-gate's analysis.

## Recommendations

1. Rewrite Hand off with a switch-or-print fallback, so bmad-build never runs in the wrong checkout. (resolves: enhancement-1, architecture-3)
2. Trim the restated mechanics in steps 3-4 and Gotchas down to the fields and commands the dispatcher acts on. (resolves: leanness-1, leanness-2, leanness-3, leanness-4, leanness-5, determinism-5)
3. Add the missing routes in step 2 and step 1: take-over not-claimed leads to claim create, and take-over is offered only from a warning's `actions`. (resolves: architecture-1, architecture-2)
4. Add a named-story fast path and a release option for your own claims. (resolves: enhancement-2, enhancement-3)
5. Library work: coded exit-2 errors, --offline rejected for claim, a `waiting` list when nothing is ready, optionally `next --format text`. Then simplify the prompt to match. (resolves: determinism-1, determinism-2, determinism-3, determinism-4)
6. Small wording fixes: drop 'post-v1' from Headless, narrow the snapshot claim, and apply or remove {communication_language}. (resolves: architecture-4, architecture-5, customization-1)

## Experience

- **Pick next story** — orch next shows warnings, your stories and the ranked ready list, then claim create, worktree plus node context, then hand off to bmad-build.
- **Resume own story** — The story is in `mine`, so claiming is skipped: worktree reuses the existing worktree or branch, then hand off.
- **Take over stale claim** — A warn stale-claim is shown, the user confirms, claim take-over --expect runs, then the worktree continues from origin/story/<N-M>.
- Headless: -H prints `orch.py next` JSON and changes nothing. Claims need a human choice.

## Findings

### High (1)

#### enhancement-1 — Add a fallback for when bmad-build cannot run inside the worktree

- Lens: enhancement
- Location: `SKILL.md:## 4. Hand off`
- Evidence: Hand off says to start bmad-build 'with the worktree as its working directory' with no fallback. When the session is pinned to its launch checkout, bmad-build edits the main checkout instead of story/<N-M>.
- Recommendation: If the session can switch into the worktree, start bmad-build there. Otherwise print a copy-paste command that opens a new session in the worktree path and invokes bmad-build for <N-M>.

### Medium (7)

#### leanness-1 — Hand-off restates the node context's own Finish section

- Lens: leanness
- Location: `SKILL.md:## 4. Hand off, second paragraph`
- Evidence: The marker, gate, push and PR steps are already written into node-context.md '## Finish' by work.render() with concrete values. orch-next performs none of them.
- Recommendation: Cut the paragraph. At most, point to the context's Finish section.
- Proposed smallest: Tell the user the worktree path, the branch and the context file. Then start `bmad-build` for the story with the worktree as its working directory and the node context as its first read (its Finish section says how the story closes). When the `bmad-build` override from `orch-setup` is installed, it loads the context itself.
- Predicted delta: Probably nothing. bmad-build gets the finish steps from node-context.md. Route to variant eval to confirm.

#### leanness-2 — Node-context paragraph lists file contents the dispatcher never acts on

- Lens: leanness
- Location: `SKILL.md:## 3. Worktree and node context, final paragraph`
- Evidence: It lists the context contents and the info/exclude mechanism. orch-next needs only the path and the refresh command.
- Recommendation: Shorten to the path and the refresh command.
- Proposed smallest: The node context lands in the worktree as `.orch/context/node-context.md` (and `.json`), kept out of commits. To refresh it later, for example after a contract merged, run `orch.py context --write` inside the worktree.
- Predicted delta: Maybe a slightly poorer answer to 'what's in the context file?'. The model can open the file. Route to variant eval to confirm.

#### architecture-1 — Take-over 'not-claimed' outcome has no route

- Lens: architecture
- Location: `SKILL.md:## 2. Claim`
- Evidence: claims.take_over returns reason 'not-claimed' (exit 1, no holder) when the stale claim was released before confirmation. Step 2 routes only already-claimed, lost-race and claim-changed.
- Recommendation: After a take-over, 'not-claimed' means the story is free, so offer `orch.py claim create --story <N-M>`.

#### determinism-1 — Step 1 has the model hand-format the full `next` JSON on every run

- Lens: determinism
- Location: `SKILL.md:## 1. Choose (items 1-3)`
- Evidence: The model filters warnings by severity, counts info entries and lays out fixed-format ready lines. The output is deterministic, and gate already has render_text / --format text.
- Recommendation: Add `orch.py next --format text` (work.render_next) and trim duplicated plan_issues from the JSON. The prompt then only recommends a story and handles the pick.

#### determinism-2 — The model tells exit-2 errors apart by reading the message text

- Lens: determinism
- Location: `SKILL.md:## Resolution rules and ## 3. Worktree`
- Evidence: Every OrchError becomes {ok:false, error:str} with exit 2 and no code. Recovery (orch-setup, planning, ask for a clone path) depends on how the model reads the prose.
- Recommendation: Add an optional `code` to OrchError (not-a-clone with repo, config-missing, registry-missing, epics-missing, story-not-found) and branch on it in the prompt.

#### enhancement-2 — Add a direct path for users who name a story

- Lens: enhancement
- Location: `SKILL.md:## 1. Choose`
- Evidence: The skill triggers on 'claim a story' and 'resume my orch story', but always renders the full list before acting.
- Recommendation: When the invocation names a story, use `next` only to place it: mine goes to step 3, ready goes to step 2, and anything else explains why and falls back to the list.

#### enhancement-3 — Add a release option to the 'Yours' list

- Lens: enhancement
- Location: `SKILL.md:## 1. Choose (Yours) / ## 2. Claim`
- Evidence: The library has `claim release --story --expect`, and `next` already returns claim_sha for mine entries, but the skill offers only resume. Unwanted claims go stale and become warnings for everyone else.
- Recommendation: Offer 'resume or release'. Add a Release bullet that runs `orch.py claim release --story <N-M> --expect <claim_sha>` after confirmation.

### Low (12)

#### leanness-3 — Step 3 narrates the library's worktree-reuse order

- Lens: leanness
- Location: `SKILL.md:## 3. Worktree and node context, first paragraph`
- Evidence: It copies the prepare() docstring. The library picks the case and reports it in `status`.
- Recommendation: Replace with one clause: it reuses an existing worktree or branch, including a taken-over story's pushed branch, and reports which in `status`.

#### leanness-4 — Claim-race rule stated twice, with a mechanism aside

- Lens: leanness
- Location: `SKILL.md:intro paragraph 2 and ## 2. Claim`
- Evidence: The same never-retry rule appears in the intro and step 2. 'Atomic git ref operation' does not change behavior.
- Recommendation: Keep the rule in step 2. Keep a one-clause why in the intro.

#### leanness-5 — Two Gotchas are aimed at the builder, not the dispatcher

- Lens: leanness
- Location: `SKILL.md:## Gotchas, bullets 3-4`
- Evidence: 'Push work regularly' and 'Read isolation is for context economy' are builder advice. The latter is already in node-context.md Boundaries.
- Recommendation: Drop the read-isolation bullet. Reduce the idle bullet to the part that explains a stale-claim warning.

#### architecture-2 — Not every stale-claim warning has a take-over action

- Lens: architecture
- Location: `SKILL.md:## 1. Choose (last paragraph)`
- Evidence: status.py gives a blind stale-claim severity 'info' and actions []. The skill says every stale-claim warning carries take over.
- Recommendation: Offer take-over only when the warning's `actions` includes it, and run exactly its args.

#### architecture-3 — Hand-off gives no way to switch the working directory

- Lens: architecture
- Location: `SKILL.md:## 4. Hand off`
- Evidence: The session runs in the coordination repo or a clone. Nothing says how to move it to worktree.path.
- Recommendation: Name the mechanism (cd, a worktree switch or absolute paths) or tell the user to open a session there.

#### architecture-4 — Headless section says -H is both future work and defined now

- Lens: architecture
- Location: `SKILL.md:## Headless`
- Evidence: '-H is post-v1. Until then, orch-next -H prints...'
- Recommendation: State the current behavior directly and drop 'post-v1'.

#### architecture-5 — Contract snapshot rule is stated too broadly

- Lens: architecture
- Location: `SKILL.md:## 3. Worktree and node context (last paragraph)`
- Evidence: Snapshots are taken only when clone != coord_root, so a contract story in a polyrepo gets none. `context --write` in a polyrepo code worktree needs --coord.
- Recommendation: Say 'a code story in a polyrepo also gets snapshots', and add '(pass --coord in a polyrepo)' to the refresh instruction.

#### determinism-3 — The empty-ready case asks for blocked stories that `next` does not return

- Lens: determinism
- Location: `SKILL.md:## 1. Choose (paragraph after the list)`
- Evidence: `counts` holds numbers only, with no blocked rows or blocked_by.
- Recommendation: When ready is empty, have pick() add a `waiting` list with blocked_by and review info.

#### determinism-4 — Offline claiming is a prompt rule, and the library ignores --offline for claim

- Lens: determinism
- Location: `SKILL.md:## Gotchas (last bullet)`
- Evidence: cmd_claim never reads args.offline, so `claim create --offline` still pushes.
- Recommendation: Reject --offline in cmd_claim with a coded exit-2 error and delete the gotcha sentence.

#### determinism-5 — The prompt restates library algorithms (ranking, unblocks, worktree reuse order)

- Lens: determinism
- Location: `SKILL.md:## 3. Worktree, ## Gotchas`
- Evidence: It repeats work.pick's sort key and blocked_by rule and work.prepare's order. The results are already reported as `rank` and `worktree.status`.
- Recommendation: Point to the fields instead of describing the algorithms.

#### customization-1 — communication_language is resolved but never applied

- Lens: customization
- Location: `SKILL.md:15`
- Evidence: The Resolution rules define {communication_language}, but no step uses it.
- Recommendation: Add 'Speak to the user in {communication_language}' or delete the rule.

#### enhancement-4 — Make headless useful with an explicit --story

- Lens: enhancement
- Location: `SKILL.md:## Headless`
- Evidence: A caller passing an explicit story has already made the human choice. Claim and worktree are fully scripted.
- Recommendation: Optionally support `-H --story <N-M>`: claim (or skip if it is already yours), prepare the worktree, and print {status, story, worktree, branch, context}. Never start bmad-build.
