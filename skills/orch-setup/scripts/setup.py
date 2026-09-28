#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["pyyaml>=6.0", "tomlkit>=0.12"]
# ///
"""orch-setup steps that must be exact: registry draft, stock-skill overrides, CI files, pre-push hook, checks.

Every command is idempotent and never clobbers a user file: an existing file is merged into (overrides, the
`.gitlab-ci.yml` include) or left alone with the difference reported (CI files, a foreign pre-push hook).
Orch paths come from `orch.py config --working-tree` of orch-gate, which installs beside this skill, so an
uncommitted config change is already in effect here.

Commands:
  scan            propose registry entries from the repo layout (and from --checkout code repos in a polyrepo)
  write-registry  write confirmed entries (--plan JSON) as <registry_dir>/<name>.yaml; never overwrites
  overrides       install or merge the orch overrides into _bmad/custom/{bmad-build,bmad-create-epics-and-stories,
                  bmad-sprint-planning}.toml; missing orch entries are added, everything else is kept; an orch
                  entry that differs from the template is reported as drift and replaced only with --update
  ci              install the orch-gate CI job (--platform github|gitlab) for a monorepo registry; CODEOWNERS
                  lines are only suggested
  hook            install a pre-push hook that runs the gate on pushed story/* branches
  check           git/uv/host CLIs, stock anchors the overrides rely on, ignored orch dirs
  status          what is already in place: registry entries, overrides, merge driver, CI files, pre-push hook

Output is JSON on stdout. Exit codes: 0 = ok, 1 = something needs the user (invalid plan, a check failed,
a file left alone), 2 = usage or environment error.
"""

from __future__ import annotations

import argparse
import difflib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tomllib
from pathlib import Path, PurePosixPath

import tomlkit
import yaml

ORCH_GATE = Path(__file__).resolve().parents[2] / "orch-gate"
OVERRIDES = ("bmad-build", "bmad-create-epics-and-stories", "bmad-sprint-planning")
CI_TARGETS = {"github": ".github/workflows/orch-gate.yml", "gitlab": ".gitlab/orch-gate.gitlab-ci.yml"}
CI_TEMPLATES = {"github": "github-actions.yml", "gitlab": "gitlab-ci.yml"}
GITLAB_ROOT = ".gitlab-ci.yml"
CODEOWNERS_FILES = {"github": (".github/CODEOWNERS", "CODEOWNERS", "docs/CODEOWNERS"),
                    "gitlab": ("CODEOWNERS", "docs/CODEOWNERS", ".gitlab/CODEOWNERS")}
HOOK_TAG = "# orch-gate pre-push hook (installed by orch-setup)"
MIN_GIT = (2, 25)
MANIFESTS = ("package.json", "go.mod", "pyproject.toml", "Cargo.toml", "pom.xml", "build.gradle", "build.gradle.kts")
CONVENTIONAL = ("services", "packages", "apps")
SKIP_DIRS = {"node_modules", "vendor", "target", "dist", "build", "__pycache__", "venv"}
NAME = re.compile(r"^[a-z0-9][a-z0-9._-]*$")
# What the planning overrides say about the stock skills. BMad updates overwrite stock skills but never
# _bmad/custom/, so a renamed anchor silently weakens an override; `check` re-reads them on every run.
STOCK_ANCHORS = {
    "bmad-create-epics-and-stories": [
        ("steps/step-04-final-validation.md", "[C] Complete"),
        ("steps/step-03-create-stories.md", "STORY FORMAT"),
        ("steps/step-03-create-stories.md", "So that"),
    ],
    "bmad-sprint-planning": [("SKILL.md", f"**{i}**") for i in ("readiness", "sprint-planning", "status", "validate", "fix")]
                            + [("SKILL.md", "confirm every entry was executed")],
}


class SetupError(Exception):
    def __init__(self, message: str, code: str = "error", **fields):
        super().__init__(message)
        self.code, self.fields = code, fields


def emit(payload: dict, code: int = 0) -> int:
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return code


def git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    res = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)
    if check and res.returncode != 0:
        raise SetupError(f"git {' '.join(args)}: {res.stderr.strip()}", "git")
    return res


def orch_config(root: Path, orch_gate: Path) -> dict:
    """Resolved orch config of the coordination repo's working tree, from orch-gate's CLI."""
    cli = orch_gate / "scripts" / "orch.py"
    if not cli.is_file():
        raise SetupError(f"orch-gate not found at {orch_gate}; orch skills install side by side", "no-orch-gate")
    res = subprocess.run(["uv", "run", str(cli), "config", "--working-tree", "--offline", "--repo", str(root)],
                         capture_output=True, text=True)
    try:
        out = json.loads(res.stdout)
    except json.JSONDecodeError:
        raise SetupError(f"orch.py config failed: {(res.stderr or res.stdout).strip()[-500:]}", "orch-config")
    if res.returncode != 0:
        raise SetupError(f"orch.py config: {out.get('error')}", out.get("code") or "orch-config")
    return out["config"]


