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

First run `uv --version` and `git --version`. Every step needs both, so if one is missing, stop before asking anything and give its install hint: uv from docs.astral.sh/uv, git 2.25 or newer.

orch is registered only by the BMad installer (`npx bmad-method install --custom-source <orch git URL or local path>`). It writes `[modules.orch]` to `{project-root}/_bmad/config.toml` (`orch_worktrees_dir` to `config.user.toml`) and installs the skills. Run `uv run scripts/write-config.py --project-root <project root> --module-yaml assets/module.yaml --show`. It gives each variable's value in effect as a raw answer, and the layer it comes from.

- `installer_config` true: installed.
- False: orch was copied in by hand and `bmad-help` does not list its skills. Recommend the installer. If the user continues without it, step 3 writes the full config into the custom layers.

Then run `setup.py status`. When it shows registry entries, a `registry_draft` or installed overrides, this is a re-run. Skip the intro and say in two lines what is in place: config values, registry names, and the state of the overrides, merge driver, CI files and hook. Ask which parts to revisit: config (3), subprojects (4), wiring (5), CI (6). Check (7) always runs. "reconfigure orch" goes straight to step 3. On a first setup, run every step.

## 2. Intro

Open with orch in a few lines. It lets several people and agents deliver one epic across many subprojects in parallel. Each story belongs to one subproject, contracts come first, and a deterministic gate checks every merge. The daily flow: plan with stock BMad, `orch-next` claims a story into its own worktree, `bmad-build` builds it, the PR passes `orch-gate`, and `orch-status` tracks progress and closes epics.

## 3. Configure

Present every variable from `--show` at once, with its prompt and value in effect, so the user answers in one reply with only what changes. Inline values or `accept defaults` skip the question. Write a temp JSON file of `{variable: raw answer}` holding only the changed variables (all of them when orch is not installed) and run write-config.py with `--answers <temp file>` in place of `--show`. Variables left out keep their value. On exit 1, ask again only for the answers listed in `errors`. Show what `written` changed, file by file.

## 4. Draft the registry

Run `setup.py scan --draft`. In a polyrepo, first ask for local clones of the code repos and pass each with `--checkout <path>`. The proposals go to a draft file in the git dir (`draft.path`). When the draft already `exists`, offer to resume it or start over (delete it and scan again). Show the draft as a short table: name, repo, path, detected contracts. Then let the user rename, merge, drop or add entries and widen `allowed_read`. The scan never guesses `imports`, so ask which subprojects consume each exported contract. Save each decision to the draft as it is made. Nothing is written to the registry until the user confirms. Then run `setup.py write-registry --plan <draft>`.

Validate with `uv run {orch} registry --working-tree --repo <project root>`. Fix each issue with the user in the draft and write again: a corrected draft replaces the files it wrote, while they are unchanged. `canonical-missing` is expected until a contract story adds the canonical. When nothing else is left, delete the draft. The entry format is in `{skill-root}/../orch-gate/SKILL.md`.

## 5. Wire it in

- **Merge driver:** `uv run {orch} sprint-status install-driver --repo <coordination repo>`. It is per clone.
- **Overrides:** `setup.py overrides` installs or merges `{project-root}/_bmad/custom/bmad-build.toml`, `bmad-create-epics-and-stories.toml` and `bmad-sprint-planning.toml`. Show each file's `changes` and every warning. In `on_complete`, the orch steps sit between `orch:begin` and `orch:end` marker lines, and team steps belong outside them. A team `on_complete` gets the orch block appended, so ask the user to check that their steps do not push before the gate. An orch entry that differs from the template is reported under `drift` with a diff, because it is either a newer orch template or a team edit. Show the diff, and re-run with `--update` only if the user agrees to replace it. On `manual`, show the `error`.
- **Pre-push hook (offer, optional):** `setup.py hook --repo <clone>`, with `--coord <coordination repo>` in a polyrepo code repo. It gates pushed `story/*` branches locally and blocks only on a failing verdict. CI stays the real gate. On `foreign-hook`, show the `snippet` for the user to merge into their hook or hook manager.

## 6. CI gate

Ask for GitHub Actions, GitLab CI or both, and the CODEOWNERS owners (e.g. `@org/orch-owners`), offering `ci_defaults` from `status` so the user only confirms. Run `setup.py ci --platform github --platform gitlab --owners <owners>` with the chosen platforms. On `differs`, show the diff and leave the file. On `manual`, show where the snippet goes. On `registry-invalid`, fix the registry as in step 4 first. On `polyrepo`, say the CI job for a polyrepo is a later orch release and skip this step. Suggest `codeowners_lines` for `codeowners_file` (or the platform's default location), and never write CODEOWNERS yourself. Then give the protection checklist, because the gate cannot see repository settings: require the `orch-gate` check on the main branch (and in the GitHub merge queue), require code-owner review, and make sure CODEOWNERS owns the CI files.

## 7. Check

Run `setup.py check` and `uv run {orch} deps --working-tree --repo <project root>`. Report each problem with its fix. A missing stock anchor means a BMad update changed a stock skill the overrides describe, so the override may no longer fire where it should. For missing detectors, give the install hint `deps` prints, and offer `deps --probe` once they are installed.

## 8. Next steps

List what to commit: config, registry, overrides, CI files and the orch skills themselves. The first setup PR passes the gate with a notice, because orch is not yet on the base. After it merges, every PR fails `setup` until the epics land. The epics PR changes only planning files, so the gate accepts it as a setup repair. Give every teammate the command to run once in their clone for the merge driver: `orch-setup -H`, or `orch-setup -H hook` if the team uses the hook. In a polyrepo code-repo clone, the hook command also takes `coord=<coordination repo checkout>`. Finish with `module_greeting` from `assets/module.yaml`.

## Headless

With `-H`, skip the questions, the intro and the registry scan.

- Step 3: pass only the inline values (all defaults when orch is not installed). Skip write-config when there are none.
- Step 4 only for an inline `registry=<plan file>` of confirmed entries: run write-registry with it, then validate.
- Steps 5 and 7: install the merge driver and the overrides, then run the checks. Run overrides without `--update`, so drift is reported and never replaced.
- Install the hook only for an inline `hook`, with `--coord` for an inline `coord=<path>`.
- Install CI only for an inline `ci=<platforms>`.

A missing uv or git, or any exit 2, stops the run there. Exit 1 is recorded, and the run continues. The only output is one JSON object: `{"ok": <true when every step exited 0>, "steps": {"config": …, "registry": …, "merge_driver": …, "overrides": …, "hook": …, "ci": …, "check": …, "deps": …}}`. Each step holds its script's JSON, or `"skipped"`.
