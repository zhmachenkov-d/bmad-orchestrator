#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["pyyaml>=6.0", "tomlkit>=0.12"]
# ///
"""Write orch module config answers into the project's BMad custom TOML layers.

The BMad installer owns `_bmad/config.toml` and `_bmad/config.user.toml` and writes the install answers
there. This script never touches those two files. It writes `[modules.orch]` into `_bmad/custom/config.toml`
(team, committed) and, for variables marked `user_setting: true` in module.yaml, into
`_bmad/custom/config.user.toml` (personal, gitignored). A key whose answer equals the installer's value is
removed from the custom layer instead, so the install answer applies and later installs can change it.
Comments and other tables in the custom files are kept.

Answers are raw values keyed by module.yaml variable names; each variable's `result` template is applied
here, so the literal `{project-root}` token stays in the written values. Numeric defaults stay numbers.

Output is JSON on stdout. Exit codes: 0 = ok, 1 = invalid answers, 2 = usage or environment error.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import tomllib
from pathlib import Path

import tomlkit
import yaml

MODULE = "orch"
TEAM_BASE, USER_BASE = "_bmad/config.toml", "_bmad/config.user.toml"
TEAM_CUSTOM, USER_CUSTOM = "_bmad/custom/config.toml", "_bmad/custom/config.user.toml"
HEADERS = {
    TEAM_CUSTOM: "# Team / enterprise overrides for _bmad/config.toml.\n# Committed to the repo — applies to every developer on the project.\n",
    USER_CUSTOM: "# Personal overrides for _bmad/config.toml and _bmad/config.user.toml.\n# Applies only to you — keep this file out of git.\n",
}


class UsageError(Exception):
    pass


def variables(module_yaml: Path) -> dict[str, dict]:
    """module.yaml variables that carry a prompt, keyed by name."""
    data = yaml.safe_load(module_yaml.read_text(encoding="utf-8")) or {}
    if data.get("code") != MODULE:
        raise UsageError(f"{module_yaml} is not the {MODULE} module definition (code: {data.get('code')!r})")
    return {k: v for k, v in data.items() if isinstance(v, dict) and "prompt" in v}


def render(spec: dict, raw) -> tuple[object, str | None]:
    """The value to store for one answer, or an error message."""
    text = "" if raw is None else str(raw).strip()
    if spec.get("required") and not text:
        return None, "a value is required"
    if spec.get("regex") and not re.fullmatch(spec["regex"], text):
        return None, f"{text!r} does not match {spec['regex']}"
    default = spec.get("default")
    if isinstance(default, (int, float)) and not isinstance(default, bool) and spec.get("result", "{value}") == "{value}":
        try:
            number = float(text)
        except ValueError:
            return None, f"{text!r} is not a number"
        return (int(number) if number.is_integer() else number), None
    return str(spec.get("result", "{value}")).replace("{value}", text), None


def orch_section(path: Path) -> dict:
    """`[modules.orch]` of a TOML file, keys normalised to the `orch_` prefix as orch's config loader does."""
    if not path.is_file():
        return {}
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise UsageError(f"{path} is not valid TOML: {exc}") from exc
    section = data.get("modules", {}).get(MODULE, {})
    return {k if k.startswith("orch_") else f"orch_{k}": v for k, v in section.items()}


def same(a, b) -> bool:
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return float(a) == float(b)
    return a == b


def apply(root: Path, rel: str, set_values: dict, remove: list[str], dry_run: bool) -> dict:
    """Set and remove keys in `[modules.orch]` of one custom file; report what changed.

    A key stored without the `orch_` prefix counts as the same key, and is rewritten with the prefix when set.
    """
    path = root / rel
    doc = tomlkit.parse(path.read_text(encoding="utf-8") if path.is_file() else HEADERS[rel])
    modules = doc.setdefault("modules", tomlkit.table(is_super_table=True))
    section = modules.setdefault(MODULE, tomlkit.table())
    stored = {(k if k.startswith("orch_") else f"orch_{k}"): k for k in section}
    changed = {"set": {}, "removed": []}
    for key, value in set_values.items():
        if key in stored and same(section[stored[key]].unwrap(), value):
            continue
        if key in stored:
            del section[stored[key]]
        section[key] = value
        stored[key] = key
        changed["set"][key] = value
    for key in remove:
        if key in stored:
            del section[stored.pop(key)]
            changed["removed"].append(key)
    if not section:
        del modules[MODULE]
    if not modules:
        del doc["modules"]
    if (changed["set"] or changed["removed"]) and not dry_run:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(tomlkit.dumps(doc), encoding="utf-8")
    return changed


def run(root: Path, module_yaml: Path, answers: dict, dry_run: bool) -> tuple[int, dict]:
    specs = variables(module_yaml)
    errors = {k: "not a variable of the orch module" for k in answers if k not in specs}
    team_base = orch_section(root / TEAM_BASE)
    user_base = {**team_base, **orch_section(root / USER_BASE)}
    plan = {TEAM_CUSTOM: ({}, []), USER_CUSTOM: ({}, [])}
    for key, raw in answers.items():
        if key not in specs:
            continue
        value, error = render(specs[key], raw)
        if error:
            errors[key] = error
            continue
        user = bool(specs[key].get("user_setting"))
        target, base = (USER_CUSTOM, user_base) if user else (TEAM_CUSTOM, team_base)
        if key in base and same(base[key], value):
            plan[target][1].append(key)
        else:
            plan[target][0][key] = value
    if errors:
        return 1, {"ok": False, "errors": errors}
    written = {}
    for rel, (set_values, remove) in plan.items():
        changed = apply(root, rel, set_values, remove, dry_run)
        if changed["set"] or changed["removed"]:
            written[rel] = changed
    installed = bool(team_base) or bool(orch_section(root / USER_BASE))
    return 0, {"ok": True, "dry_run": dry_run, "installer_config": installed, "written": written}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--project-root", required=True, help="project root (a real path, not the {project-root} token)")
    parser.add_argument("--module-yaml", required=True, help="orch-setup's assets/module.yaml")
    parser.add_argument("--answers", required=True, help="JSON file: {variable name: raw answer}")
    parser.add_argument("--dry-run", action="store_true", help="report the changes without writing")
    args = parser.parse_args(argv)
    try:
        for name in ("project_root", "module_yaml", "answers"):
            if "{project-root}" in getattr(args, name):
                raise UsageError(f"--{name.replace('_', '-')} still holds the literal {{project-root}} token; pass the real path")
        root = Path(args.project_root).resolve()
        if not (root / "_bmad").is_dir():
            raise UsageError(f"{root} has no _bmad/ directory; install BMad first")
        try:
            answers = json.loads(Path(args.answers).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise UsageError(f"cannot read answers from {args.answers}: {exc}") from exc
        if not isinstance(answers, dict):
            raise UsageError("answers must be a JSON object")
        code, result = run(root, Path(args.module_yaml), answers, args.dry_run)
    except (UsageError, OSError, yaml.YAMLError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}))
        return 2
    print(json.dumps(result, indent=2))
    return code


if __name__ == "__main__":
    sys.exit(main())
