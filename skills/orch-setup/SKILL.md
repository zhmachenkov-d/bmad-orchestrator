---
name: orch-setup
description: Sets up BMad Orchestrator (orch) in a project. Use when the user says 'install orch module', 'setup orch', 'configure BMad Orchestrator' or 'reconfigure orch'.
---

# orch-setup

Act as the installer's companion for orch. In one sitting the project goes from "orch installed" to "ready to plan and build in parallel": config set the way the team works, a subproject registry the user confirmed, and the merge driver, stock-skill overrides, CI gate and checks in place. The consumer is the whole team, so everything shared must work in every clone, not only this one. Speak to the user in `{communication_language}`.

Never clobber a user file, and keep every step safe to re-run: the scripts merge into existing files or leave them alone and report the difference. Never edit the files the BMad installer owns: `{project-root}/_bmad/config.toml`, `{project-root}/_bmad/config.user.toml`, `{project-root}/_bmad/_config/` and the installed skill folders.

## Resolution rules

- Bare paths (e.g. `assets/module.yaml`) and `{skill-root}` resolve from this skill's installed directory. `{orch}` is `{skill-root}/../orch-gate/scripts/orch.py`: orch skills install side by side, and orch-gate owns the library.
- `{project-root}` → the project working directory, which is the coordination repo. In config values it stays a literal token; in script path arguments, pass the real path.
- `{communication_language}` → the `communication_language` that `uv run {orch} config --working-tree --offline` prints; null means the user's language.

Every script prints JSON. Exit 1 means something needs the user, and exit 2 is an environment error: report its `error`. Run `scripts/setup.py` as `uv run scripts/setup.py <command>`, with `--project-root <project root>` on every command except `hook`. It reads orch paths from `uv run {orch} config --working-tree`, so config written in step 3 applies before it is committed.

## 1. Check the install

First run `uv --version` and `git --version`. If either is missing, stop before any question and give its install hint: every step runs `uv run`, and orch needs git 2.25 or later.

orch is registered only by the BMad installer (`npx bmad-method install --custom-source <orch git URL or local path>`), which writes `[modules.orch]` to `{project-root}/_bmad/config.toml` (`orch_worktrees_dir` to `config.user.toml`) and installs the skills.

Run `uv run scripts/write-config.py --project-root <project root> --module-yaml assets/module.yaml --show`. It gives each variable's value in effect as a raw answer, and the layer it comes from.

- `installer_config` true: installed.
- False: orch was copied in by hand and `bmad-help` does not list its skills. Recommend the installer. If the user continues without it, step 3 writes the full config into the custom layers.

Then run `setup.py state`. It reports what is already in place: registry entries, a registry draft, the merge driver, each override (`missing`, `current`, `incomplete`, `drift`), the hook and the CI files.

## 2. Intro or re-run

When `in_place` is false, this is a first install: open with orch in a few lines. It lets several people and agents deliver one epic across many subprojects in parallel. Each story belongs to one subproject, contracts come first, and a deterministic gate checks every merge. The daily flow: plan with stock BMad, `orch-next` claims a story into its own worktree, `bmad-build` builds it, the PR passes `orch-gate`, and `orch-status` tracks progress and closes epics. Then walk steps 3 to 8.

When `in_place` is true, skip the intro. Summarize `state` in a few lines and ask which steps to revisit, offering by default the ones with something missing, a draft or drift. Do only those, then step 7. "Reconfigure orch" goes straight to step 3. A teammate's fresh clone usually needs only the merge driver (and the hook, if the team uses it), so point to the headless command in step 8.

## 3. Configure

Present every variable from `--show` at once, with its prompt and value in effect, so the user answers in one reply with only what changes. Inline values or `accept defaults` skip the question. Write a temp JSON file of `{variable: raw answer}` holding only the changed variables (all of them when orch is not installed) and run write-config.py with `--answers <temp file>` in place of `--show`. Variables left out keep their value. On exit 1, ask again only for the answers listed in `errors`. Show what `written` changed, file by file.

## 4. Draft the registry