def cli_location(root: Path, orch_gate: Path) -> dict:
    """Where the committed files point: orch-gate relative to the project root, with its caveats."""
    try:
        rel = orch_gate.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        raise SetupError(f"orch-gate ({orch_gate}) is outside the project; committed team files must name a path "
                         "every clone has — install orch into the project", "orch-gate-outside")
    warnings = []
    if any((root / p).is_symlink() for p in [rel, *[str(q) for q in PurePosixPath(rel).parents if str(q) != "."]]):
        warnings.append(f"{rel} is (under) a symlink: CI extracts it with `git archive`, which keeps only the link; "
                        "commit a real directory")
    if git(root, "ls-files", "--error-unmatch", f"{rel}/scripts/orch.py", check=False).returncode != 0:
        warnings.append(f"{rel} is not committed yet: overrides, hook commands and CI all run it from the repo, "
                        "so commit it with the setup PR")
    return {"dir": rel, "path": f"{rel}/scripts/orch.py", "warnings": warnings}


# ---- scan / write-registry ----

def _read(path: Path, limit: int = 4096) -> str:
    try:
        with path.open("r", encoding="utf-8", errors="replace") as f:
            return f.read(limit)
    except OSError:
        return ""


def _globs(root: Path, patterns: list) -> list[Path]:
    found = []
    for pat in patterns:
        if isinstance(pat, str) and not pat.startswith("!"):
            found += [p for p in sorted(root.glob(pat.strip("/"))) if p.is_dir()]
    return found


def _workspace_members(root: Path) -> list[Path]:
    members = []
    pkg = root / "package.json"
    if pkg.is_file():
        try:
            ws = json.loads(pkg.read_text(encoding="utf-8")).get("workspaces")
        except (json.JSONDecodeError, AttributeError, OSError):
            ws = None
        members += _globs(root, ws.get("packages", []) if isinstance(ws, dict) else ws or [])
    pnpm = root / "pnpm-workspace.yaml"
    if pnpm.is_file():
        try:
            members += _globs(root, (yaml.safe_load(pnpm.read_text(encoding="utf-8")) or {}).get("packages", []))
        except (yaml.YAMLError, AttributeError, OSError):
            pass
    gowork = root / "go.work"
    if gowork.is_file():
        uses = re.findall(r"^\s*(?:use\s+)?(\./[^\s()]+)\s*$", gowork.read_text(encoding="utf-8"), re.M)
        members += [root / u for u in uses if (root / u).is_dir()]
    for toml_file, keys in (("Cargo.toml", ("workspace",)), ("pyproject.toml", ("tool", "uv", "workspace"))):
        path = root / toml_file
        if path.is_file():
            try:
                table = tomllib.loads(path.read_text(encoding="utf-8"))
                for k in keys:
                    table = table.get(k, {})
                members += _globs(root, table.get("members", []))
            except (tomllib.TOMLDecodeError, AttributeError, OSError):
                pass
    return members


def _candidates(root: Path, exclude: list[str]) -> list[Path]:
    dirs = set(_workspace_members(root))
    for conv in CONVENTIONAL:
        if (root / conv).is_dir():
            dirs |= {d for d in (root / conv).iterdir() if d.is_dir() and not d.name.startswith(".")}
    dirs = {d for d in dirs if d.resolve() != root.resolve()
            and not any(d.relative_to(root).as_posix() == e or d.relative_to(root).as_posix().startswith(e + "/")
                        for e in exclude)}
    # a member nested in another member is part of it
    top = sorted(d for d in dirs if not any(o != d and o in d.parents for o in dirs))
    if not top and any((root / m).is_file() for m in MANIFESTS):
        return [root]
    return top


def _walk(base: Path, depth: int = 6):
    for dirpath, dirnames, filenames in os.walk(base):
        rel_depth = len(Path(dirpath).relative_to(base).parts)
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".") and rel_depth < depth]
        yield Path(dirpath), filenames


