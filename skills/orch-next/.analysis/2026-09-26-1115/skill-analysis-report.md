# Analysis Report: skills/orch-next

Generated: 2026-09-26 · Schema: 2

**Grade: Excellent**

> Fix pass holds: all 2026-09-26-1025 findings resolved or knowingly declined/deferred; one medium gap left in the named-story fast path.

The 1499-token SKILL.md now states outcomes and routes on library codes and reasons, with every CLI claim verified against orch.py and orchlib. The only open issue, raised independently by three lenses, is that the named-story fast path asks the model to explain why a story is unavailable using data `next` does not return when anything is ready.

| Severity | Count |
| --- | --- |
| Critical | 0 |
| High | 0 |
| Medium | 3 |
| Low | 0 |

## Themes

### 1. Named story has no data source when it is not in mine/ready/held

- Root cause: work.pick() returns rows only for mine, ready and held, and adds `waiting` only when ready is empty; a blocked or foreign-claimed story named by the user is otherwise absent from `next`, so the model must guess its state.
- Fix: Give the fast path a deterministic source: either point the step at the story's row in `orch.py status` (state, blocked_by, claimant, review), or add `next --story <N-M>` that returns that row as a `named` entry with the `waiting` fields.
- Findings:
  - `architecture-1` Named-story fast path asks for a 'why not available' that `next` cannot supply — `SKILL.md:## 1. Choose, first paragraph`
  - `determinism-1` The named-story fast path asks for reasons that `next` does not return — `SKILL.md:## 1. Choose (first paragraph)`
  - `enhancement-1` Add a way to explain a named story that `next` does not list — `SKILL.md:## 1. Choose, first paragraph (line 18)`

## Strengths

- Outcome-first intro with hard guards: never rank, never hand-write refs, never work an unclaimed story.
- Routing by exit-2 `code` and exit-1 `reason`, not by message text.
- Switch-or-print hand-off keeps bmad-build inside worktree.path.
- No restated library mechanics; the Finish steps and reuse order live in the library and node context.
- Library decisions (waiting list, offline-claim refusal, coded errors) are covered by tests.

## Recommendations

1. Route a named story missing from mine/ready/held to its `orch.py status` row (prompt-only), or add `next --story` in the library if one call matters. (resolves: architecture-1, determinism-1, enhancement-1)

## Experience

- **What should I work on** — next -> warnings/yours/ready -> claim create -> worktree -> hand off to bmad-build in the worktree
- **Named story** — next places it (mine -> worktree, ready -> claim); otherwise the reason is currently unsupported by data (theme 1)
- **Nothing ready** — next -> waiting (open stories and holders, then blocked) -> offer orch-status
- Headless: -H prints `orch.py next` JSON and changes nothing; claiming stays human (story claim headless deferred post-v1).

## Findings

### Medium (3)

#### architecture-1 — Named-story fast path asks for a 'why not available' that `next` cannot supply

- Lens: architecture
- Location: `SKILL.md:## 1. Choose, first paragraph`
- Evidence: work.pick() returns rows only for mine, ready and held; `waiting` appears only when ready is empty, so a blocked or foreign-claimed named story is absent.
- Recommendation: For a named story missing from mine/ready/held, read its row in `orch.py status`, or add `next --story` returning that row.

#### determinism-1 — The named-story fast path asks for reasons that `next` does not return

- Lens: determinism
- Location: `SKILL.md:## 1. Choose (first paragraph)`
- Evidence: Story id -> state/holder/blockers has one correct answer, but with anything ready `next` gives only aggregate `counts` for it; the model will guess or improvise.
- Recommendation: Push the lookup into the library (`next --story` -> `named` entry) or point the step at the matching `orch.py status` row.

#### enhancement-1 — Add a way to explain a named story that `next` does not list

- Lens: enhancement
- Location: `SKILL.md:## 1. Choose, first paragraph (line 18)`
- Evidence: Intent-first fast path added in the fix pass lacks data for the common case: some stories ready, user names one blocked or held by a teammate.
- Recommendation: Explain from the story's `orch.py status` row, then fall back to the list; or add `next --story`.
