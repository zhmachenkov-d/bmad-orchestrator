# Do Not Edit Vendor BMAD Files

Never modify files that the BMAD installer owns or regenerates on update. Edits there are lost on the next install/update and diverge from upstream.

## Do not edit

- `_bmad/config.toml`, `_bmad/config.user.toml`
- `_bmad/<module>/` (e.g. `bmm/`, `core/`, `tea/`, `cis/`, `bmb/`, `bmad-loop/`) — including `config.yaml`, help CSVs, shims
- `_bmad/scripts/`, `_bmad/_config/`
- Installed skill packages: `.agents/skills/bmad-*/**` (especially `SKILL.md`, `customize.toml`, `assets/`)
- Any file whose header says `DO NOT EDIT` / `overwritten on every update` / `Installer-managed`

## Customize instead

| Need                                              | Put it here                                                                    |
| ------------------------------------------------- | ------------------------------------------------------------------------------ |
| Personal name/language and user settings          | `_bmad/custom/config.user.toml`                                                |
| Team/project config overrides                     | `_bmad/custom/config.toml`                                                     |
| Skill/workflow behavior (hooks, facts, templates) | `_bmad/custom/<skill-name>.toml` (team) or `<skill-name>.user.toml` (personal) |
| Planning/implementation outputs                   | `_bmad-output/` (and other project-owned paths)                                |

Use `bmad-customize` when unsure which override surface to use. Prefer upstream fixes or custom overrides over patching vendor copies.

## Examples

```text
# ❌ BAD — edited stock skill; wiped on BMAD update
.agents/skills/bmad-product-brief/SKILL.md

# ✅ GOOD — durable project override
_bmad/custom/bmad-product-brief.toml
```