def _contracts(root: Path, sub: Path, name: str, contracts_dir: str) -> list[dict]:
    exports, protos, migrations = [], [], []
    for dirpath, files in _walk(sub):
        if dirpath.name == "migrations" and any(f.endswith(".sql") for f in files):
            migrations.append(dirpath)
        for f in files:
            path = dirpath / f
            if f.endswith(".proto"):
                protos.append(path)
            elif f.endswith((".yaml", ".yml", ".json")):
                head = _read(path)
                for kind, key in (("openapi", "openapi"), ("openapi", "swagger"), ("asyncapi", "asyncapi")):
                    if re.search(rf'^(?:{key}\s*:|\{{?\s*"{key}"\s*:)', head, re.M):
                        exports.append({"type": kind, "copy": path})
                        break
    if protos:
        common = Path(os.path.commonpath([p.parent for p in protos]))
        exports.append({"type": "protobuf", "copy": protos[0] if len(protos) == 1 else common})
    exports += [{"type": "db-schema", "copy": m} for m in migrations]
    out, used = [], set()
    for e in exports:
        base = e["copy"].name if e["copy"].is_file() else {"protobuf": "proto", "db-schema": "migrations"}[e["type"]]
        canonical = f"{contracts_dir}/{name}/{base}"
        n = 2
        while canonical in used:
            canonical, n = f"{contracts_dir}/{name}/{Path(base).stem}-{n}{Path(base).suffix}", n + 1
        used.add(canonical)
        out.append({"type": e["type"], "canonical": canonical, "copy": e["copy"].relative_to(root).as_posix()})
    return out


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9._-]+", "-", text.lower()).strip("-._") or "root"


def _existing(root: Path, registry_dir: str) -> dict:
    out = {}
    for path in sorted((root / registry_dir).glob("*.y*ml")):
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError:
            data = {}
        out[path.stem] = data if isinstance(data, dict) else {}
    return out


def cmd_scan(args) -> int:
    root = Path(args.project_root).resolve()
    cfg = orch_config(root, Path(args.orch_gate))
    registry_dir, contracts_dir = cfg["registry_dir"], cfg["contracts_dir"]
    existing = _existing(root, registry_dir)
    taken = {(str(d.get("repo", ".")).strip(), str(d.get("path", "")).strip().strip("/")) for d in existing.values()}
    names = set(existing)
    proposals, notes = [], []
    exclude = [registry_dir, contracts_dir, "_bmad", "_bmad-output", cfg["planning_artifacts"]]
    for checkout in [root, *[Path(c).resolve() for c in args.checkout]]:
        if checkout == root:
            repo = "."
        else:
            origin = git(checkout, "remote", "get-url", "origin", check=False)
            repo = origin.stdout.strip() if origin.returncode == 0 else ""
            if not repo:
                notes.append(f"{checkout}: no origin remote; its entries need 'repo' set to the clone URL by hand")
        for sub in _candidates(checkout, exclude if checkout == root else []):
            path = "." if sub == checkout else sub.relative_to(checkout).as_posix()
            if (repo, path) in taken:
                continue
            name = _slug(checkout.name if sub == checkout else sub.name)
            if name in names or name == "contracts":
                name = _slug(f"{sub.parent.name}-{sub.name}" if sub != checkout else f"{checkout.name}-repo")
            n = 2
            while name in names or name == "contracts":
                name, n = f"{name}-{n}", n + 1
            names.add(name)
            manifests = [m for m in MANIFESTS if (sub / m).is_file()]
            glob = "**" if path == "." else f"{path}/**"
            proposals.append({
                "name": name, "repo": repo, "path": path, "allowed_read": [glob], "allowed_write": [glob],
                "contracts": {"exports": _contracts(checkout, sub, name, contracts_dir), "imports": []},
                "evidence": manifests or ["directory convention"],
            })
    return emit({"ok": True, "registry_dir": registry_dir, "contracts_dir": contracts_dir,
                 "existing": sorted(existing), "proposals": proposals, "notes": notes})


def _entry_yaml(e: dict) -> str:
    body = {"name": e["name"], "repo": e["repo"], "path": e["path"]}
    if e.get("branch"):
        body["branch"] = e["branch"]
    body |= {"allowed_read": e.get("allowed_read") or [], "allowed_write": e["allowed_write"]}
    contracts = e.get("contracts") or {}
    body["contracts"] = {"exports": [{k: v for k, v in x.items() if v is not None} for x in contracts.get("exports") or []],
                         "imports": contracts.get("imports") or []}
    return yaml.safe_dump(body, sort_keys=False, allow_unicode=True, default_flow_style=None, width=120)


