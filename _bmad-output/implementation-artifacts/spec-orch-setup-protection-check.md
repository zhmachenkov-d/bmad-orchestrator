---
title: 'orch-setup verifies the GitHub branch protection orch-gate relies on'
type: 'feature'
created: '2026-09-30'
status: 'done'
baseline_commit: 'bb8d766052ae10b177217675ab0488ae5492b45e'
route: 'dispatch'
review_loop_iteration: 0
context: []
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** orch-gate's guarantees hold only when platform settings are on: the `orch-gate` check is required on main, code-owner review is required, and every PR merges onto the main the gate saw (the trial merge judges main as of the run). orch-setup only prints this as a checklist (SKILL.md step 6), so a missed setting silently lets a PR run its own CLI or land an unchecked merge.

**Approach:** Add `setup.py protection`, which reads the GitHub settings through `gh api --include` with the user's own login and reports each as `ok`, `missing` or `unknown`. `missing` settings are problems the user must fix; `unknown` ones (no CLI, no login, no permission, network) fall back to that setting's checklist line. orch-setup runs it in step 7 (Check) whenever the GitHub CI file is installed, so the first install and every re-run check it. Decisions by user: GitLab, CODEOWNERS ownership and bypass checks are split out and deferred; until then they stay checklist lines.

## Boundaries & Constraints

**Always:**
- CLI: `setup.py protection --project-root <root> --platform github`, common `--orch-gate`; `--platform` takes only `github` for now. Monorepo only: a registry naming a repo other than `.` returns code `polyrepo` exactly as `ci` does; `registry-invalid` likewise.
- Target: host and `owner/repo` parsed from `git remote get-url origin` (https, `git@host:path`, `ssh://[user@]host[:port]/path`; strip `.git` and any userinfo, so no credential reaches the output). For the ssh forms the host is resolved through `ssh -G <host>` (its `hostname` line), because origins often use an SSH alias; if that fails the host is used as written. Branch = orch config `main_branch`. Every call is `gh api --include --hostname <host> <endpoint>` with a 30 s timeout; `FileNotFoundError` and `TimeoutExpired` are caught. No origin, an unparsable one, or a host `gh` cannot reach → every setting `unknown`, the message naming the host.
- Response: the status is `^HTTP/\S+ (\d{3})` on the first stdout line and the JSON body follows the first blank line; the exit code is not relied on. No status line, or a non-2xx other than the 404 below, makes that source unreadable; its message carries the body's `message`.
- Sources: `GET repos/{o}/{r}/rules/branches/{branch}?per_page=100` (active rulesets, repo and org; exactly 100 rules → unreadable) and `GET repos/{o}/{r}/branches/{branch}/protection` (classic). A classic 404 whose body message is `Branch not protected` is a readable "none"; a plain `Not Found` 404 (what a non-admin gets) is unreadable.
- Settings, in this order, each `{id, status, message, checklist}`; `checklist` is the human line to print when the status is not `ok`:
  - `required-check`: a required status check with context `orch-gate` (ruleset `required_status_checks.parameters.required_status_checks[].context`, classic `required_status_checks.contexts` or `.checks[].context`).
  - `code-owner-review`: ruleset `pull_request.parameters.require_code_owner_review` or classic `required_pull_request_reviews.require_code_owner_reviews`.
  - `merge-onto-gated-main`: `required-check` is `ok` and some source has a `merge_queue` rule or strict checks (`strict_required_status_checks_policy` / classic `strict`); the source need not be the one requiring `orch-gate`, since a queue waits for every required check and strict applies to the whole branch (decision by user). `required-check` not `ok` → this setting takes its status. Classic protection exists but no queue or strict is visible anywhere → `unknown`, because classic "Require merge queue" is not in the REST response.
- Status: `ok` when a readable source satisfies the setting; `missing` when every source was read and none does; `unknown` otherwise.
- Output via `emit`: `{ok, platforms: {github: {host, project, branch, settings: [...]}}, problems, warnings}`, problems and warnings as `{code, message}`. Each `missing` → problem `protection-missing`; each `unknown` → warning `protection-unverified`. Exit 1 when any problem, else 0.

