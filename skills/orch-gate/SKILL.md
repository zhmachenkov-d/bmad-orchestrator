---
name: orch-gate
description: Deterministic pre-merge gate for orch stories. Use when the user says 'run orch gate', 'check my PR with orch', or 'why did orch-gate fail'.
---

# orch-gate

Act as the explainer and fixer for a deterministic gate. The verdict comes from `scripts/orch.py gate`. It is a script, and it gives the same answer locally and in CI. Your job is to make that verdict actionable. The developer who asked should leave knowing exactly which rule failed, why the rule exists, and the smallest change that makes the PR pass without weakening the boundary. A PR merges only if it respects its story's subproject boundaries and its contracts.

Never decide the verdict yourself, and never make a check pass by working around it. That means no hand-edited pins, no widened `allowed_write`, and no marker moved to another story. Those changes need a registry or contract PR that goes through CODEOWNERS. When the script fails or cannot run, report that. Do not substitute your own judgment.

## Resolution rules

- Bare paths (e.g. `scripts/orch.py`) resolve from this skill's installed directory.
- `{project-root}` → the project working directory.

## Running the gate

Run `uv run scripts/orch.py gate --format text` from the story's repo for the human view, and drop `--format` for JSON. Defaults: the base is `origin/main` or `main`, the coordination repo is the current repo (a monorepo), and the registry is read at the base ref. In a polyrepo code repo, pass `--coord <path to a coordination repo checkout>`, or set `ORCH_COORD`. If the registry `repo` URL differs from `origin`, also pass `--repo-id <url as in the registry>`.

Explain each failing or warning check in `{communication_language}`. When the fix is mechanical, offer to apply it:

- Missing or stale pins, after a rebase onto the latest contract: `orch.py marker write --story <id>`. Commit the story's changes first.
- A clone without the merge driver: `orch.py sprint-status install-driver`.
- `sprint-status.yaml` drifted from the merged markers: `orch.py sprint-status derive --write`.

Everything else is a judgment for the user. That covers a breaking contract change, which has to be split into expand → migrate → contract, and a scope violation, which means the change belongs in another subproject's story.

## The checks

| Check | Rule | Why |
| --- | --- | --- |
| marker | A story PR carries exactly one `.orch/stories/<N-M>.yaml`. The story exists in the epics on coordination main. Its subproject lives in this repo. | The marker is the merge record: "merged" means the file is on main. |
| scope | Every changed path is inside the subproject's `allowed_write`, the story's own marker, or the coordination repo's `{implementation_artifacts}`. A PR without a marker must not touch any subproject or the contracts dir. Markers may only be moved unchanged to `.orch/archive/epic-N/`. | Parallel work stays mergeable when a PR touches only what its story owns. |
| pins | Implementation stories pin the canonical contracts they export and import, at their blob on coordination main (fail). The PRD pin only warns. Contract stories pin the version they produce, and must branch from the latest contract. | A story must be built against the contracts that are live, never a stale version. |
| conformance | Each `exports[].copy` is byte-equal to its canonical, ignoring line endings and trailing whitespace. | Services hold copies. Canonicals live in the coordination repo. |
| breaking | Canonical contract changes run through the detector for their type. A breaking change fails, unless the story says `Contract change: narrow` and depends on a merged story for every registry consumer. A missing detector also fails. | This is how expand → migrate → contract is enforced, with no atomic cross-repo merge needed. |
| sprint-status | Runs when a coordination PR touches it. A story is `done` only when its marker is merged. An epic is `done` only with a `closed/epic-N.yaml` record that passes the close check. | Stock BMad never sets `epic-N: done`, so orch owns that transition. |
| merge-driver | Local runs only: fails when `.gitattributes` declares the driver but this clone has not registered it. | The derived file must merge without hand-resolved conflicts. |

## Story metadata (the contract with planning)

The gate reads these bold labels under each stock `### Story N.M: Title` heading in `{planning_artifacts}/epic*.md`. The orch override for `bmad-create-epics-and-stories` must emit exactly this:

```markdown
**Subproject:** payment-service
**Depends on:** 1.1, 1.3
**Contract change:** none
```

`Depends on` takes story ids or `none`, and may only point backwards. `Contract change` is `none`, `expand` or `narrow`, and only applies to stories in the `contracts` pseudo-subproject. The story key used for markers, claims and branches is `N-M`, for example `1-2`.

## Library for other orch skills

Every other orch skill calls this CLI rather than reimplementing it. Output is JSON on stdout. Exit code 1 means a failing verdict, a lost race or validation issues. Exit code 2 means a usage or environment error. Run `uv run scripts/orch.py <command> --help` for the interface.

| Command | Use |
| --- | --- |
| `config` | Resolved orch and bmm config. Paths are relative to the coordination repo. |
| `registry` | Load and validate the registry: overlapping writes, unknown imports, cycles, canonical locations. |
| `stories` | Parse epics into stories (subproject, depends_on, contract change) and report plan issues. |
| `merged` | Merged markers across all registry repos. Pull-based, cached in `.orch/cache/`. |
| `marker write --story` | Write the completion marker with current pins. Used by the `bmad-build` on_complete override. |
| `claim list\|create\|take-over\|release` | Atomic claims on `refs/heads/claim/<N-M>`. Take-over and release need `--expect <sha>`. |
| `sprint-status check\|derive\|merge\|install-driver` | Done invariants, derivation from markers, git merge driver. |
| `epic close-check --epic N` | All stories merged, markers archived, pins converged. |
| `deps` | Detector availability for the contract types in the registry: `oasdiff`, `buf`, `asyncapi`, `atlas`. |

## Gotchas

- The gate reads through git refs, never the working tree. Uncommitted changes are invisible to it, so commit before running. Orch config is read at the base (or coordination main) too, so a config change takes effect only once merged, and `*.user.toml` never affects the verdict.
- A CI merge-ref checkout (the PR merged into base) is gated as the PR tip. The result's `head_resolved` says so.
- `db-schema` contracts are a migrations directory. The detector needs `dev_url` on the registry export, or `ORCH_ATLAS_DEV_URL`.
- The `asyncapi` and `atlas` detector command lines have not yet been verified against real binaries. If one of them misfires, report the output rather than bypassing it.
