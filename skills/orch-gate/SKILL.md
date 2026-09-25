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
- `{communication_language}` → the first `communication_language` found in `{project-root}/_bmad/custom/config.user.toml`, `{project-root}/_bmad/config.user.toml`, `{project-root}/_bmad/custom/config.toml`, `{project-root}/_bmad/config.toml`, else the user's language.
- `{planning_artifacts}`, `{implementation_artifacts}` → the values `uv run scripts/orch.py config` prints.

## Running the gate

When the user asks about a CI failure and has the CI result (the `-o` JSON artifact or the job log), explain that run. Re-running locally can reach a different verdict. Otherwise run `uv run scripts/orch.py gate --format text -o <temp file>.json` from the story's repo root: show the text, and reason from the JSON, since only it carries `fix.mechanical` and the input fields. If a local verdict differs from CI, compare `base_sha`, `base_source`, `head_sha`, `coord_sha`, `coord_ref_source`, `repos_read`, `notices` and `unread_repos` in the two results. Those fields name the inputs that differ.

Defaults: in the coordination repo the base is `origin/<orch_main_branch>` or `<orch_main_branch>` (`main` unless configured), and in a code repo it is its subproject's registry `branch`. The registry is read at the base in a monorepo, and at coordination main otherwise. The coordination repo is resolved in this order: `--coord`, `ORCH_COORD`, a local path in this repo's `orch_coordination_repo`, and finally the current repo (a monorepo). If the registry `repo` URL differs from `origin`, also pass `--repo-id <url as in the registry>`.

Explain each failing or warning finding in `{communication_language}`, and mention any `notices`. Every finding has a stable `code`. When a finding carries `fix.mechanical`, offer to run `fix.command` as given, from the repo root, once `fix.precondition` (if any) is met. After a fix, commit what it changed, re-run the gate and report the new verdict. The fix is done only when the gate passes or only non-mechanical failures remain.

Findings without a `fix` are a judgment for the user. That covers a breaking contract change, which has to be split into expand → migrate → contract, and a scope violation, which means the change belongs in another subproject's story.

## The checks

| Check | Rule | Why |
| --- | --- | --- |
| setup | The coordination main has registered subprojects, a structurally valid registry and epics, and this repo is in the registry. A missing subproject path or canonical only warns. A coordination PR that changes only registry and planning files passes as `setup-repair` if its head fixes the problems. | Without a trustworthy registry, "nothing protected" would pass every PR. |
| marker | A story PR carries exactly one `.orch/stories/<N-M>.yaml`. The story exists in the epics on coordination main. Its subproject lives in this repo. | The marker is the merge record: "merged" means the file is on main. |
| scope | Every changed path is inside the subproject's `allowed_write`, the story's own marker, or the coordination repo's `{implementation_artifacts}`. A PR without a marker must not touch any subproject or the contracts dir. As in gitignore, a pattern that names a directory covers everything below it. Markers may only be moved unchanged to their own epic's `.orch/archive/epic-N/`. | Parallel work stays mergeable when a PR touches only what its story owns. |
| pins | Implementation stories pin the canonical contracts they export and import, at their blob on coordination main (fail). The PRD pin only warns. Contract stories pin the version they produce, and must branch from the latest contract. | A story must be built against the contracts that are live, never a stale version. |
| conformance | Each `exports[].copy` is byte-equal to its canonical, ignoring line endings and trailing whitespace. A directory copy is compared file by file. | Services hold copies. Canonicals live in the coordination repo. |
| breaking | Canonical contract changes run through the detector for their type. A breaking change fails, unless the story says `Contract change: narrow`, depends on a story of every registry consumer, and all of those dependencies are merged. A missing detector also fails. | This is how expand → migrate → contract is enforced, with no atomic cross-repo merge needed. |
| sprint-status | Runs when a coordination PR touches it. A story is `done` only when its marker is merged. An epic is `done` only with a `closed/epic-N.yaml` record that passes the close check. | Stock BMad never sets `epic-N: done`, so orch owns that transition. |
| merge-driver | Local runs only: fails when `.gitattributes` declares the driver but this clone has not registered it. | The derived file must merge without hand-resolved conflicts. |

## Story metadata (the contract with planning)

The gate reads these bold labels under each stock `### Story N.M: Title` heading in `{planning_artifacts}/epic*.md`:

```markdown
**Subproject:** payment-service
**Depends on:** 1.1, 1.3
**Contract change:** none
```

`Depends on` takes story ids or `none`, and may only point backwards. `Contract change` is `none`, `expand` or `narrow`, and only applies to stories in the `contracts` pseudo-subproject. The story key used for markers, claims and branches is `N-M`, for example `1-2`.

## CLI behavior

Output is JSON on stdout. Exit code 1 means a failing verdict, a lost race, validation issues, a missing detector, or `epic close-check` problems. Exit code 2 means a usage, environment or internal error, so report it and do not explain it as a verdict. `orch.py --help` lists the commands. `gate --format markdown` renders a CI step summary. `deps --probe` checks installed detectors against built-in compatible and breaking fixtures.

## Gotchas

- The gate reads through git refs, never the working tree. Uncommitted changes are invisible to it, so commit before running. Orch config is read at the base (or coordination main) too, from the team layers only, so a config change takes effect only once merged, and personal `*.user.toml` layers never affect the verdict, even when committed.
- A CI merge-ref checkout (the PR merged into base) is gated as the PR tip. The result's `head_resolved` says so. The gate needs full history: in a shallow clone it stops with the fetch-depth fix.
- If a registry repo cannot be read, its stories' merge status is unknown. They fail as `done-unverifiable` or `consumers-unverifiable`, never as "not merged", and the fix is read access for the runner. `--offline` is refused in CI.
- `openapi` and `asyncapi` canonicals are single files, `protobuf` may be a file or a package directory, and `db-schema` is a migrations directory. The `db-schema` detector needs `dev_url` on the registry export, or `ORCH_ATLAS_DEV_URL`.
- The `asyncapi` and `atlas` detector command lines are not yet verified against real binaries. `orch.py deps --probe` verifies `asyncapi`, but not `atlas`, which needs a dev database. Detector versions are recorded in `detectors_used`, so keep the same version locally and in CI.
- Registry-only adoption without BMad planning is post-v1. Every subproject change needs a story marker.