**Never:**
- Change any repository setting, write any file or ref, fetch, or read tokens directly (only `gh` holds credentials).
- Import from orch-gate; reach it only through its CLI, as setup.py does today.
- GitLab, CODEOWNERS contents, bypass actors / admin pushes, polyrepo code repos.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| All on via ruleset | rules: required `orch-gate`, code-owner review, merge queue | every setting `ok`, exit 0 | N/A |
| Classic only | rules empty; classic strict + contexts `orch-gate` + code owners | `ok` from classic | N/A |
| Queue and check apart | ruleset A `merge_queue`, `orch-gate` required by ruleset B | `merge-onto-gated-main` `ok` | N/A |
| Queue without the check | `merge_queue` rule, no required `orch-gate`, classic "none" | `required-check`, `merge-onto-gated-main` `missing` | exit 1 |
| Classic without strict | classic requires `orch-gate`, not strict; no ruleset queue | `merge-onto-gated-main` `unknown` | exit 0 |
| Branch not protected | rules empty, classic `Branch not protected` | all three `missing` | exit 1 |
| Classic not readable | rules lack the check, classic plain 404 or 403 | `required-check` `unknown`, warning + checklist | exit 0 |
| No gh / not logged in | `gh` absent, or no status line | every setting `unknown` | exit 0 |
| No origin | no `origin` remote | every setting `unknown`, no call | exit 0 |
| SSH alias origin | `git@gh-alias:o/r.git`, `ssh -G` → `github.com` | calls use `--hostname github.com` | N/A |
| Token in origin | `https://x:SECRET@github.com/o/r` | `SECRET` nowhere in the output | N/A |
| Polyrepo registry | a repo other than `.` | code `polyrepo`, exit 1, no call | as `ci` |

</frozen-after-approval>

## Code Map

- `skills/orch-setup/scripts/setup.py` -- `emit`/`SetupError` (:77-86), `git()` (:88), `orch_config`, `cmd_ci` (:619-655), `state` output with `ci` per platform (:754), `build_parser`/`COMMANDS` (:830-860). Add `cmd_protection` and its parser beside `ci`; factor only the registry/polyrepo guard (:622-633) out of `cmd_ci`, not `cli_location`/`orch_config`.
- `skills/orch-gate/assets/ci/github-actions.yml` -- job/check name `orch-gate`; header states the same requirements. Do not edit.
- `skills/orch-setup/SKILL.md` -- step 6 (:59) static checklist; step 7 (:63); Headless (:71-73) step list and JSON shape.
- `skills/orch-setup/scripts/tests/test_setup.py` -- in-process `run()` (:47-52), `project` fixture (:31-45, no origin yet). For fake `gh`/`ssh`, copy the PATH-stub idea of `fake_bin` (`skills/orch-gate/scripts/tests/conftest.py:156`) locally; no cross-skill import.
- `skills/orch-gate/scripts/orchlib/status.py:89-113` -- existing `gh` invocation style; reference only.
- `skills/orch-setup/.memlog.md:12` -- records the API check as deferred.

## Tasks & Acceptance