def cmd_write_registry(args) -> int:
    root = Path(args.project_root).resolve()
    registry_dir = orch_config(root, Path(args.orch_gate))["registry_dir"]
    try:
        plan = json.loads(Path(args.plan).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SetupError(f"cannot read --plan: {exc}", "bad-plan")
    entries = plan.get("subprojects") if isinstance(plan, dict) else plan
    if not isinstance(entries, list):
        raise SetupError("--plan must be a list of entries or {\"subprojects\": [...]}", "bad-plan")
    errors, written, kept = [], [], []
    for i, e in enumerate(entries):
        name = e.get("name") if isinstance(e, dict) else None
        if not isinstance(name, str) or not NAME.match(name) or name == "contracts":
            errors.append(f"entry {i}: name {name!r} must be lowercase kebab-case and not 'contracts'")
            continue
        if not all(isinstance(e.get(k), str) and e[k].strip() for k in ("repo", "path")) or not e.get("allowed_write"):
            errors.append(f"{name}: needs non-empty 'repo', 'path' and 'allowed_write'")
    if errors:
        return emit({"ok": False, "errors": errors}, 1)
    for e in entries:
        path = root / registry_dir / f"{e['name']}.yaml"
        rel = path.relative_to(root).as_posix()
        if path.exists() or path.with_suffix(".yml").exists():
            kept.append(rel)
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(_entry_yaml(e), encoding="utf-8")
        written.append(rel)
    return emit({"ok": not kept, "written": written, "kept_existing": kept}, 1 if kept else 0)


# ---- overrides ----

def _identity(entry: str) -> str:
    """Which orch entry this is, stable across template revisions: the lead phrase of an `orch ...` entry."""
    text = entry.strip()
    if text.startswith("orch "):
        first = text.splitlines()[0]
        return re.split(r" — | \(", first, maxsplit=1)[0]
    return text


def _render(template: Path, values: dict) -> str:
    text = template.read_text(encoding="utf-8")
    for placeholder, value in values.items():
        text = text.replace(placeholder, value)
    return text


def _toml_string(value: str):
    return tomlkit.string(value, multiline="\n" in value)


# A string key (on_complete) is shared with the team's own steps, so orch's part sits between these lines and
# only that span is ever replaced; team text before or after it is kept byte for byte.
BLOCK_BEGIN = "<!-- orch:begin — managed by orch-setup; put team steps outside these lines -->"
BLOCK_END = "<!-- orch:end -->"


def _block(value: str) -> str:
    return f"{BLOCK_BEGIN}\n{value.strip()}\n{BLOCK_END}"


def _span(text: str) -> tuple[int, int] | None:
    """Where the orch block sits in a string value, end exclusive; None if it is not (cleanly) there."""
    b = text.find(BLOCK_BEGIN)
    e = text.find(BLOCK_END, b + 1) if b >= 0 else -1
    if b < 0 or e < 0 or text.count(BLOCK_BEGIN) != 1 or text.count(BLOCK_END) != 1:
        return None
    return b, e + len(BLOCK_END)


def _stale_registry_facts(key: str, arr, ours: list[str]) -> list[str]:
    """`file:` entries shaped like orch's registry facts that the current render no longer holds."""
    shape = re.compile(r"file:\{project-root\}/.+/\*\.ya?ml")
    if not any(shape.fullmatch(x) for x in ours):
        return []
    return [f"{key}: '{x}' looks like an orch registry fact from an earlier orch_registry_dir; remove it if so"
            for x in map(str, arr) if shape.fullmatch(x) and x not in ours]


def merge_override(current: str | None, rendered: str, update: bool = False) -> tuple[str, list[str], list[dict]]:
    """Merge the rendered orch template into an override file's text. Returns (text, notes, drift).

    Missing orch entries are added. An orch entry that differs from the template (a template upgrade or a team
    edit) is replaced only with `update`; otherwise it is left and reported in `drift` with a diff.
    """
    fresh = current is None or not current.strip()
    doc = tomlkit.parse(rendered if fresh else current)
    ours = tomllib.loads(rendered)["workflow"]
    wf = doc.get("workflow")
    if wf is None:
        wf = tomlkit.table()
        doc["workflow"] = wf
    notes, drift = (["created"] if fresh else []), []
    for key, value in ours.items():
        if isinstance(value, list):
            arr = wf.get(key)
            if arr is None:
                arr = tomlkit.array()
                arr.multiline(True)
                wf[key] = arr
            for item in value:
                ids = [_identity(str(x)) for x in arr]
                if _identity(item) in ids:
                    i = ids.index(_identity(item))
                    if str(arr[i]) == item:
                        continue
                    if update:
                        arr[i] = _toml_string(item)
                        notes.append(f"{key}: updated '{_identity(item)[:60]}'")
                    else:
                        drift.append({"key": key, "entry": _identity(item)[:60],
                                      "diff": _diff(str(arr[i]), item, key)})
                else:
                    arr.multiline(True)
                    arr.append(_toml_string(item))
                    notes.append(f"{key}: added '{_identity(item)[:60]}'")
            notes += _stale_registry_facts(key, arr, value)
        elif isinstance(value, str):
            cur, block = str(wf.get(key, "") or ""), _block(value)
            span = _span(cur)
            if fresh or not cur.strip():
                new = block + "\n"
            elif span:
                if cur[span[0]:span[1]] == block:
                    continue
                if not update:
                    drift.append({"key": key, "entry": _identity(value)[:60],
                                  "diff": _diff(cur[span[0]:span[1]], block, key)})
                    continue
                new = cur[:span[0]] + block + cur[span[1]:]
            elif BLOCK_BEGIN in cur or BLOCK_END in cur:
                raise SetupError(f"'{key}' has a broken or repeated orch block marker; fix it by hand, keeping one "
                                 f"'{BLOCK_BEGIN}' ... '{BLOCK_END}' pair", "manual")
            elif value.strip() in cur:  # installed by hand from the template: mark it where it is
                new = cur.replace(value.strip(), block, 1)
            else:
                new = cur.rstrip() + "\n\n" + block + "\n"
                notes.append(f"{key}: appended the orch steps after the existing team steps; review that they "
                             "do not push or open a PR before the orch gate")
            if new != cur:
                wf[key] = _toml_string(new)
                notes.append(f"{key}: {'set' if fresh or not cur.strip() else 'updated'} '{_identity(value)[:60]}'")
        elif wf.get(key) != value:
            wf[key] = value
            notes.append(f"{key}: set")
    text = tomlkit.dumps(doc)
    merged = tomllib.loads(text)["workflow"]  # the result must parse and still hold every orch entry it had
    for key, value in ours.items():
        got = merged.get(key)
        held = {d["key"] for d in drift}
        if key in held:
            continue
        if not (all(v in got for v in value) if isinstance(value, list) else
                (_block(value) in got if isinstance(value, str) else got == value)):
            raise SetupError(f"merging '{key}' lost orch entries; nothing was written", "merge-failed")
    return text, notes, drift


def _override_values(root: Path, orch_gate: Path, cfg: dict) -> tuple[dict, list[str]]:
    cli = cli_location(root, orch_gate)
    return {"@ORCH_CLI@": "{project-root}/" + cli["path"], "@ORCH_REGISTRY_DIR@": cfg["registry_dir"]}, cli["warnings"]


def cmd_overrides(args) -> int:
    root = Path(args.project_root).resolve()
    cfg = orch_config(root, Path(args.orch_gate))
    values, cli_warnings = _override_values(root, Path(args.orch_gate), cfg)
    files, warnings = [], list(cli_warnings)
    templates = Path(args.orch_gate) / "assets" / "custom"
    for name in OVERRIDES:
        rendered = _render(templates / f"{name}.toml", values)
        target = root / "_bmad" / "custom" / f"{name}.toml"
        current = target.read_text(encoding="utf-8") if target.exists() else None
        rel = target.relative_to(root).as_posix()
        try:
            text, notes, drift = merge_override(current, rendered, args.update)
        except (tomlkit.exceptions.ParseError, tomllib.TOMLDecodeError) as exc:
            files.append({"file": rel, "status": "unparsable", "error": str(exc)})
            continue
        except SetupError as exc:
            if exc.code != "manual":
                raise
            files.append({"file": rel, "status": "manual", "error": str(exc)})
            continue
        status = "created" if current is None else ("unchanged" if text == current else "updated")
        if status != "unchanged" and not args.dry_run:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8")
        entry = {"file": rel, "status": status, "changes": [n for n in notes if n and n != "created"]}
        if drift:
            entry["drift"] = drift
        files.append(entry)
        personal = target.with_name(f"{name}.user.toml")
        if personal.exists():
            try:
                mine = tomllib.loads(personal.read_text(encoding="utf-8")).get("workflow", {})
            except tomllib.TOMLDecodeError:
                mine = {}
            for key, value in tomllib.loads(rendered)["workflow"].items():
                if isinstance(value, str) and str(mine.get(key) or "").strip() and _identity(value) not in mine[key]:
                    warnings.append(f"{personal.relative_to(root).as_posix()} sets {key}, which replaces the team "
                                    f"one and drops '{_identity(value)}'; add the orch steps from the team file to it")
    bad = [f for f in files if f["status"] in ("unparsable", "manual") or f.get("drift")]
    return emit({"ok": not bad, "dry_run": args.dry_run, "cli": values["@ORCH_CLI@"], "files": files,
                 "warnings": warnings}, 1 if bad else 0)


# ---- ci ----

def _diff(old: str, new: str, name: str, limit: int = 80) -> str:
    lines = list(difflib.unified_diff(old.splitlines(), new.splitlines(), f"{name} (current)", f"{name} (orch)",
                                      lineterm=""))
    return "\n".join(lines[:limit] + ([f"... {len(lines) - limit} more lines"] if len(lines) > limit else []))


def _install(root: Path, rel: str, text: str, dry_run: bool) -> dict:
    path = root / rel
    if path.exists():
        current = path.read_text(encoding="utf-8")
        if current == text:
            return {"file": rel, "status": "unchanged"}
        return {"file": rel, "status": "differs", "diff": _diff(current, text, rel)}
    if not dry_run:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return {"file": rel, "status": "created"}


def _gitlab_include(root: Path, dry_run: bool) -> dict:
    path, target = root / GITLAB_ROOT, CI_TARGETS["gitlab"]
    block = f"include:\n  - local: {target}\n"
    if not path.exists():
        if not dry_run:
            path.write_text(block, encoding="utf-8")
        return {"file": GITLAB_ROOT, "status": "created"}
    text = path.read_text(encoding="utf-8")
    if target in text:
        return {"file": GITLAB_ROOT, "status": "unchanged"}
    if re.search(r"^include\s*:", text, re.M):
        return {"file": GITLAB_ROOT, "status": "manual", "snippet": f"  - local: {target}",
                "hint": "add this item to the existing top-level include list"}
    if not dry_run:
        path.write_text(text + ("" if text.endswith("\n") else "\n") + "\n" + block, encoding="utf-8")
    return {"file": GITLAB_ROOT, "status": "updated"}


def cmd_ci(args) -> int:
    root = Path(args.project_root).resolve()
    cfg = orch_config(root, Path(args.orch_gate))
    cli = cli_location(root, Path(args.orch_gate))
    registry = _existing(root, cfg["registry_dir"])
    foreign = sorted(n for n, d in registry.items() if str(d.get("repo", "")).strip() not in (".", "./"))
    if foreign:
        return emit({"ok": False, "code": "polyrepo",
                     "error": "the CI templates cover a monorepo registry (repo: . only); these entries name other "
                              "repos, which need the polyrepo CI follow-up: " + ", ".join(foreign)}, 1)
    values = {"@ORCH_CLI_DIR@": cli["dir"], "@ORCH_CLI_PATH@": cli["path"],
              "@ORCH_REGISTRY_DIR@": cfg["registry_dir"], "@ORCH_CONTRACTS_DIR@": cfg["contracts_dir"],
              "@PLANNING_ARTIFACTS@": cfg["planning_artifacts"], "@ORCH_OWNERS@": args.owners or "@ORCH_OWNERS@"}

    def render(template: str) -> str:
        return _render(Path(args.orch_gate) / "assets" / "ci" / template, values)

    files = []
    for platform in dict.fromkeys(args.platform):
        files.append(_install(root, CI_TARGETS[platform], render(CI_TEMPLATES[platform]), args.dry_run))
        if platform == "gitlab":
            files.append(_gitlab_include(root, args.dry_run))
    owners = [line for line in render("CODEOWNERS").splitlines() if line.strip() and not line.startswith("#")]
    codeowners = {p: next((f for f in CODEOWNERS_FILES[p] if (root / f).exists()), None) for p in dict.fromkeys(args.platform)}
    warnings = list(cli["warnings"]) + ([] if registry else ["the registry is empty: commit the CI job in the same "
                                                              "PR as the registry, or every PR fails registry-empty"])
    left = [f for f in files if f["status"] in ("differs", "manual")]
    return emit({"ok": not left, "dry_run": args.dry_run, "files": files, "codeowners_lines": owners,
                 "codeowners_file": codeowners, "owners_set": bool(args.owners), "warnings": warnings},
                1 if left else 0)


# ---- hook ----

def hook_text(orch_py: Path, coord: str | None) -> str:
    coord_flag = f' --coord "{coord}"' if coord else ""
    return f"""#!/bin/sh
{HOOK_TAG}
# Runs the orch gate on every pushed story/* branch and blocks the push only on a failing verdict (exit 1).
# Any other exit is an environment problem: it warns and lets the push through. CI stays the real gate.
status=0
while read -r local_ref local_sha remote_ref remote_sha; do
  case "$local_ref" in refs/heads/story/*) ;; *) continue ;; esac
  case "$local_sha" in *[!0]*) ;; *) continue ;; esac  # branch deletion
  uv run "{orch_py}" gate --format text --head "$local_sha"{coord_flag} </dev/null
  code=$?
  if [ "$code" -eq 1 ]; then
    echo "orch pre-push: the gate failed for ${{local_ref#refs/heads/}}; fix it or push with --no-verify" >&2
    status=1
  elif [ "$code" -ne 0 ]; then
    echo "orch pre-push: the gate could not run (exit $code); push not blocked" >&2
  fi
done
exit $status
"""


def _hook_path(repo: Path) -> Path:
    """This clone's pre-push hook file, honouring core.hooksPath."""
    hooks = Path(git(repo, "rev-parse", "--git-path", "hooks").stdout.strip())
    return (hooks if hooks.is_absolute() else repo / hooks) / "pre-push"


def cmd_hook(args) -> int:
    repo = Path(git(Path(args.repo), "rev-parse", "--show-toplevel").stdout.strip())
    path = _hook_path(repo)
    orch_py = (Path(args.orch_gate) / "scripts" / "orch.py").resolve()
    text = hook_text(orch_py, str(Path(args.coord).resolve()) if args.coord else None)
    if path.exists():
        current = path.read_text(encoding="utf-8", errors="replace")
        if HOOK_TAG not in current:
            return emit({"ok": False, "status": "foreign-hook", "hook": str(path),
                         "hint": "a pre-push hook already exists (or a hook manager owns it); add the orch loop to it "
                                 "by hand, reading stdin only once", "snippet": text}, 1)
        if current == text:
            return emit({"ok": True, "status": "unchanged", "hook": str(path)})
        status = "updated"
    else:
        status = "created"
    if not args.dry_run:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return emit({"ok": True, "status": status, "hook": str(path), "dry_run": args.dry_run})


# ---- status ----

MERGE_DRIVER = "orch-sprint-status"  # orch-gate's sprint_status.DRIVER


def cmd_status(args) -> int:
    """What setup already put in place, so a re-run can offer only the parts the user wants to revisit."""
    root = Path(args.project_root).resolve()
    cfg = orch_config(root, Path(args.orch_gate))
    values, _ = _override_values(root, Path(args.orch_gate), cfg)
    overrides = {}
    for name in OVERRIDES:
        target = root / "_bmad" / "custom" / f"{name}.toml"
        if not target.exists():
            overrides[name] = "missing"
            continue
        rendered = _render(Path(args.orch_gate) / "assets" / "custom" / f"{name}.toml", values)
        try:
            text, notes, drift = merge_override(target.read_text(encoding="utf-8"), rendered)
        except (tomlkit.exceptions.ParseError, tomllib.TOMLDecodeError):
            overrides[name] = "unparsable"
        except SetupError:
            overrides[name] = "manual"
        else:
            overrides[name] = "drift" if drift else ("incomplete" if [n for n in notes if "earlier" not in n] else "installed")
    hook = _hook_path(root)
    hook_state = "none" if not hook.exists() else (
        "orch" if HOOK_TAG in hook.read_text(encoding="utf-8", errors="replace") else "foreign")
    driver = git(root, "config", "--get", f"merge.{MERGE_DRIVER}.driver", check=False).returncode == 0
    registry = sorted(_existing(root, cfg["registry_dir"]))
    return emit({"ok": True, "registry": registry, "overrides": overrides, "merge_driver": driver,
                 "ci": {p: (root / t).exists() for p, t in CI_TARGETS.items()}, "hook": hook_state})


# ---- check ----

def _stock_dirs(root: Path, name: str) -> list[Path]:
    return sorted(p for p in root.glob(f".*/skills/{name}") if p.is_dir())


def cmd_check(args) -> int:
    root = Path(args.project_root).resolve()
    cfg = orch_config(root, Path(args.orch_gate))
    problems, warnings = [], []
    ver = subprocess.run(["git", "--version"], capture_output=True, text=True).stdout
    m = re.search(r"(\d+)\.(\d+)", ver)
    git_version = tuple(int(x) for x in m.groups()) if m else (0, 0)
    if git_version < MIN_GIT:
        problems.append({"code": "git-too-old", "message": f"git {ver.strip()} is older than 2.25 (worktree, update-ref)"})
    tools = {t: bool(shutil.which(t)) for t in ("uv", "gh", "glab")}
    if not tools["gh"] and not tools["glab"]:
        warnings.append({"code": "no-host-cli", "message": "neither gh nor glab found: review waits fall back to branch age"})
    anchors = {}
    for skill, needed in STOCK_ANCHORS.items():
        dirs = _stock_dirs(root, skill)
        if not dirs:
            anchors[skill] = {"status": "not-installed"}
            warnings.append({"code": "stock-not-installed", "message": f"stock {skill} not found under .*/skills/"})
            continue
        for d in dirs:
            missing = [f"{f}: {a}" for f, a in needed if a not in _read(d / f, 1 << 20)]
            if skill == "bmad-sprint-planning":
                text = _read(d / "SKILL.md", 1 << 20)
                if not ("detect intent" in text and "{workflow.activation_steps_append}" in text
                        and text.index("detect intent") < text.index("{workflow.activation_steps_append}")):
                    missing.append("SKILL.md: activation_steps_append runs after intent detection")
            anchors[d.relative_to(root).as_posix()] = {"status": "missing" if missing else "ok", "missing": missing}
            if missing:
                problems.append({"code": "stock-anchor-missing",
                                 "message": f"{d.relative_to(root).as_posix()} no longer has what the orch override "
                                            f"relies on: {'; '.join(missing)}"})
    for key in ("registry_dir", "contracts_dir", "planning_artifacts"):
        if git(root, "check-ignore", "-q", f"{cfg[key]}/probe.yaml", check=False).returncode == 0:
            problems.append({"code": "orch-dir-ignored",
                             "message": f"{cfg[key]} is gitignored: it must be committed, and planning checks cannot see it"})
    for personal in ("_bmad/custom/config.user.toml", "_bmad/custom/bmad-build.user.toml"):
        if git(root, "check-ignore", "-q", personal, check=False).returncode != 0:
            warnings.append({"code": "personal-not-ignored",
                             "message": f"{personal} is not gitignored; personal layers must stay out of commits"})
    return emit({"ok": not problems, "git": ".".join(map(str, git_version)), "tools": tools, "stock_anchors": anchors,
                 "problems": problems, "warnings": warnings}, 1 if problems else 0)


# ---- parser ----

class JsonParser(argparse.ArgumentParser):
    def error(self, message):
        print(json.dumps({"ok": False, "code": "bad-args", "error": message}))
        sys.exit(2)


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--orch-gate", default=str(ORCH_GATE), help="orch-gate skill dir (default: beside this skill)")
    root = argparse.ArgumentParser(add_help=False)
    root.add_argument("--project-root", required=True, help="the coordination repo root (holds _bmad/)")
    p = JsonParser(prog="setup.py", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True, parser_class=JsonParser)
    sc = sub.add_parser("scan", parents=[common, root], help="propose registry entries")
    sc.add_argument("--checkout", action="append", default=[], help="local clone of a polyrepo code repo (repeatable)")
    wr = sub.add_parser("write-registry", parents=[common, root], help="write confirmed registry entries")
    wr.add_argument("--plan", required=True, help="JSON file: the confirmed entries")
    ov = sub.add_parser("overrides", parents=[common, root], help="install or merge the stock-skill overrides")
    ov.add_argument("--dry-run", action="store_true")
    ov.add_argument("--update", action="store_true",
                    help="replace orch entries that differ from the template (after the user confirmed the drift)")
    ci = sub.add_parser("ci", parents=[common, root], help="install the orch-gate CI job (monorepo)")
    ci.add_argument("--platform", action="append", choices=sorted(CI_TARGETS), required=True)
    ci.add_argument("--owners", help="CODEOWNERS owners for the suggested lines, e.g. @org/orch-owners")
    ci.add_argument("--dry-run", action="store_true")
    hk = sub.add_parser("hook", parents=[common], help="install the pre-push hook in this clone")
    hk.add_argument("--repo", default=".", help="repo whose clone gets the hook (default: cwd)")
    hk.add_argument("--coord", help="coordination repo checkout, for a polyrepo code repo")
    hk.add_argument("--dry-run", action="store_true")
    sub.add_parser("check", parents=[common, root], help="tools, stock anchors, ignored orch dirs")
    sub.add_parser("status", parents=[common, root], help="what setup already put in place in this clone")
    return p


COMMANDS = {"scan": cmd_scan, "write-registry": cmd_write_registry, "overrides": cmd_overrides, "ci": cmd_ci,
            "hook": cmd_hook, "check": cmd_check, "status": cmd_status}


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return COMMANDS[args.cmd](args)
    except SetupError as exc:
        return emit({"ok": False, "code": exc.code, "error": str(exc), **exc.fields}, 2)
    except Exception as exc:  # never let a crash look like a result the user must act on (exit 1)
        return emit({"ok": False, "code": "internal", "error": f"{type(exc).__name__}: {exc}"}, 2)


if __name__ == "__main__":
    sys.exit(main())
