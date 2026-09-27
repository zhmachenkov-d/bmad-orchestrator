---
title: 'orch-gate CI templates for GitHub Actions and GitLab CI'
type: 'feature'
created: '2026-09-26'
status: 'done'
route: 'dispatch'
baseline_commit: '0b42294126aceba9a286b226bde68188654303eb'
review_loop_iteration: 0
context: []
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** `orch-gate` must run on every PR/MR (plan L135, L229), but there is no CI job to install; `orch-setup` (not built yet) has no CI assets to write.

**Approach:** Ship three templates under `skills/orch-gate/assets/ci/` for a repo whose registry lists only `repo: .` (monorepo) — a GitHub Actions workflow, a GitLab CI job file, and suggested CODEOWNERS lines — using `@…@` placeholders that `orch-setup` will substitute, plus tests that substitute defaults and validate them.

## Boundaries & Constraints

**Always:**
- Decision (tamper-proofing, 1A): the gate's code comes from the base, never from the PR. The job extracts `@ORCH_CLI_DIR@` at the CLI commit with `git archive "$CLI_REF" -- @ORCH_CLI_DIR@ | tar -x -C "$WT"`, where `WT=$(mktemp -d)` (under `$RUNNER_TEMP` on GitHub). It uses no worktree, so nothing is registered in `.git` and there is no full-tree checkout. It then runs `uv run --script "$WT/@ORCH_CLI_PATH@" gate --ci --base "$BASE" --format markdown -o orch-gate.json > orch-gate.md || code=$?` from the checkout root, finishing with `exit ${code:-0}`. This form is used on both platforms, because both run `bash -e`.
- Base and CLI commit per event:

  | Event | `--base` | `CLI_REF` |
  |---|---|---|
  | GitHub `pull_request` | `origin/$BASE_REF` (`env: BASE_REF: ${{ github.base_ref }}`) | same |
  | GitHub `merge_group` (decision 2B) | `$MG_BASE` (`env: MG_BASE: ${{ github.event.merge_group.base_sha }}`) — correct for the merge, squash and rebase methods | same; includes queued-ahead PRs, trusted because each was reviewed and gated |
  | GitLab MR | `origin/$CI_MERGE_REQUEST_TARGET_BRANCH_NAME`, fetched with `+refs/heads/$T:refs/remotes/origin/$T` | same |

  No expression interpolation inside `run`. GitLab merge trains are out of scope; the GitLab header says not to require the job under merge trains.
- Bootstrap: before extracting, check with `git cat-file -e`. If the CLI commit has neither `@ORCH_CLI_PATH@` nor `@ORCH_REGISTRY_DIR@`, orch is not installed at base: print a notice and exit 0. If it has the registry but no CLI, exit 2 with a message naming the path.
- After the run, strip the `$WT/` prefix from `orch-gate.md` and `orch-gate.json`, so fix commands read `@ORCH_CLI_PATH@`.
- GitHub appends `orch-gate.md` to `$GITHUB_STEP_SUMMARY`: truncated to 900000 bytes first; on exit 2 fenced as code afterwards; a truncation adds a "truncated, see artifact" line. GitLab prints it to the log. Artifacts upload even when the gate fails: GitHub `if: always()` with `if-no-files-found: warn`; GitLab `when: always` with `expose_as`. `orch-gate.json` may be absent on an early exit 2.
- Full history (GitHub `fetch-depth: 0`, GitLab `GIT_DEPTH: 0`). GitHub: triggers `pull_request` (never `pull_request_target`) and `merge_group` only, `permissions: contents: read`, gate step `id: gate`. GitLab: `merge_request_event` rule, image `ghcr.io/astral-sh/uv:python3.12-bookworm` (slim images lack git).
- Placeholders:
  - `@ORCH_CLI_DIR@` — the committed orch-gate skill dir, default `.claude/skills/orch-gate`; a real directory, not a symlink.
  - `@ORCH_CLI_PATH@` — under that dir, default `.claude/skills/orch-gate/scripts/orch.py`; this is `@ORCH_CLI@` without `{project-root}/`. Neither path is absolute, `{project-root}` or `{skill-root}`.
  - `@ORCH_REGISTRY_DIR@`, `@ORCH_CONTRACTS_DIR@`, `@PLANNING_ARTIFACTS@`, `@ORCH_OWNERS@`.
