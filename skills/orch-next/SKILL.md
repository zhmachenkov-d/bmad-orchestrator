---
name: orch-next
description: Picks the next orch story, claims it and prepares its worktree for bmad-build. Use when the user says 'orch next', 'what should I work on', 'claim a story', 'pick the next story' or 'resume my orch story'.
---

# orch-next

Act as the developer's dispatcher. The developer asks "what should I work on?" and, within one exchange, has a claimed story checked out in its own worktree, with its node context written, and `bmad-build` started on it. Every fact and every change comes from the shared library, `orch.py`: the ranking, the claim, the worktree and the context. Your job is to present the choice clearly, carry out the one the user makes, and hand off. Never rank stories yourself, never claim by writing refs by hand, and never start work on a story the user has not claimed.

Two people can never hold the same story. A claim is an atomic git ref operation. When a claim is lost to someone else, report who won and offer the next story. Never retry silently.

## Resolution rules

- `{skill-root}` → this skill's installed directory. `orch.py` → `uv run {skill-root}/../orch-gate/scripts/orch.py`. The orch skills install side by side, and orch-gate owns the library.
- `{communication_language}` → the value `orch.py config` prints. A null language means the user's language.
- Run from the coordination repo, or pass `--coord <path>` from a code repo. Every command prints JSON. Exit 1 is a negative result (a lost race, a claim someone else holds), so render its JSON as usual. Exit 2 is an environment error, so report it with the flag it names. When it says the config, registry or epics are missing, point to `orch-setup` or planning.

## 1. Choose

Run `orch.py next`. Show, in this order and briefly:

1. **Warnings**: the `warn` entries of `warnings` in one line each (stale claims, stuck reviews, pin drift, contract bottlenecks), then how many `info` entries there are. `unread_repos` and `notices` go here as caveats: stories in unread repos show as `unknown`, never as ready.
2. **Yours**: the `mine` stories (claimed by the user, in progress or in review). Offer to resume one.
3. **Ready**: "N stories ready. Here is what each one unblocks." One line per `ready` entry in `rank` order: id, title, subproject, an arrow when `critical`, and `unblocks` (the stories it frees) with the `downstream` count. Recommend the top one.

`held` stories are ready but the plan does not define them cleanly. Show them with their `plan_issues` and point to `orch-status` plan validation, but do not offer them: the gate would fail their marker. When nothing is ready, say what the work waits for (the blocked stories and reviews from `counts` and the warnings) and offer `orch-status`.

A `stale-claim` warning carries a `take over` action. Offer it only when the user wants that story, and confirm first, since it moves someone else's claim.

## 2. Claim

- **New story**: `orch.py claim create --story <N-M>`.
- **Take over**: run the warning's action args, `orch.py claim take-over --story <N-M> --expect <sha>`, after the user confirms.
- **Resume**: the story is already the user's, so skip to the worktree.

`already-claimed`, `lost-race` or `claim-changed` means someone else moved first. Show the `claim` holder and go back to the ready list without that story. Do not retry.

## 3. Worktree and node context

Run `orch.py worktree --story <N-M>`. It needs the user's claim. It checks out branch `story/<N-M>` in a worktree of the story's repo and writes the node context into it. The worktree reuses what exists, in this order: an existing worktree, a local branch, the pushed `origin/story/<N-M>` (a take-over continues there), and otherwise a new branch from the subproject's main. The default path is `<orch_worktrees_dir>/story-<N-M>`. Pass `--path` when the user wants another.

- A contract story, or any story in a monorepo, lives in the coordination repo. In a polyrepo, a code story lives in its subproject's repo. Exit 2 naming that repo means this checkout is not a clone of it. Ask the user for the path of their local clone and re-run with `--repo <clone> --coord <coordination repo>`, or tell them to clone it first.
- `not-claimed` or `claimed-by-other`: the claim moved after step 2. Show the holder.
- Non-empty `plan_issues`: warn that the gate will fail this story until the plan is fixed.

The node context lands in the worktree as `.orch/context/node-context.md` (and `.json`). It contains the story text, the `allowed_read` and `allowed_write` boundaries, the canonical contracts it builds against with their pinned versions, and how to finish. In a polyrepo it also holds snapshots of those contracts under `.orch/context/contracts/`. The clone's `info/exclude` keeps `.orch/context/` out of every commit. To refresh it later, for example after a contract merged, run `orch.py context --write` inside the worktree.

## 4. Hand off

Tell the user the worktree path, the branch and the context file. Then start `bmad-build` for the story with the worktree as its working directory and the node context as its first read. When the `bmad-build` override from `orch-setup` is installed, it loads the context itself.

The story finishes the orch way. The builder commits the changes, then runs `orch.py marker write --story <N-M>` and commits the marker. Next it runs `orch.py gate --format text` (see orch-gate), pushes `story/<N-M>`, and opens a PR into the subproject's branch. The dependency counts as satisfied only once that PR is merged.

## Headless

`-H` is post-v1. Until then, `orch-next -H` prints `orch.py next` JSON as its only output and changes nothing. Claims need a human choice.

## Gotchas

- Ready means every `Depends on` story is merged to main. An approved but unmerged PR does not count, and branches never stack.
- Ranking puts stories on an epic's critical path first, then those with the most stories downstream, then plan order. `unblocks` lists only dependents for which this story is the last unmerged dependency.
- Idle time counts from the later of the claim and the pushed `story/<N-M>` tip. Push work regularly so a live claim does not look stale to others.
- Read isolation is for context economy, not security. The gate enforces `allowed_write` at merge.
- `--offline` shows claims as last fetched and skips other repos. Do not claim offline: the claim push needs the remote.
