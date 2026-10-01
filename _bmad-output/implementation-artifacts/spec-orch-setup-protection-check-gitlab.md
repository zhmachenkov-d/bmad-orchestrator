---
title: 'orch-setup verifies the GitLab settings orch-gate relies on'
type: 'feature'
created: '2026-10-01'
status: 'done'
baseline_commit: '7831af2b97a9b252e6b1dfaf8659f15cd9301a82'
route: 'dispatch'
review_loop_iteration: 0
context: []
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** `setup.py protection` checks only GitHub; on GitLab, "pipelines must succeed", code-owner approval on the protected main branch and a fast-forward/semi-linear merge method are still a checklist in SKILL.md step 6, so a missed setting silently lets an MR merge past the gate.

**Approach:** Add `--platform gitlab` to `setup.py protection`: read the project and its protected branches through `glab api --include --hostname` with the user's own login, and report the same three settings with the same `ok`/`missing`/`unknown` rules, response parsing and output shape as GitHub. Step 7 runs it whenever the GitLab CI file is installed. Decisions by user: the merge-trains warning and a repeatable `--platform` are split out and deferred.

## Boundaries & Constraints

**Always:**
- CLI: `--platform` takes `github` or `gitlab` (one per run); output `platforms: {gitlab: {host, project, branch, settings}}`. Polyrepo / `registry-invalid` refusals as today.
- Origin: GitLab paths may have nested groups (`group/sub/project`, 2+ segments); GitHub stays exactly `owner/repo`. SSH alias resolution as for GitHub; `altssh.gitlab.com` (SSH over 443) maps to `gitlab.com`. Project id in endpoints = the URL-encoded full path.
- Calls: `glab api --include --hostname <host> <endpoint>`, 30 s timeout, `OSError`/`TimeoutExpired` caught; same status-line/body parsing as `gh_api`.
- Sources: `GET projects/{id}` and `GET projects/{id}/protected_branches?per_page=100`; any non-2xx (404, 403 included) makes that source unreadable; 100 or more entries → unreadable (as GitHub). Matching rules = entries, `inherited` ones included, whose `name` matches `main_branch` as a whole, case-sensitively, where only `*` is a wildcard and matches any characters including `/`; no match = branch not protected.
- Per setting: a known field value that breaks the rule → `missing`; else a field absent from a readable response, or a source unreadable → `unknown` (anonymous reads omit the project's settings; the message then says which fields were not returned and suggests `glab auth login --hostname <host>`); else `ok`.
- Settings, same ids, order and `{id, status, message, checklist}` shape as GitHub:
  - `required-check`: `only_allow_merge_if_pipeline_succeeds` true and `allow_merge_on_skipped_pipeline` not true; `null` counts as false, GitLab's default (a `[skip ci]` pipeline would merge past the gate — decision by user).
  - `code-owner-review`: some matching rule has `code_owner_approval_required` true → `ok`; no matching rule, or false on every matching rule → `missing`; absent on every matching rule (Community Edition) → `unknown`. Its checklist says code-owner approval needs GitLab Premium or higher.
  - `merge-onto-gated-main`: `required-check` not `ok` → takes its status (as GitHub); else `merge_method` `ff` or `rebase_merge` → `ok`, `merge` → `missing`, anything else → `unknown`.
- Problems, warnings and exit code exactly as GitHub (`protection-missing`, `protection-unverified`).
- Headless keeps one `steps.protection` object: with one platform it is that run's result as today; with both, `ok` is both `ok`, `platforms` the union, `problems` and `warnings` concatenated.

**Never:**
- Change any setting, file or ref; fetch; read tokens (only `glab` holds credentials). Import from orch-gate.
- CODEOWNERS contents, bypass / push-access checks, merge trains, polyrepo, several platforms per run.
- Change GitHub's results or output.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| All on | pipeline succeeds, no skipped, rule `main` with code owners, `merge_method: ff` | three `ok`, exit 0 | N/A |
| Wildcard rule | only rule `ma*` (code owners true); also `ma*` vs `xmain`, `Main` vs `main` | `ok`; the latter two do not match | N/A |
| Skipped never set | `allow_merge_on_skipped_pipeline: null`, rest on | `required-check` `ok` | N/A |
| Skipped allowed | `allow_merge_on_skipped_pipeline: true` | `required-check`, `merge-onto-gated-main` `missing` | exit 1 |
| Partly known | pipeline-succeeds `false`, skipped field absent | `required-check` `missing` | exit 1 |
| Merge commits | `merge_method: merge`, gate ok | `merge-onto-gated-main` `missing` | exit 1 |
| Unprotected main | rules for other branches only | `code-owner-review` `missing` | exit 1 |
| Inherited rule | only a group rule `main`, `inherited: true`, code owners true | `code-owner-review` `ok` | N/A |
| Community Edition | matching rule lacks `code_owner_approval_required` | `code-owner-review` `unknown` | exit 0 |
| Anonymous read | project 200 without setting fields | `required-check`, `merge-onto-gated-main` `unknown`, message suggests `glab auth login` | exit 0 |
| Rules unreadable | protected_branches 403 | `code-owner-review` `unknown` | exit 0 |
| Project 404 | project not found | every setting `unknown`, message names host | exit 0 |
| Nested groups | `git@gitlab.com:g/sub/shop.git` | endpoint `projects/g%2Fsub%2Fshop` | N/A |
| SSH over 443 | alias resolving to `altssh.gitlab.com` | calls use `--hostname gitlab.com` | N/A |
| No glab | binary absent | every setting `unknown` | exit 0 |

</frozen-after-approval>

## Code Map

- `skills/orch-setup/scripts/setup.py` -- protection section (:671-901). Generalize `gh_api` into a runner taking the CLI (`GH`, new `GLAB = "glab"`; tests monkeypatch `setup.GH`, keep that working); `parse_origin` gains a nested-groups mode for GitLab, GitHub results unchanged (`https://gitlab.com/group/sub/shop.git` stays None there); reuse `_source`, `_status`, `_unread`; add `read_gitlab_sources`, `gitlab_settings`; `cmd_protection` picks sources/evaluator/host fix-up (`ssh.github.com`, `altssh.gitlab.com`) per platform, unread suffix `(<cli> api on <host>)`, no-origin message platform-neutral. Parser :1099: choices `github`, `gitlab`.
- `skills/orch-setup/scripts/tests/test_setup.py` -- protection tests (:542-802), `FAKE_GH` + `gh` fixture; add a `glab` stub on the same PATH (own responses/log env vars) without changing the GitHub tests.
- `skills/orch-setup/SKILL.md` -- step 6 (:59): drop the GitLab checklist sentence, point to step 7, keep CODEOWNERS ownership. Step 7 (:65): run `protection --platform gitlab` when `.gitlab/orch-gate.gitlab-ci.yml` is installed (or `state.ci.gitlab`), with fix hints (Settings → Merge requests: "Pipelines must succeed", untick "Skipped pipelines are considered successful", merge method fast-forward or semi-linear; protected branch "Code owner approval", Premium+). Add that "pipelines must succeed" proves the MR's pipeline passed, not that `orch-gate` ran in it: that rests on code-owned CI files and merge request pipelines (`gitlab-ci.yml` header). Headless (:71-73): run it for each platform in `ci=` (still only when `ci` did not refuse), combined as in Boundaries.
- `skills/orch-gate/assets/ci/gitlab-ci.yml` -- job `orch-gate`; reference only, do not edit.
- `skills/orch-setup/.memlog.md`, `_bmad-output/implementation-artifacts/deferred-work.md` (GitLab entry :17-19).

## Tasks & Acceptance

**Execution:**
- [x] `skills/orch-setup/scripts/setup.py` -- shared CLI runner, nested-group origins, GitLab sources and evaluator, per-platform `cmd_protection`, parser -- the check.
- [x] `skills/orch-setup/scripts/tests/test_setup.py` -- fake `glab`; one test per I/O row, GitLab origin forms, wildcard (`*` across `/`, `[` literal) -- proves the rules.
- [x] `skills/orch-setup/SKILL.md` -- steps 6, 7, Headless as in the Code Map -- wires it in.
- [x] `skills/orch-setup/.memlog.md` -- append: GitLab API check exists, checklist is its fallback; CODEOWNERS, bypass, merge trains, multi-platform runs deferred.
- [x] `_bmad-output/implementation-artifacts/deferred-work.md` -- remove the GitLab entry; keep the rest.

**Acceptance Criteria:**
- Given the full orch-setup suite, when it runs, then every test passes with no skips, no test reaches the network, and the existing GitHub tests pass unchanged.
- Given any run of `protection`, when it finishes, then `git status --porcelain` and the project's refs are unchanged.

## Implementation Notes

- `gh_api` kept as a thin wrapper over the new `cli_api(platform, ...)`, so the GitHub reader and the `setup.GH` monkeypatch work unchanged; the GitHub tests are untouched.
- The no-origin message names the platform ("GitHub owner/repo" / "GitLab project") instead of one neutral text, so GitHub's output stays byte for byte as before.
- `_source` drops a leading repeated status from the error ("HTTP 404 404 Project Not Found" -> "HTTP 404 Project Not Found"); GitHub messages never start with it.
- Smoke-checked live with glab 1.120 against gitlab.com, anonymously: `gitlab-org/cli` reads `required-check` and `merge-onto-gated-main` `unknown` with the login hint and `code-owner-review` `ok`; a missing project reads all `unknown` with HTTP 404.

## Spec Change Log

## Review Triage Log

- Spec review (pre-approval, 12 findings): patched — nullable `allow_merge_on_skipped_pipeline`, code-owner field present on every gitlab.com tier (absent only on CE; Premium noted in checklist), partly known fields, other `merge_method` values, "pipelines must succeed" ≠ `orch-gate` ran (SKILL note), headless shape kept compatible, 403 row not tied to a role, full-string case-sensitive matching, inherited rules, `altssh` row, unknown message for absent fields. Rejected — `x-next-page` instead of the 100 cap: same rule as GitHub, exactly 100 protected branches is rare and only degrades to `unknown`. Known limit: a downgraded project keeping a stale `true` reads `ok`; not visible through the API.

Code review, pass 1 (blind, edge-case, verification-gap):

- medium — the `read_gitlab_sources` type guards (project not a dict, branches not a list) are untested; a mutation removing them passes, and without them a list crashes `gitlab_settings` and a dict yields a false `missing` (verification-gap, pre-verified) -> patch: parametrized test.
- medium — a private project read without a glab login answers 404 "Project Not Found" with no hint that signing in would help (edge-case) -> patch: login hint on project 401/404.
- low — `_source`'s removal of the repeated status is untested; the 403 test passes with "403 403" (verification-gap, blind) -> patch: assert the exact text.
- low — headless with both platforms leaves the combined `protection` step's exit code unstated (blind, edge-case) -> patch: one SKILL.md clause.
- false — glab's `--include` format unverified (blind): verified live with glab 1.120 on gitlab.com (Design Notes, Implementation Notes); the fake follows that format.
- false — spec missing from the diff (blind): left out on purpose, it is the review's claims file.
- false — a 401 body with `error`/`error_description` loses its reason (edge-case): GitLab API 401 bodies are `{"message": "401 Unauthorized"}`.
- false — `--platform gitlab` on a GitHub origin (edge-case): every setting degrades to `unknown` naming the host, as decided for the GitHub half.
- low — `merge-onto-gated-main` repeats a `missing` `required-check`, so step 7 may advise a merge method already set (blind) -> reject: the frozen spec mandates "takes its status", as on GitHub, and the message says it depends on the pipeline setting.
- low — combined headless problems lose which platform they came from (blind) -> reject: `platforms.<p>.settings` carries it; the prefix is the deferred repeatable `--platform` item.
- low — mixed matching rules (one false, one without the field) read `missing` (blind, edge-case) -> reject: an instance returns the field on every entry or on none.
- low — the 100-rule cap ignores `X-Next-Page` (blind) -> reject: carried from the spec review, same rule as GitHub.
- low — no glab tests for no-login / timeout / stderr-only (blind) -> reject: glab without login reads anonymously (covered by the anonymous-read row); timeout and stderr paths are the shared `cli_api` code tested through gh.
- low — self-managed GitLab with a separate SSH host or a relative URL root degrades to `unknown` (blind, edge-case) -> reject: the spec accepts the degrade and the message names the host.
- low — the MR-pipeline caveat lives only in SKILL.md, not the JSON (blind) -> reject: out of scope by the spec; step 7 prints it.
- low — four per-platform tables and `_cli`'s else-branch (blind) -> reject: argparse limits the platform to two; no caller diverges.
- low — the dependent `merge-onto-gated-main` message omits that `merge_method` was not returned either (blind) -> reject: its checklist line is printed for `unknown`, which covers the merge method.

## Design Notes

Verified with glab 1.120 against gitlab.com: 200 and 404 both print `HTTP/2.0 <code> ...`, headers, a blank line and JSON (404 exits 1). With no login, glab reads anonymously: the project comes back without `merge_method` and the other settings, so absent means `unknown`; protected branches of a public project are readable, with `code_owner_approval_required`.

## Verification

**Commands:**
- `uv run --with pytest --with pyyaml --with tomlkit pytest -q skills/orch-setup/scripts/tests` -- expected: all pass, no skips.
- `uv run --with pytest --with pyyaml pytest -q skills/orch-gate/scripts/tests` -- expected: all pass.
