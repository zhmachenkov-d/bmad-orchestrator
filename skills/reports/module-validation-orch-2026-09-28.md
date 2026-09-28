# Module Validation: orch (BMad Orchestrator)

- **Date:** 2026-09-28
- **Module:** `orch` 0.1.0, setup skill `orch-setup`
- **Skills:** `orch-setup`, `orch-gate`, `orch-next`, `orch-status`
- **Method:** bmad-module-builder Validate Module (VM): `validate-module.py` plus a review of every help entry against its SKILL.md
- **Verdict:** ready for use. Four low-to-medium help-catalog fixes were found and applied in the same change.

## Structural checks (script)

`validate-module.py skills/`: status `pass`, 0 findings, before and after the fixes.

- `orch-setup/assets/module-help.csv` has 8 entries covering all 4 skills: no orphans, no duplicate menu codes, no broken `preceded-by` / `followed-by` references, no missing required fields.
- Phase values (`plan`, `ship`, `anytime`) match the stock BMad help catalog.
- No menu code collides with an installed module's code in `_bmad/_config/bmad-help.csv`.
- `module.yaml` has no `agents:` block, so no agent roster check applies.
- `.claude-plugin/marketplace.json` lists all four skills.

## Quality review

Every capability described in the four SKILL.md files is registered. `orch-status` has one row per mode (status, plan-check, close-epic, report, rebuild). Resume, release and take-over are covered by the `orch-next` and `orch-status` descriptions.

| # | Severity | Entry | Finding | Fix (applied) |
| --- | --- | --- | --- | --- |
| 1 | medium | `OSU` Setup Orch | Description was two sentences listing seven items; help entries should be one concise sentence. | Replaced with "Configure orch, draft and confirm the subproject registry, and install the merge driver, stock-skill overrides, CI gate and checks; re-run with -H in each new clone." |
| 2 | low | `OPV` Orch Plan Check | Menu code did not follow the display name, so it was hard to remember. | Renamed to `OPC`. |
| 3 | low | `ON` Orch Next Story | `action` was empty, and `-H` was missing from `args` although SKILL.md supports a read-only headless mode. | `action` set to `next`; `args` now start with `{-H: headless mode, prints the ranking only}`. |
| 4 | low | `OCE` Orch Close Epic | `preceded-by: orch-next` was inaccurate: an epic closes after its last story passes the gate and merges. | `preceded-by` set to `orch-gate`. |

Entries `OG`, `OS`, `OER` and `ORS` are accurate: their descriptions start with a verb, and their args match their SKILL.md.

## Remaining work outside this validation

- The open medium and low findings of the `orch-setup` skill analysis (`skills/orch-setup/.analysis/2026-09-28-0916/`) are not part of module validation.
- Pilot on one real monorepo and one two-repo polyrepo before calling v1 done (Build Roadmap).