- CODEOWNERS lines cover the registry, contracts, planning and CLI dirs, plus `/.github/workflows/orch-gate.yml`, `/.gitlab/orch-gate.gitlab-ci.yml`, `/.gitlab-ci.yml` and the CODEOWNERS file itself. Reason: a PR runs its own CI file, so the base-CLI rule holds only when the CI file is code-owned and the job is required.
- Each file starts with a `#` header in the existing asset style. It covers:
  - what the file does and where it is installed;
  - substitution rules and never clobbering existing files;
  - requiring the job, and that it must be code-owned (GitLab code-owner approval needs Premium);
  - that a PR updating orch is gated by the old version;
  - that in a squash queue `contract-moved-on-main` is vacuous (the `pull_request` run covers it), and that a queued-ahead PR that changes a pinned contract or merges the same story dequeues this one;
  - that detectors (`orch.py deps --probe`) must be installed in CI;
  - that a registry with a repo other than `.` needs the polyrepo follow-up;
  - for CODEOWNERS: it goes to `.github/CODEOWNERS` or `.gitlab/CODEOWNERS`, and orch-setup suggests these lines but does not write them.

**Never:**
- Build `orch-setup` or substitution/merge code; polyrepo auth or coordination clone (deferred); edit this repo's own `.github/` CI or CODEOWNERS; change orch library/CLI behavior.
- Post PR/MR comments or use any secret.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Pass / verdict / error | gate exit 0 / 1 / 2 | job exit 0 / 1 / 2; summary + artifacts | exit 2 fenced |
| PR edits the CLI | PR changes `@ORCH_CLI_DIR@` | base CLI runs | N/A |
| Orch not at base | no CLI, no registry at base | notice, exit 0, gate not run | N/A |
| CLI lost at base | registry present, CLI missing | exit 2 naming the path | exit 2 |
| Merge queue, rebase method | 2-commit PR queued | `--base` = `base_sha`; whole PR gated | N/A |
| Huge output | summary > 900000 bytes | truncated, fence closed, note added | N/A |

</frozen-after-approval>

## Code Map

- `skills/reports/orch-module-plan.md` L74, L86, L229, L233 -- setup contract.
- `skills/orch-gate/scripts/orch.py` L500-510 `gate` flags; L379-383, L405 `fix_prefix` from `__file__` (why the `$WT/` strip); L537-543 early exit 2 writes no `-o`; L74 explicit `--base` SHA accepted; `build_parser()` reused by tests.
- `skills/orch-gate/scripts/orchlib/gate.py` L160-165 story from markers in the diff (works detached); L231-233, L308-325 base-tip checks relevant in a queue.
- `skills/orch-gate/scripts/orchlib/gitio.py:145` `resolve_head` -- merge refs; no `--head`.
- `skills/orch-gate/scripts/orchlib/config.py` L27-32 -- defaults for test substitution.
- `skills/orch-gate/assets/custom/bmad-build.toml` L1-30 -- header style.
- `skills/orch-gate/scripts/tests/test_overrides.py` L22-30, L107-112 -- patterns to mirror; do not modify.

## Tasks & Acceptance

**Execution:**
- [x] `skills/orch-gate/assets/ci/github-actions.yml` -- workflow `orch-gate` (target `.github/workflows/orch-gate.yml`): checkout@v4, astral-sh/setup-uv, one `id: gate` step holding base selection, bootstrap check, extract, gate, prefix strip, summary; `actions/upload-artifact@v4`; concurrency per ref.
- [x] `skills/orch-gate/assets/ci/gitlab-ci.yml` -- job `orch-gate` (target `.gitlab/orch-gate.gitlab-ci.yml`, added via `include: local`): same script shape, target fetch, artifacts.
- [x] `skills/orch-gate/assets/ci/CODEOWNERS` -- the lines above, owned by `@ORCH_OWNERS@`.
- [x] `skills/orch-gate/scripts/tests/test_ci_templates.py` -- the test harness:
  - a defaults table for every placeholder; triggers read via `doc.get("on", doc.get(True))`;
  - locate the GitHub `id: gate` step and the GitLab `script` lines, and run them under `bash -eo pipefail` in a tmp git repo with a bare `origin`, where the base commit holds a dummy `@ORCH_CLI_PATH@` and the registry dir;
  - env: `GITHUB_EVENT_NAME`, `BASE_REF`, `MG_BASE`, `RUNNER_TEMP`, `GITHUB_STEP_SUMMARY` (tmp files), `CI_MERGE_REQUEST_TARGET_BRANCH_NAME`;
  - a stub `uv` first on `PATH` asserts `run --script <path under WT>`, echoes its args plus the `WT` path into stdout, and exits with a code from env;
  - the gate command is checked with `shlex.split`: drop the `uv run --script <path>` prefix, expand `$BASE`, parse with `build_parser()`.
- [x] `skills/orch-gate/SKILL.md` -- short "CI templates" note under Running the gate.