**Execution:**
- [x] `skills/orch-setup/scripts/setup.py` -- origin parser with ssh-alias resolution, `gh api --include` runner, GitHub evaluator, `cmd_protection`, parser entry, shared registry guard -- the check itself.
- [x] `skills/orch-setup/scripts/tests/test_setup.py` -- fake `gh`/`ssh` on PATH answering per endpoint; one test per I/O matrix row, plus the origin URL forms -- proves the rules.
- [x] `skills/orch-setup/SKILL.md` -- step 6: keep the checklist only for GitLab and for CODEOWNERS ownership, and point to step 7 for GitHub. Step 7: when `.github/workflows/orch-gate.yml` is installed (per `state`'s `ci` on a re-run), run `setup.py protection --platform github`; for `missing`, name the setting and how to turn it on; for `unknown`, print its `checklist` line; keep the trial-merge reason. Headless: add `protection` after `check` in the step list and JSON, run only when `ci=` included github -- wires it in.
- [x] `skills/orch-setup/.memlog.md` -- append a decision line: GitHub API check exists, checklist is its fallback, GitLab / CODEOWNERS / bypass deferred -- keeps the skill memory true.
- [x] `_bmad-output/implementation-artifacts/deferred-work.md` -- remove the two protection entries (sources `spec-orch-gate-ci-templates.md` and `spec-orch-gate-plan-trial-merge.md`); keep the three new entries from this spec -- done here.

**Acceptance Criteria:**
- Given the full orch-setup suite, when it runs, then every test passes with no skips and no test reaches the network.
- Given any run of `protection`, when it finishes, then `git status --porcelain` and the project's refs are unchanged.

## Implementation Notes

- `orch_cli` runs orch.py with `PYTHONDONTWRITEBYTECODE=1`: otherwise its `__pycache__` lands in the project and the "no file changes" criterion fails. Affects every `orch_cli` caller, harmlessly.
- The registry guard's error now says "fix them first" instead of "before installing CI", since `protection` shares it; codes unchanged.
- A rules page of 100 or more counts as unreadable; the branch name is URL-encoded; an https port is dropped (GHES on a custom port degrades to `unknown`).

## Spec Change Log

## Review Triage Log

- Spec review (pre-approval, 13 findings): ssh alias host, GitLab wildcard rules, pagination, same-rule queue semantics (user chose "meaning, not place"), platform/host mismatch, re-run platforms from `state.ci`, token in origin, GitLab 404 scope, `--include` parsing and caught exceptions, merge-trains noise, `integration_id` (rejected: a non-Actions app blocks merges, which is safe), Free-plan 403, developer ambiguities — all others patched. GitLab findings moved with the GitLab split into deferred-work.

Code review, pass 1 (blind, edge-case, verification-gap):

- low — `ssh.github.com` (GitHub's SSH-over-443 host) is passed to `gh api --hostname`, so every setting stays `unknown` (blind, edge-case) -> patch: map it to `github.com` after `ssh -G`.
- low — `gh`/`ssh` present but not executable raise `PermissionError`, which `main` turns into exit 2 `internal` instead of `unknown` / alias fallback (edge-case, two findings) -> patch: catch `OSError`.
- low — `ssh_host` docstring says "no connection, reads ssh_config only", but `Match exec` runs user commands under `-G` (blind) -> patch: correct the comment.
- low — headless runs `protection` when `ci=` includes github even if `ci` refused (`polyrepo`/`registry-invalid`), recording the same refusal twice (blind, edge-case) -> patch: run it only when `ci` did not refuse.
- medium — no test reaches `merge-onto-gated-main` `missing` with `required-check` `ok`; turning that branch into `unknown` passes the suite (verification-gap, pre-verified) -> patch: test.
- false — platform/host mismatch unhandled: a non-GitHub host makes `gh` fail, every setting is `unknown` and each message ends "(gh api on <host>)", as the frozen rule requires.
- false — merge queue not checked to run `orch-gate`: a queue waits for every required check, and `merge-onto-gated-main` is `ok` only when `required-check` is.
- false — the no-change test cannot see bytecode: the `project` fixture has no `.gitignore`, so a `__pycache__` written by `orch.py` would show in `git status --porcelain`.
- false — step 7 silent on `protection` refusals: it runs only when the GitHub CI file is installed, which a polyrepo never gets, and SKILL.md's exit-code rule covers exit 1/2.
- low — tests missing for GHES host, branch with `/`, missing `ssh`, 5xx/non-JSON body, non-list rules, null classic checks (blind) -> reject: each path degrades to `unknown`; tests add no protection users would meet.
- low — `parameters` not a dict, `contexts` a string (edge-case) -> reject: the REST schema fixes both types; guards add branches for states never shown.
- low — a `workflows` ruleset rule enforcing `orch-gate.yml` reads as `required-check` `missing` (edge-case) -> reject: rare, and the advice it gives (require the check) is still right; supporting it adds a branch.
- low — unparsable-origin message names no host (edge-case, claim) -> reject: there is no host to name, and echoing the URL could print a token.
- low — `protection` docstring entry terse (blind) -> reject: cosmetic.
- low — spec bookkeeping and memlog line 7 still listing the deferred check (blind) -> reject: the memlog is append-only and its new line supersedes; the `orch_cli` bytecode change is recorded in Implementation Notes.

## Design Notes

A setting is `missing` only when every source was read: a non-admin gets a plain 404 on classic protection (verified on `cli/cli`), and calling that "missing" would push users to turn on something already on.

Interactive runs always end in step 7, so the first install and every re-run check protection. Headless runs it only with `ci=`, which keeps teammates' `orch-setup -H` for the merge driver off the network.

## Verification

**Commands:**
- `uv run --with pytest --with pyyaml --with tomlkit pytest -q skills/orch-setup/scripts/tests` -- expected: all pass, no skips.
- `uv run --with pytest --with pyyaml pytest -q skills/orch-gate/scripts/tests` -- expected: all pass (templates unchanged).
