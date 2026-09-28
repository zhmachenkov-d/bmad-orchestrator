---
name: orch-setup
description: Sets up BMad Orchestrator (orch) in a project. Use when the user says 'install orch module', 'setup orch', 'configure BMad Orchestrator' or 'reconfigure orch'.
---

# orch-setup

Act as the installer's companion for orch. The user should leave with orch registered in their BMad project and its config set the way their team works, knowing what to do next. Module identity and every config variable come from `./assets/module.yaml`; config writes go through `./scripts/write-config.py`. Speak to the user in `{communication_language}`.

Never edit the files the BMad installer owns: `_bmad/config.toml`, `_bmad/config.user.toml`, `_bmad/_config/` and the installed skill folders. The installer regenerates them, and orch reads its team config from `_bmad/config.toml` and `_bmad/custom/config.toml` only.

## Resolution rules

- Bare paths (e.g. `./assets/module.yaml`) resolve from this skill's installed directory.
- `{project-root}` → the project working directory. In config values it stays a literal token; in script path arguments, pass the real path. The script refuses the token.
- `{communication_language}` → `communication_language` from `_bmad/config.user.toml`, then `_bmad/custom/config.user.toml`; when unset, the user's language.

## 1. Check the install

orch is registered by the BMad installer, never by this skill. `npx bmad-method install --custom-source <orch git URL or local path>` reads `.claude-plugin/marketplace.json`, asks the questions in `./assets/module.yaml`, writes the answers to `[modules.orch]` in `_bmad/config.toml` (and `orch_worktrees_dir` to `_bmad/config.user.toml`), installs the orch skills side by side and adds `./assets/module-help.csv` to the help catalog.

- `[modules.orch]` present in `_bmad/config.toml`: installed. Say which values are in effect: `_bmad/custom/config.toml` over `_bmad/config.toml`, and for `orch_worktrees_dir` the personal layers over both.
- Absent: orch was copied in by hand, so `bmad-help` does not list its skills. Recommend the installer command above. If the user continues without it, step 2 writes the full config into the custom layers, and the orch skills fall back to the `module.yaml` defaults for anything left unset.

## 2. Configure

Present every `module.yaml` variable with a `prompt` at once, each with the value in effect (else its default), so the user answers in one reply with only what changes. Inline values or `accept defaults` in the invocation skip the question. Then write a temp JSON file of `{variable: raw answer}` for every variable and run:

```bash
uv run ./scripts/write-config.py --project-root <project root> --module-yaml ./assets/module.yaml --answers <temp file>
```

The script applies each variable's `result` template and validates `regex` and `required`. A team answer that differs from the installer's value lands in `_bmad/custom/config.toml`, a personal one (`user_setting: true`) in `_bmad/custom/config.user.toml`, and an answer equal to the installer's value removes the override. Comments and other tables in those files are kept. Exit 1 lists invalid answers in `errors`: ask again for those only. Exit 2 is an environment error: report its `error`.

Show what `written` changed, file by file. `_bmad/custom/config.toml` is team config: tell the user to commit it through a PR. orch reads team config at the coordination repo's main, so the gate and every clone see a change only once it is merged.

## 3. Next steps

The remaining setup is done by hand for now, from orch-gate's assets. Each file's header says where it goes and what to substitute:

- Subproject registry: one YAML per subproject in `orch_registry_dir` (format in orch-gate's SKILL.md).
- Stock-skill overrides: `../orch-gate/assets/custom/*.toml` → `_bmad/custom/`, merged with any existing file.
- CI gate: `../orch-gate/assets/ci/` for GitHub Actions or GitLab CI, plus the suggested `CODEOWNERS` lines.

Land the registry and the epics in one coordination PR that changes only registry and planning files; the gate accepts it as a setup repair. Then plan with `bmad-prd` → `bmad-architecture` → `bmad-create-epics-and-stories` → `bmad-sprint-planning`, and pick work with `orch-next`. Finish by showing `module_greeting` from `./assets/module.yaml`.

## Headless

With `-H`, skip the questions: use inline values, else the values in effect, else the defaults, run step 2, and print the script's JSON as the only output.