**Acceptance Criteria:**
- Given defaults substituted, then both YAML files parse, CODEOWNERS is plain lines, and nothing has `@…@`, an absolute path or `{project-root}`.
- Given either template's gate command, then it parses with `build_parser()` and carries `--ci`, `--format markdown`, `-o orch-gate.json`, `--base`, with the CLI under `$WT`.
- Given the stub exiting 0, 1 or 2, when each platform's script runs, then it exits the same, `orch-gate.md` holds the stub output without the `WT` path, and the GitHub summary holds it (fenced when 2).
- Given base without CLI and registry, then exit 0 with the notice and the stub is not called; given the registry but no CLI, then exit 2.
- Given `GITHUB_EVENT_NAME=merge_group` and `MG_BASE` set to the base SHA of a 2-commit queue branch, then the stub receives `--base` = `MG_BASE`.
- Given stub output over 900000 bytes with exit 2, then the summary is at most about 900 KB, its fence is closed, and the truncation note is present.
- Given the templates, then GitHub has `fetch-depth: 0`, triggers exactly `pull_request` + `merge_group`, `contents: read`, and upload `if: always()`; GitLab has `GIT_DEPTH: 0`, the target refspec, the MR rule, a non-slim image and `when: always`; neither references a secret or `git worktree`.
- Given CODEOWNERS substituted, then it has one line per required path above, owned by the owners value.

## Review Triage Log

Pass 1 (B = blind, E = edge case, V = verification gap):

| # | Finding | Verdict | Evidence / route |
|---|---|---|---|
| B1, E10, E16 | GitLab fork-MR pipeline runs in the fork, so `origin` target branch and CLI come from the author's fork | high | GitLab runs fork MR pipelines in the fork project by default; violates the base-CLI rule. patch: exit 2 when `CI_PROJECT_ID` ≠ `CI_MERGE_REQUEST_PROJECT_ID`, header explains parent-project pipelines |
| B3, E15 | `/CODEOWNERS`, `/docs/CODEOWNERS` not self-owned; GitLab reads root before `.gitlab/` | high | an MR adding `/CODEOWNERS` shadows `.gitlab/CODEOWNERS` without owner review. patch: add both lines, header names lookup order |
| B2, E13, E14 | a new workflow with an `orch-gate` job, or an unowned GitLab include redefining the job, bypasses the owned CI file | medium | required checks match by name; GitLab merges job keys across includes. patch: header warns and recommends owning `/.github/workflows/` and every included CI file |
| B4 | `_bmad/config.toml` and `_bmad/custom/config.toml` move registry/contract paths after merge without owner review | medium | config.py:19 reads these layers from the base. patch: add both lines. Owning story files: false, would gate every story PR |
| E1 | unresolvable `CLI_REF` makes both probes fail and the job passes as "not installed" | medium | silent pass on a missing base. patch: `git rev-parse --verify` first, exit 2 |
| E2, E4, B7 | truncation inside a `<details>` fence (gate.py:525) or ``` in exit-2 output breaks the summary fence | medium | patch: fence with ```` ```` ```` when exit 2 or truncated |
| B8, E11 | changing the PR base does not re-run the check (`edited` not in default types) | medium | patch: `types: [opened, synchronize, reopened, edited]` |
| E12 | GitLab target-branch change keeps the old pipeline | medium | patch: header says to re-run the pipeline |
| V1, E6, E7, E17 | fix-command strip untested against the real CLI; `_portable` resolves symlinks | medium | stub writes the path it expects. patch: `WT=$(cd "$(mktemp -d …)" && pwd -P)` + a real-CLI test with the temp root two levels above the repo. Sibling temp dir (relative path): low, rare, reject |
| B10 | `workflow:rules` without MR pipelines hides the job | low | direct doc line. patch |
| B6 | substituted paths unquoted | low | direct quoting. patch. sed specials in mktemp paths: false |
| B11 | orch-setup missing | false | spec defers it. SKILL.md "It holds" antecedent unclear: low, patch |
| B5 | CLI_PATH outside CLI_DIR | low | fails loudly in `uv run`; orch-setup sets both. reject |
| B9 | actions/images not SHA-pinned | low | tag pins are the norm; reject |
| B12 | GitLab queue paragraph, fragile `secrets` check, no merge_group exit-2 test, concurrency | low / false | harmless text; `github.ref` differs per PR and queue ref (false). reject |
| E3 | UTF-8 cut by `head -c` | low | only >900 KB; reject |
| E5 | crash with empty md looks like a verdict | low | log carries stderr; reject |
| E8, E9 | `export-ignore` drops CLI files from `git archive` | low | fails loudly; reject |

## Verification

**Commands:**
- `uv run --with pytest --with pyyaml pytest skills/orch-gate/scripts/tests -q` -- expected: all pass.