Run `setup.py scan`. In a polyrepo, first ask for local clones of the code repos and pass each with `--checkout <path>`. The scan keeps its proposals in a registry draft (`draft.path`, in this clone's git dir, never committed). When `draft.status` is `kept`, an earlier negotiation is waiting: offer to resume it, or rescan with `--fresh` to start over.

Show the draft's entries as a short table: name, repo, path, detected contracts. Then let the user rename, merge, drop or add entries and widen `allowed_read`. The scan never guesses `imports`, so ask which subprojects consume each exported contract. Write each decision into the draft file as it is made, so an interruption loses nothing. The entry format is in `{skill-root}/../orch-gate/SKILL.md`.

Before asking for confirmation, run `setup.py write-registry --dry-run`. It validates the draft with orch-gate as if written and leaves no file. Fix each of its `issues` in the draft with the user. `canonical-missing` is expected until a contract story adds the canonical. Nothing reaches the registry until the user confirms. Then run `setup.py write-registry`: it writes the draft's entries, never overwrites an existing one, and removes the draft once every entry is written.

## 5. Wire it in

- **Merge driver:** `uv run {orch} sprint-status install-driver --repo <coordination repo>`. It is per clone.
- **Overrides:** `setup.py overrides` installs or merges `{project-root}/_bmad/custom/bmad-build.toml`, `bmad-create-epics-and-stories.toml` and `bmad-sprint-planning.toml`. Show each file's `changes` and every warning. In `on_complete`, the orch steps sit between `orch:begin` and `orch:end` marker lines, and team steps belong outside them. A team `on_complete` gets the orch block appended, so ask the user to check that their steps do not push before the gate. An orch entry that differs from the template is reported under `drift` with a diff, because it is either a newer orch template or a team edit. Show the diff, and re-run with `--update` only if the user agrees to replace it. On `manual`, show the `error`.
- **Pre-push hook (offer, optional):** `setup.py hook --repo <clone>`, with `--coord <coordination repo>` in a polyrepo code repo. It gates pushed `story/*` branches locally and blocks only on a failing verdict. CI stays the real gate. On `foreign-hook`, show the `snippet` for the user to merge into their hook or hook manager.

## 6. CI gate

Ask for GitHub Actions, GitLab CI or both, and the CODEOWNERS owners (e.g. `@org/orch-owners`), preselecting what `state` found in `ci_defaults` so the user only confirms. Run `setup.py ci --platform github --platform gitlab --owners <owners>` with the chosen platforms. On `differs`, show the diff and leave the file. On `manual`, show where the snippet goes. On `polyrepo`, say the CI job for a polyrepo is a later orch release and skip this step. On `registry-invalid`, fix the listed entries first. Suggest `codeowners_lines` for `codeowners_file` (or the platform's default location), and never write CODEOWNERS yourself. The gate cannot see repository settings, so its guarantees rest on protection the team turns on. Step 7 checks it on GitHub and GitLab. The API check does not cover CODEOWNERS ownership, so give it as a checklist line: make sure CODEOWNERS owns the CI files (on GitLab, every CI file `.gitlab-ci.yml` includes).

## 7. Check

Run `setup.py check` and `uv run {orch} deps --working-tree --repo <project root>`. Report each problem with its fix. A missing anchor means a BMad update changed a stock skill the overrides describe, so the override may no longer fire where it should. For missing detectors, give the install hint `deps` prints, and offer `deps --probe` once they are installed.

When `.github/workflows/orch-gate.yml` is installed (step 6 created it, or `state`'s `ci.github` on a re-run), run `setup.py protection --platform github`. It reads the GitHub settings with the user's `gh` login and changes nothing. For each `missing` setting, name it and say how to turn it on in a ruleset or branch protection rule of the main branch: `required-check` (require the `orch-gate` status check), `code-owner-review` (require review from Code Owners), `merge-onto-gated-main` (a merge queue, or "Require branches to be up to date before merging", because the gate judges a coordination PR on a trial merge with main as of the run). For each `unknown` setting, print its `checklist` line: the API could not tell (no `gh`, no login, no admin access), so the user checks it by hand.

When `.gitlab/orch-gate.gitlab-ci.yml` is installed (step 6 created it, or `state`'s `ci.gitlab` on a re-run), run `setup.py protection --platform gitlab`. It reads the project and its protected branches with the user's `glab` login and changes nothing. For each `missing` setting, say how to turn it on: `required-check` (Settings → Merge requests: turn on "Pipelines must succeed" and untick "Skipped pipelines are considered successful"), `code-owner-review` (protect the main branch with "Code owner approval", which needs GitLab Premium or higher), `merge-onto-gated-main` (merge method "Fast-forward merge" or "Merge commit with semi-linear history", for the same trial-merge reason). For each `unknown` setting, print its `checklist` line; when its message says fields were not returned, the read was anonymous, so suggest `glab auth login --hostname <host>` and a re-run. Add that "Pipelines must succeed" proves the MR's pipeline passed, not that `orch-gate` ran in it: that rests on code-owned CI files and on merge request pipelines being enabled (see the header of the installed GitLab CI file).

## 8. Next steps

List what to commit: config, registry, overrides, CI files and the orch skills themselves. The first setup PR passes the gate with a notice, because orch is not yet on the base. After it merges, every PR fails `setup` until the epics land. The epics PR changes only planning files, so the gate accepts it as a setup repair. Every teammate runs `orch-setup -H` once in their clone for the merge driver, or `orch-setup -H hook` if the team uses the pre-push hook. Finish with `module_greeting` from `assets/module.yaml`, adding `bmad-sprint-planning` after the epics.

## Headless

With `-H`, skip the questions, the intro and the `state` summary; the tool check in step 1 still runs. In step 3, pass only the inline values (all defaults when orch is not installed), and skip write-config when there are none. Run overrides without `--update`, so drift is reported and never replaced. Then install the merge driver, overrides and checks (steps 5 and 7); run `protection --platform <p>` once for each platform in an inline `ci=`, only when `ci` did not refuse (its result has no `code`). With one platform, `steps.protection` is that run's result; with both, it is one object: `ok` true only when both are, `platforms` the union, `problems` and `warnings` concatenated. Each `protection` run's exit code counts as that step's for the exit 1 / exit 2 rules below (an exit 2 stops at `protection`). Install the hook only for an inline `hook`. Install CI only for an inline `ci=<platforms>`. Write the registry only for an inline `registry=<plan.json>`, an already confirmed plan: run `write-registry --plan <file> --dry-run`, and write only when it exits 0.

Print one JSON object as the only output: `{"ok": ..., "steps": {"tools", "config", "registry", "merge_driver", "overrides", "hook", "ci", "check", "protection", "deps"}}`, where each step holds its script's JSON, or null when skipped. After an exit 1, record the result and go on. After an exit 2 or a missing tool, record it, stop and add `"stopped_at": "<step>"`. `ok` is true only when every step that ran exited 0.
