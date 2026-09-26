---
name: orch-next
description: Picks the next orch story, claims it and prepares its worktree for bmad-build. Use when the user says 'orch next', 'what should I work on', 'claim a story', 'pick the next story' or 'resume my orch story'.
---

# orch-next

Act as the developer's dispatcher. The developer asks "what should I work on?" and, within one exchange, has a claimed story checked out in its own worktree, with its node context written, and `bmad-build` started on it. Every fact and every change comes from the shared library, `orch.py`: the ranking, the claim, the worktree and the context. Your job is to present the choice clearly, carry out the one the user makes, and hand off. Never rank stories yourself, never claim by writing refs by hand, and never start work on a story the user has not claimed. Speak to the user in `{communication_language}`.

## Resolution rules

- `{skill-root}` → this skill's installed directory. `orch.py` → `uv run {skill-root}/../orch-gate/scripts/orch.py`. The orch skills install side by side, and orch-gate owns the library.
- `{communication_language}` → the value `orch.py config` prints. A null language means the user's language.
- Run from the coordination repo, or pass `--coord <path>` from a code repo. Every command prints JSON. Exit 1 is a negative result with a `reason` (a lost race, a claim someone else holds), so render its JSON as usual. Exit 2 is an environment error. Branch on its `code` where this skill names one, and otherwise report its `error`.

## 1. Choose

Run `orch.py next`. When the user already named a story, use the result only to place it: in `mine` go to step 3, in `ready` go to step 2, in `held` show its `plan_issues`. Otherwise `next` does not carry it: explain from its row in `orch.py status` (`state`, `blocked_by`, `claimant`, `review`), then fall back to the list below.

Show, in this order and briefly:

1. **Warnings**: the `warn` entries of `warnings` in one line each, then how many `info` entries there are. `unread_repos` and `notices` go here as caveats: stories in unread repos show as `unknown`, never as ready.
2. **Yours**: the `mine` stories (claimed by the user: in progress, in review, or `unknown` when their repo was not read). Offer to resume or release each one.
3. **Ready**: "N stories ready. Here is what each one unblocks." One line per `ready` entry in `rank` order: id, title, subproject, an arrow when `critical`, and `unblocks` with the `downstream` count. Recommend the top one.

`held` stories are ready but the plan does not define them cleanly. Show them with their `plan_issues` and point to `orch-status` plan validation, but do not offer them: the gate would fail their marker. When nothing is ready, summarise `waiting` (open stories and their holders first, then what the blocked ones wait for) and offer `orch-status`. A top-level `no-epics` plan issue means planning has not run yet.

Offer a take-over only for a warning whose `actions` include `take over`, only when the user wants that story, and only after they confirm, since it moves someone else's claim.

## 2. Claim

Two people can never hold the same story, so never retry a lost claim.

- **New story**: `orch.py claim create --story <N-M>`.
- **Take over**: run exactly the warning action's args (`claim take-over --story <N-M> --expect <sha>`). If the result is `not-claimed`, the holder released it meanwhile, so offer `claim create` instead.
- **Resume**: the story is already the user's, so skip to the worktree.
- **Release**: after the user confirms, `orch.py claim release --story <N-M> --expect <claim_sha from mine>`. Then go back to the list.

`already-claimed`, `lost-race` or `claim-changed` means someone else moved first. Show the `claim` holder and go back to the ready list without that story.

## 3. Worktree and node context

Run `orch.py worktree --story <N-M>`. It needs the user's claim. It checks out branch `story/<N-M>` in a worktree of the story's repo, reusing an existing worktree or branch (a take-over continues from the pushed branch), and reports which in `worktree.status`. Pass `--path` when the user wants a directory other than the default.

- `not-a-clone` (exit 2): the story lives in a polyrepo code repo (`repo`) that this checkout is not a clone of. Ask the user for the path of their local clone and re-run with `--repo <clone> --coord <coordination repo>`, or tell them to clone it first.
- `path-not-empty` (exit 2): ask for another `--path`.
- `not-claimed` or `claimed-by-other`: the claim moved after step 2. Show the holder.
- Non-empty `plan_issues`: warn that the gate will fail this story until the plan is fixed.

The node context lands in the worktree as `.orch/context/node-context.md` (and `.json`) and stays out of commits. To refresh it later, for example after a contract merged, run `orch.py context --write` inside the worktree, adding `--coord <coordination repo>` in a polyrepo code worktree.

## 4. Hand off

Tell the user the worktree path, the branch and the context file. `bmad-build` must run inside `worktree.path`. Work started from this session's checkout would land on the wrong branch. If this host can move the session there (a worktree switch, or a subagent rooted in that directory), start `bmad-build` for the story there, with the node context as its first read. Otherwise do not start it here. Give the user a ready-to-paste command that opens a new session in `worktree.path` and starts `bmad-build` for `<N-M>`. The context's Finish section tells the builder how the story closes, and the `bmad-build` override from `orch-setup`, when installed, loads the context itself.

## Headless

`orch-next -H` prints `orch.py next` JSON as its only output and changes nothing. Claims need a human choice.

## Gotchas

- Ready means every `Depends on` story is merged to main. An approved but unmerged PR does not count, and branches never stack.
- A claim goes stale after `stale_claim_hours` without activity, counted from the later of the claim and the pushed `story/<N-M>` tip.
- `--offline` shows claims as last fetched and skips other repos. Claims refuse it (`offline-claim`), since the claim push needs the remote.
