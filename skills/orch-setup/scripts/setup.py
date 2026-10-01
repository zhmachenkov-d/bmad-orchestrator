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
  state           what is already in place (registry, draft, merge driver, overrides, hook, CI), for a re-run,
                  and the CI platforms and owners the repo already suggests
  scan            propose registry entries from the repo layout (and from --checkout code repos in a polyrepo)
                  and keep them as a registry draft in this clone's git dir until they are written
  write-registry  write confirmed entries (the draft, or --plan JSON) as <registry_dir>/<name>.yaml; never
                  overwrites; the draft is removed once every entry is written; --dry-run validates the plan
                  with orch-gate as if written and leaves no file
  overrides       install or merge the orch overrides into _bmad/custom/{bmad-build,bmad-create-epics-and-stories,
                  bmad-sprint-planning}.toml; missing orch entries are added, everything else is kept; an orch
                  entry that differs from the template is reported as drift and replaced only with --update
  ci              install the orch-gate CI job (--platform github|gitlab) for a monorepo registry, read through
                  `orch.py registry --working-tree`; CODEOWNERS lines are only suggested
  protection      read the GitHub or GitLab settings the gate relies on (required orch-gate check, code-owner
                  review, merge onto the gated main) with the user's gh / glab login; ok, missing or unknown each
  hook            install a pre-push hook that runs the gate on pushed story/* branches
  check           git/uv/host CLIs, stock anchors the overrides rely on, ignored orch dirs

Output is JSON on stdout. Exit codes: 0 = ok, 1 = something needs the user (invalid plan, a check failed,
a file left alone), 2 = usage or environment error.
"""

from __future__ import annotations

import argparse
import difflib
import glob
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tomllib
import urllib.parse
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


def orch_cli(root: Path, orch_gate: Path, cmd: str) -> tuple[int, dict]:
    """One orch-gate CLI read of the coordination repo's working tree; exit 2 raises."""
    cli = orch_gate / "scripts" / "orch.py"
    if not cli.is_file():
        raise SetupError(f"orch-gate not found at {orch_gate}; orch skills install side by side", "no-orch-gate")
    # no bytecode: a read of the project must leave its working tree as it was
    res = subprocess.run(["uv", "run", str(cli), cmd, "--working-tree", "--offline", "--repo", str(root)],
                         capture_output=True, text=True, env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
    try:
        out = json.loads(res.stdout)
    except json.JSONDecodeError:
        raise SetupError(f"orch.py {cmd} failed: {(res.stderr or res.stdout).strip()[-500:]}", f"orch-{cmd}")
    if res.returncode not in (0, 1):
        raise SetupError(f"orch.py {cmd}: {out.get('error')}", out.get("code") or f"orch-{cmd}")
    return res.returncode, out


def orch_config(root: Path, orch_gate: Path) -> dict:
    """Resolved orch config of the coordination repo's working tree, from orch-gate's CLI."""
    code, out = orch_cli(root, orch_gate, "config")
    if code != 0:
        raise SetupError(f"orch.py config: {out.get('error')}", out.get("code") or "orch-config")
    return out["config"]


def cli_location(root: Path, orch_gate: Path) -> dict:
    """Where the committed files point: orch-gate relative to the project root, with its caveats.

    The path is resolved, so committed files name the real directory even when orch-gate was reached through a
    symlink; one that leads out of the project is outside."""
    try:
        rel = orch_gate.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        raise SetupError(f"orch-gate ({orch_gate.resolve()}) is outside the project (or reached through a symlink "
                         "that leads out of it); committed team files must name a path every clone has — install "
                         "orch into the project", "orch-gate-outside")
    warnings = []
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


def _globs(root: Path, patterns: list, notes: list[str]) -> list[Path]:
    """Workspace member dirs inside the checkout. A `**` pattern matches every nested dir, so it keeps only dirs
    that hold a manifest, never the glob base; a member outside the checkout is skipped with a note."""
    found, top = [], root.resolve()
    for pat in patterns:
        if not isinstance(pat, str) or not pat.strip() or pat.startswith("!"):
            continue
        rel = pat.strip()
        if PurePosixPath(rel).is_absolute() or ".." in PurePosixPath(rel).parts:
            notes.append(f"{root}: workspace member '{pat}' is outside this checkout; scan it with --checkout")
            continue
        rel = rel.removeprefix("./").strip("/")
        if not rel or rel == ".":
            continue
        base = root / rel.split("**", 1)[0].rstrip("/") if "**" in rel else None
        for p in sorted(root.glob(rel)):
            parts = p.relative_to(root).parts
            if not p.is_dir() or any(x in SKIP_DIRS or x.startswith(".") for x in parts):
                continue
            if not p.resolve().is_relative_to(top):
                notes.append(f"{p.relative_to(root)} links outside the checkout; skipped")
            elif base is None or (p != base and any((p / m).is_file() for m in MANIFESTS)):
                found.append(p)
    return found


def _workspace_members(root: Path, notes: list[str]) -> list[Path]:
    members = []
    pkg = root / "package.json"
    if pkg.is_file():
        try:
            ws = json.loads(pkg.read_text(encoding="utf-8")).get("workspaces")
        except (json.JSONDecodeError, AttributeError, OSError):
            ws = None
        members += _globs(root, ws.get("packages", []) if isinstance(ws, dict) else ws or [], notes)
    pnpm = root / "pnpm-workspace.yaml"
    if pnpm.is_file():
        try:
            members += _globs(root, (yaml.safe_load(pnpm.read_text(encoding="utf-8")) or {}).get("packages", []), notes)
        except (yaml.YAMLError, AttributeError, OSError):
            pass
    gowork = root / "go.work"
    if gowork.is_file():
        uses = re.findall(r"^\s*(?:use\s+)?(\./[^\s()]+)\s*$", gowork.read_text(encoding="utf-8"), re.M)
        members += _globs(root, [glob.escape(u) for u in uses], notes)
    for toml_file, keys in (("Cargo.toml", ("workspace",)), ("pyproject.toml", ("tool", "uv", "workspace"))):
        path = root / toml_file
        if path.is_file():
            try:
                table = tomllib.loads(path.read_text(encoding="utf-8"))
                for k in keys:
                    table = table.get(k, {})
                members += _globs(root, table.get("members", []), notes)
            except (tomllib.TOMLDecodeError, AttributeError, OSError):
                pass
    return members


def _candidates(root: Path, exclude: list[str], notes: list[str]) -> list[Path]:
    dirs = set(_workspace_members(root, notes))
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


def draft_path(root: Path) -> Path:
    """The registry draft under negotiation. It lives in this clone's git dir, so it survives an interrupted
    conversation but is never committed."""
    common = Path(git(root, "rev-parse", "--git-common-dir").stdout.strip())
    return (common if common.is_absolute() else root / common) / "orch-setup" / "registry-draft.json"


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
        for sub in _candidates(checkout, exclude if checkout == root else [], notes):
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
    draft = draft_path(root)
    if draft.exists() and not args.fresh:
        status = "kept"  # an earlier negotiation: the user resumes it or rescans with --fresh
    elif proposals:
        draft.parent.mkdir(parents=True, exist_ok=True)
        draft.write_text(json.dumps({"subprojects": proposals}, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        status = "written"
    else:
        draft.unlink(missing_ok=True)
        status = "none"
    return emit({"ok": True, "registry_dir": registry_dir, "contracts_dir": contracts_dir,
                 "existing": sorted(existing), "proposals": proposals, "notes": notes,
                 "draft": {"path": str(draft), "status": status}})


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
    draft = draft_path(root)
    source = Path(args.plan) if args.plan else draft
    if not args.plan and not draft.exists():
        raise SetupError("no --plan and no registry draft; run scan first", "no-plan")
    try:
        plan = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SetupError(f"cannot read {source}: {exc}", "bad-plan")
    entries = plan.get("subprojects") if isinstance(plan, dict) else plan
    if not isinstance(entries, list):
        raise SetupError("the plan must be a list of entries or {\"subprojects\": [...]}", "bad-plan")
    errors, written, kept, seen = [], [], [], set()
    for i, e in enumerate(entries):
        name = e.get("name") if isinstance(e, dict) else None
        if not isinstance(name, str) or not NAME.match(name) or name == "contracts":
            errors.append(f"entry {i}: name {name!r} must be lowercase kebab-case and not 'contracts'")
            continue
        if name in seen:
            errors.append(f"entry {i}: name '{name}' is used by an earlier entry of the plan")
        seen.add(name)
        if not all(isinstance(e.get(k), str) and e[k].strip() for k in ("repo", "path")) or not e.get("allowed_write"):
            errors.append(f"{name}: needs non-empty 'repo', 'path' and 'allowed_write'")
    if errors:
        return emit({"ok": False, "plan": str(source), "errors": errors}, 1)
    reg_dir = root / registry_dir
    # a dry run removes what it wrote, down to the first directory it had to create
    new_dir = next((d for d in [*reversed(Path(registry_dir).parents), Path(registry_dir)]
                    if str(d) != "." and not (root / d).exists()), None)
    try:
        for e in entries:
            path = reg_dir / f"{e['name']}.yaml"
            rel = path.relative_to(root).as_posix()
            if path.exists() or path.with_suffix(".yml").exists():
                kept.append(rel)
                continue
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(_entry_yaml(e), encoding="utf-8")
            written.append(rel)
        if args.dry_run:
            _, out = orch_cli(root, Path(args.orch_gate), "registry")
    finally:
        if args.dry_run:
            for rel in written:
                (root / rel).unlink(missing_ok=True)
            if new_dir is not None:
                shutil.rmtree(root / new_dir, ignore_errors=True)
    if args.dry_run:
        # canonical-missing is expected until a contract story adds the canonical
        blocking = [i for i in out["issues"] if i["code"] != "canonical-missing"]
        return emit({"ok": not kept and not blocking, "dry_run": True, "plan": str(source), "would_write": written,
                     "kept_existing": kept, "issues": out["issues"]}, 1 if kept or blocking else 0)
    removed = source == draft and not kept
    if removed:
        draft.unlink()
    return emit({"ok": not kept, "plan": str(source), "written": written, "kept_existing": kept,
                 "draft_removed": removed}, 1 if kept else 0)


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


def _override_values(cfg: dict, cli: dict) -> dict:
    return {"@ORCH_CLI@": "{project-root}/" + cli["path"], "@ORCH_REGISTRY_DIR@": cfg["registry_dir"]}


def cmd_overrides(args) -> int:
    root = Path(args.project_root).resolve()
    cfg = orch_config(root, Path(args.orch_gate))
    cli = cli_location(root, Path(args.orch_gate))
    values = _override_values(cfg, cli)
    files, warnings = [], list(cli["warnings"])
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


def monorepo_registry(root: Path, orch_gate: Path) -> tuple[dict, dict | None]:
    """The registry as the gate reads it, and the refusal to emit (exit 1) when it is invalid or names other repos.

    orch-gate drops an entry it cannot load and normalizes `repo`, so this reads it through orch-gate's CLI."""
    _, out = orch_cli(root, orch_gate, "registry")
    registry = {n: s for n, s in out["subprojects"].items() if n != "contracts"}
    invalid = [i for i in out["issues"] if i.get("subproject") not in registry]
    if invalid:
        return registry, {"ok": False, "code": "registry-invalid", "issues": invalid,
                          "error": "orch-gate cannot load these registry entries; fix them first: "
                                   + "; ".join(i["message"] for i in invalid)}
    foreign = sorted(n for n, s in registry.items() if s["repo"] != ".")
    if foreign:
        return registry, {"ok": False, "code": "polyrepo",
                          "error": "the CI templates cover a monorepo registry (repo: . only); these entries name "
                                   "other repos, which need the polyrepo CI follow-up: " + ", ".join(foreign)}
    return registry, None


def cmd_ci(args) -> int:
    root = Path(args.project_root).resolve()
    cfg = orch_config(root, Path(args.orch_gate))
    cli = cli_location(root, Path(args.orch_gate))
    registry, refusal = monorepo_registry(root, Path(args.orch_gate))
    if refusal:
        return emit(refusal, 1)
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


# ---- protection ----
# The gate cannot see repository settings, so this reads the ones its guarantees rest on through `gh api` or
# `glab api`, with the user's own login. It only reads: no setting, file or ref changes, no fetch, and no token
# leaves the CLI.

GH = "gh"
GLAB = "glab"
CLI_NAMES = {"github": "GitHub CLI", "gitlab": "GitLab CLI"}
SSH_API_HOSTS = {"github": {"ssh.github.com": "github.com"},  # SSH over 443; the API lives on the main host
                 "gitlab": {"altssh.gitlab.com": "gitlab.com"}}
HOST_TIMEOUT = 30
GATE_CHECK = "orch-gate"  # the job/check name in assets/ci/github-actions.yml
RULES_PAGE = 100
STATUS_LINE = re.compile(r"^HTTP/\S+ (\d{3})")
SEGMENT = re.compile(r"^[A-Za-z0-9._-]+$")


def parse_origin(url: str, nested: bool = False) -> tuple[str, str, bool] | None:
    """(host, owner/repo, is_ssh) of an https, `git@host:path` or `ssh://` origin; None when it is not one.

    With `nested` (GitLab) the path may have nested groups (`group/sub/project`, 2 or more segments); otherwise
    it is exactly `owner/repo`. Userinfo (a user, or a token in an https URL) and the port are dropped, so no
    credential reaches the output."""
    url = url.strip()
    m = re.match(r"^(https?|ssh)://", url, re.I)
    if m:
        try:
            parts = urllib.parse.urlsplit(url)
            host = parts.hostname
        except ValueError:
            return None
        path, ssh = parts.path, m.group(1).lower() == "ssh"
    else:
        m = re.match(r"^(?:[^@/:]+@)?([^@/:]+):(?!/)(.+)$", url)  # scp-like: [user@]host:path
        if not m:
            return None
        host, path, ssh = m.group(1), m.group(2), True
    if not host or host.startswith("-"):
        return None
    path = path.strip("/").removesuffix(".git")
    segments = path.split("/")
    count_ok = len(segments) >= 2 if nested else len(segments) == 2
    if not count_ok or not all(SEGMENT.match(s) and s not in (".", "..") for s in segments):
        return None
    return host.lower(), path, ssh


def ssh_host(alias: str) -> str:
    """The real host behind an SSH alias (`ssh -G`: no connection; evaluates the user's ssh_config); the alias
    on failure."""
    try:
        res = subprocess.run(["ssh", "-G", alias], capture_output=True, text=True, timeout=HOST_TIMEOUT)
    except (OSError, subprocess.TimeoutExpired):
        return alias
    if res.returncode == 0:
        for line in res.stdout.splitlines():
            key, _, value = line.strip().partition(" ")
            if key.lower() == "hostname" and value.strip():
                return value.strip().lower()
    return alias


def _cli(platform: str) -> tuple[str, str]:
    """(executable, short name) of the platform's CLI; read at call time so tests can swap the executable."""
    return (GH, "gh") if platform == "github" else (GLAB, "glab")


def cli_api(platform: str, host: str, endpoint: str) -> tuple[int | None, object, str]:
    """(HTTP status, JSON body, error text) of one `gh api --include` / `glab api --include` read. The status
    comes from the response's first line, not the CLI's exit code; None when there is none (no CLI, no login,
    no network)."""
    exe, name = _cli(platform)
    try:
        res = subprocess.run([exe, "api", "--include", "--hostname", host, endpoint],
                             capture_output=True, text=True, timeout=HOST_TIMEOUT)
    except FileNotFoundError:
        return None, None, f"{name} ({CLI_NAMES[platform]}) is not installed"
    except OSError as exc:
        return None, None, f"{name} cannot run: {exc}"
    except subprocess.TimeoutExpired:
        return None, None, f"{name} api timed out after {HOST_TIMEOUT}s"
    out = res.stdout.replace("\r\n", "\n")
    m = STATUS_LINE.match(out)
    if not m:
        text = (res.stderr or res.stdout).strip()
        return None, None, text.splitlines()[-1] if text else f"{name} api exited {res.returncode} without a response"
    _, _, body = out.partition("\n\n")
    try:
        data = json.loads(body) if body.strip() else None
    except json.JSONDecodeError:
        data = None
    message = data.get("message", "") if isinstance(data, dict) else ""
    return int(m.group(1)), data, str(message)


def gh_api(host: str, endpoint: str) -> tuple[int | None, object, str]:
    return cli_api("github", host, endpoint)


def _source(label: str, status: int | None, data, error: str) -> dict:
    """A settings source: readable with its data (None = nothing configured), or unreadable with why."""
    if status is None:
        return {"label": label, "readable": False, "data": None, "why": error}
    if 200 <= status < 300:
        return {"label": label, "readable": True, "data": data, "why": ""}
    error = error.removeprefix(f"{status} ")  # GitLab messages repeat the status: "404 Project Not Found"
    return {"label": label, "readable": False, "data": None, "why": f"HTTP {status}" + (f" {error}" if error else "")}


def read_github_sources(host: str, project: str, branch: str) -> list[dict]:
    """The active rulesets (repo and org) for the branch and its classic branch protection."""
    b = urllib.parse.quote(branch, safe="")
    status, data, error = gh_api(host, f"repos/{project}/rules/branches/{b}?per_page={RULES_PAGE}")
    rules = _source("rulesets", status, data, error)
    if rules["readable"] and not isinstance(rules["data"], list):
        rules.update(readable=False, data=None, why="unexpected response")
    elif rules["readable"] and len(rules["data"]) >= RULES_PAGE:
        rules.update(readable=False, data=None, why=f"{RULES_PAGE} or more rules; not all of them were read")
    status, data, error = gh_api(host, f"repos/{project}/branches/{b}/protection")
    if status == 404 and error == "Branch not protected":
        classic = {"label": "classic branch protection", "readable": True, "data": None, "why": ""}
    else:
        # a plain `Not Found` is what a non-admin gets, so it says nothing about the settings
        classic = _source("classic branch protection", status, data, error)
        if classic["readable"] and not isinstance(classic["data"], dict):
            classic.update(readable=False, data=None, why="unexpected response")
    return [rules, classic]


def _rules(source: dict, kind: str) -> list[dict]:
    if source["label"] != "rulesets" or not source["readable"]:
        return []
    return [r.get("parameters") or {} for r in source["data"] if isinstance(r, dict) and r.get("type") == kind]


def _classic(source: dict) -> dict:
    return source["data"] if source["label"] != "rulesets" and source["readable"] and source["data"] else {}


def _requires_check(source: dict) -> bool:
    for params in _rules(source, "required_status_checks"):
        if any(isinstance(c, dict) and c.get("context") == GATE_CHECK
               for c in params.get("required_status_checks") or []):
            return True
    checks = _classic(source).get("required_status_checks") or {}
    return GATE_CHECK in (checks.get("contexts") or []) or any(
        isinstance(c, dict) and c.get("context") == GATE_CHECK for c in checks.get("checks") or [])


def _requires_code_owners(source: dict) -> bool:
    if any(p.get("require_code_owner_review") is True for p in _rules(source, "pull_request")):
        return True
    return (_classic(source).get("required_pull_request_reviews") or {}).get("require_code_owner_reviews") is True


def _merges_onto_gated_main(source: dict) -> bool:
    if _rules(source, "merge_queue"):
        return True
    if any(p.get("strict_required_status_checks_policy") is True for p in _rules(source, "required_status_checks")):
        return True
    return (_classic(source).get("required_status_checks") or {}).get("strict") is True


def _status(sources: list[dict], test) -> tuple[str, dict | None]:
    """ok when a readable source satisfies the test, missing when every source was read and none does."""
    hit = next((s for s in sources if s["readable"] and test(s)), None)
    if hit:
        return "ok", hit
    return ("missing" if all(s["readable"] for s in sources) else "unknown"), None


def _unread(sources: list[dict]) -> str:
    return "; ".join(f"{s['label']}: {s['why']}" for s in sources if not s["readable"])


def github_settings(sources: list[dict], branch: str) -> list[dict]:
    checklist = {
        "required-check": f"Require the `{GATE_CHECK}` status check on `{branch}` (a ruleset or branch protection "
                          "rule), so no PR merges without the gate's verdict.",
        "code-owner-review": f"Require review from Code Owners on `{branch}`, so a PR cannot change the CI files, "
                             "the registry or orch itself without its owners.",
        "merge-onto-gated-main": f"Make every PR merge onto the `{branch}` the gate saw: a merge queue, or "
                                 "\"Require branches to be up to date before merging\" — the gate judges a "
                                 f"coordination PR on a trial merge with `{branch}` as of the run.",
    }
    out = []
    status, hit = _status(sources, _requires_check)
    out.append({"id": "required-check", "status": status, "message": {
        "ok": f"`{GATE_CHECK}` is a required status check ({hit and hit['label']})",
        "missing": f"no ruleset or branch protection on `{branch}` requires the `{GATE_CHECK}` check",
        "unknown": f"cannot tell whether `{GATE_CHECK}` is required ({_unread(sources)})"}[status]})
    status, hit = _status(sources, _requires_code_owners)
    out.append({"id": "code-owner-review", "status": status, "message": {
        "ok": f"code-owner review is required ({hit and hit['label']})",
        "missing": f"no ruleset or branch protection on `{branch}` requires code-owner review",
        "unknown": f"cannot tell whether code-owner review is required ({_unread(sources)})"}[status]})
    gate = out[0]["status"]
    if gate != "ok":
        merge = {"status": gate, "message": f"depends on the required `{GATE_CHECK}` check, which is {gate}"
                 + (f" ({_unread(sources)})" if gate == "unknown" else "")}
    else:
        status, hit = _status(sources, _merges_onto_gated_main)
        classic = next(s for s in sources if s["label"] != "rulesets")
        if status == "missing" and classic["data"]:
            status = "unknown"  # classic "Require merge queue" is not in the REST response
            why = "classic branch protection is on, but whether it requires a merge queue is not readable"
        else:
            why = _unread(sources)
        merge = {"status": status, "message": {
            "ok": f"PRs merge onto the gated `{branch}` (merge queue or up-to-date branches, {hit and hit['label']})",
            "missing": f"no merge queue and no \"up to date before merging\" rule on `{branch}`",
            "unknown": f"cannot tell whether PRs merge onto the gated `{branch}` ({why})"}[status]}
    out.append({"id": "merge-onto-gated-main", **merge})
    for s in out:
        s["checklist"] = checklist[s["id"]]
    return out


GL_GATE_FIELDS = ("only_allow_merge_if_pipeline_succeeds", "allow_merge_on_skipped_pipeline")
GL_OWNERS_FIELD = "code_owner_approval_required"


def read_gitlab_sources(host: str, project: str, branch: str) -> list[dict]:
    """The project (merge settings) and its protected branches, `inherited` group rules included."""
    p = urllib.parse.quote(project, safe="")
    status, data, error = cli_api("gitlab", host, f"projects/{p}")
    proj = _source("project", status, data, error)
    if proj["readable"] and not isinstance(proj["data"], dict):
        proj.update(readable=False, data=None, why="unexpected response")
    elif status in (401, 404):  # glab without a login reads anonymously: a private project looks missing
        proj["why"] += f"; if the project is private, sign in with `glab auth login --hostname {host}`"
    status, data, error = cli_api("gitlab", host, f"projects/{p}/protected_branches?per_page={RULES_PAGE}")
    rules = _source("protected branches", status, data, error)
    if rules["readable"] and not isinstance(rules["data"], list):
        rules.update(readable=False, data=None, why="unexpected response")
    elif rules["readable"] and len(rules["data"]) >= RULES_PAGE:
        rules.update(readable=False, data=None, why=f"{RULES_PAGE} or more protected branches; not all were read")
    return [proj, rules]


def protected_name_matches(pattern: str, branch: str) -> bool:
    """GitLab's protected-branch match: the whole name, case-sensitive, `*` matching anything (`/` too)."""
    return re.fullmatch("".join(".*" if c == "*" else re.escape(c) for c in pattern), branch) is not None


def _matching_rules(source: dict, branch: str) -> list[dict]:
    return [r for r in source["data"] or [] if isinstance(r, dict) and isinstance(r.get("name"), str)
            and protected_name_matches(r["name"], branch)]


def gitlab_settings(sources: list[dict], branch: str, host: str | None) -> list[dict]:
    proj, rules = sources
    login = f"; sign in with `glab auth login --hostname {host}`" if host else ""
    checklist = {
        "required-check": "Settings → Merge requests: turn on \"Pipelines must succeed\" and turn off \"Skipped "
                          f"pipelines are considered successful\", so no MR merges without the `{GATE_CHECK}` "
                          "job's verdict.",
        "code-owner-review": f"Protect `{branch}` with \"Code owner approval\" (needs GitLab Premium or higher), so "
                             "an MR cannot change the CI files, the registry or orch itself without its owners.",
        "merge-onto-gated-main": "Settings → Merge requests: merge method \"Fast-forward merge\" or \"Merge commit "
                                 f"with semi-linear history\" — the gate judges a coordination MR on a trial merge "
                                 f"with `{branch}` as of the run.",
    }

    def absent(fields: list[str]) -> str:
        return f"{proj['label']}: {', '.join(fields)} not returned{login}"

    out = []
    # required-check: a known breaking value is missing; else an absent field or unreadable source is unknown
    data = proj["data"] if proj["readable"] else {}
    succeeds, skipped = (data.get(f) for f in GL_GATE_FIELDS)
    if proj["readable"] and GL_GATE_FIELDS[0] in data and succeeds is not True:
        status, why = "missing", "\"Pipelines must succeed\" is off"
    elif proj["readable"] and skipped is True:
        status, why = "missing", "skipped pipelines are considered successful"
    elif not proj["readable"]:
        status, why = "unknown", _unread([proj])
    elif missing_fields := [f for f in GL_GATE_FIELDS if f not in data]:
        status, why = "unknown", absent(missing_fields)
    else:
        status, why = "ok", ""
    out.append({"id": "required-check", "status": status, "message": {
        "ok": "MRs merge only when their pipeline succeeds (and not when it is skipped)",
        "missing": f"MRs can merge without a successful pipeline: {why}",
        "unknown": f"cannot tell whether MRs need a successful pipeline ({why})"}[status]})
    gate_why = why

    status, _ = _status([rules], lambda s: any(r.get(GL_OWNERS_FIELD) is True for r in _matching_rules(s, branch)))
    matching = _matching_rules(rules, branch) if rules["readable"] else []
    why = _unread([rules])
    if status == "missing" and matching and all(GL_OWNERS_FIELD not in r for r in matching):
        status, why = "unknown", (f"{rules['label']}: {GL_OWNERS_FIELD} not returned (GitLab Community Edition "
                                  f"has no code-owner approval){login}")
    hit = next((r for r in matching if r.get(GL_OWNERS_FIELD) is True), {})
    out.append({"id": "code-owner-review", "status": status, "message": {
        "ok": f"code-owner approval is required on `{branch}` (protected branch rule `{hit.get('name')}`"
              + (", inherited from the group)" if hit.get("inherited") is True else ")"),
        "missing": (f"`{branch}` is protected without code-owner approval" if matching
                    else f"`{branch}` is not a protected branch, so code-owner approval is not required"),
        "unknown": f"cannot tell whether code-owner approval is required ({why})"}[status]})

    gate = out[0]["status"]
    if gate != "ok":
        merge = {"status": gate, "message": f"depends on the required `{GATE_CHECK}` pipeline, which is {gate}"
                 + (f" ({gate_why})" if gate == "unknown" else "")}
    else:
        method = data.get("merge_method")
        status = {"ff": "ok", "rebase_merge": "ok", "merge": "missing"}.get(method if isinstance(method, str)
                                                                             else "", "unknown")
        why = absent(["merge_method"]) if "merge_method" not in data else f"merge_method is {json.dumps(method)}"
        merge = {"status": status, "message": {
            "ok": f"MRs merge onto the gated `{branch}` (merge method {method})",
            "missing": f"the merge method is \"Merge commit\", so an MR can merge onto a `{branch}` the gate did "
                       "not see",
            "unknown": f"cannot tell whether MRs merge onto the gated `{branch}` ({why})"}[status]}
    out.append({"id": "merge-onto-gated-main", **merge})
    for s in out:
        s["checklist"] = checklist[s["id"]]
    return out


PROTECTION = {  # platform → (source labels, reader, evaluator)
    "github": (("rulesets", "classic branch protection"), read_github_sources,
               lambda sources, branch, host: github_settings(sources, branch)),
    "gitlab": (("project", "protected branches"), read_gitlab_sources, gitlab_settings),
}


def cmd_protection(args) -> int:
    root = Path(args.project_root).resolve()
    cfg = orch_config(root, Path(args.orch_gate))
    _, refusal = monorepo_registry(root, Path(args.orch_gate))
    if refusal:
        return emit(refusal, 1)
    platform = args.platform
    labels, read_sources, evaluate = PROTECTION[platform]
    branch = cfg["main_branch"]
    origin = git(root, "remote", "get-url", "origin", check=False)
    parsed = (parse_origin(origin.stdout, nested=platform == "gitlab")
              if origin.returncode == 0 and origin.stdout.strip() else None)
    host = project = None
    if parsed is None:
        why = (f"the origin remote is not a {'GitHub owner/repo' if platform == 'github' else 'GitLab project'} "
               "URL (https, git@host:path or ssh://)"
               if origin.returncode == 0 and origin.stdout.strip() else "there is no origin remote")
        sources = [{"label": label, "readable": False, "data": None, "why": why} for label in labels]
    else:
        host, project, ssh = parsed
        if ssh:
            host = ssh_host(host)
            host = SSH_API_HOSTS[platform].get(host, host)
        sources = read_sources(host, project, branch)
        for s in sources:
            if not s["readable"]:
                s["why"] = f"{s['why']} ({_cli(platform)[1]} api on {host})"
    settings = evaluate(sources, branch, host)
    problems = [{"code": "protection-missing", "message": f"{s['id']}: {s['message']}"}
                for s in settings if s["status"] == "missing"]
    warnings = [{"code": "protection-unverified", "message": f"{s['id']}: {s['message']}"}
                for s in settings if s["status"] == "unknown"]
    return emit({"ok": not problems,
                 "platforms": {platform: {"host": host, "project": project, "branch": branch, "settings": settings}},
                 "problems": problems, "warnings": warnings}, 1 if problems else 0)


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


def hook_path(clone: Path) -> Path:
    """This clone's pre-push hook, honouring core.hooksPath."""
    repo = Path(git(clone, "rev-parse", "--show-toplevel").stdout.strip())
    hooks = Path(git(repo, "rev-parse", "--git-path", "hooks").stdout.strip())
    return (hooks if hooks.is_absolute() else repo / hooks) / "pre-push"


def cmd_hook(args) -> int:
    path = hook_path(Path(args.repo))
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


# ---- state ----

def cmd_state(args) -> int:
    """What orch-setup already put in place, so a re-run offers only what is missing or what the user asks for."""
    root = Path(args.project_root).resolve()
    orch_gate = Path(args.orch_gate)
    cfg = orch_config(root, orch_gate)
    registry = sorted(_existing(root, cfg["registry_dir"]))
    draft = draft_path(root)
    try:
        values = _override_values(cfg, cli_location(root, orch_gate))
    except SetupError:
        values = None  # `overrides` reports why; here the files are only found, not compared
    overrides = {}
    for name in OVERRIDES:
        target = root / "_bmad" / "custom" / f"{name}.toml"
        if not target.exists():
            overrides[name] = "missing"
            continue
        if values is None:
            overrides[name] = "present"
            continue
        current = target.read_text(encoding="utf-8")
        try:
            text, _, drift = merge_override(current, _render(orch_gate / "assets" / "custom" / f"{name}.toml", values))
            overrides[name] = "drift" if drift else ("current" if text == current else "incomplete")
        except (tomlkit.exceptions.ParseError, tomllib.TOMLDecodeError):
            overrides[name] = "unparsable"
        except SetupError as exc:
            if exc.code != "manual":
                raise
            overrides[name] = "manual"
    hook = hook_path(root)
    hook_state = "absent" if not hook.exists() else (
        "orch" if HOOK_TAG in hook.read_text(encoding="utf-8", errors="replace") else "foreign")
    # merge.<name>.driver as orch-gate's `sprint-status install-driver` registers it (sprint_status.DRIVER)
    driver = git(root, "config", "--get", "merge.orch-sprint-status.driver", check=False).returncode == 0
    ci = {p: (root / t).exists() for p, t in CI_TARGETS.items()}
    in_place = bool(registry or driver or ci["github"] or ci["gitlab"]
                    or any(s != "missing" for s in overrides.values()))
    return emit({"ok": True, "in_place": in_place, "registry": registry,
                 "draft": str(draft) if draft.exists() else None, "merge_driver": driver, "overrides": overrides,
                 "hook": hook_state, "ci": ci, "ci_defaults": ci_defaults(root)})


def ci_defaults(root: Path) -> dict:
    """What the repo already says about the CI question: platforms in use and the owners of `*` in CODEOWNERS."""
    origin = git(root, "remote", "get-url", "origin", check=False).stdout.lower()
    platforms = [p for p, used in (("github", (root / ".github" / "workflows").is_dir() or "github" in origin),
                                   ("gitlab", (root / GITLAB_ROOT).exists() or "gitlab" in origin)) if used]
    owners = None
    for f in dict.fromkeys(f for files in CODEOWNERS_FILES.values() for f in files):
        if (root / f).is_file():
            for line in _read(root / f, 65536).splitlines():
                parts = line.split("#", 1)[0].split()
                if len(parts) > 1 and parts[0] == "*":
                    owners = " ".join(parts[1:])  # the last matching line wins, as in CODEOWNERS
            break
    return {"platforms": platforms, "owners": owners}


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
    sc.add_argument("--fresh", action="store_true", help="replace an earlier registry draft with this scan")
    wr = sub.add_parser("write-registry", parents=[common, root], help="write confirmed registry entries")
    wr.add_argument("--plan", help="JSON file: the confirmed entries (default: the registry draft scan wrote)")
    wr.add_argument("--dry-run", action="store_true", help="validate the plan with orch-gate as if written, write nothing")
    ov = sub.add_parser("overrides", parents=[common, root], help="install or merge the stock-skill overrides")
    ov.add_argument("--dry-run", action="store_true")
    ov.add_argument("--update", action="store_true",
                    help="replace orch entries that differ from the template (after the user confirmed the drift)")
    ci = sub.add_parser("ci", parents=[common, root], help="install the orch-gate CI job (monorepo)")
    ci.add_argument("--platform", action="append", choices=sorted(CI_TARGETS), required=True)
    ci.add_argument("--owners", help="CODEOWNERS owners for the suggested lines, e.g. @org/orch-owners")
    ci.add_argument("--dry-run", action="store_true")
    pr = sub.add_parser("protection", parents=[common, root], help="verify the platform settings the gate relies on")
    pr.add_argument("--platform", choices=sorted(PROTECTION), required=True)
    hk = sub.add_parser("hook", parents=[common], help="install the pre-push hook in this clone")
    hk.add_argument("--repo", default=".", help="repo whose clone gets the hook (default: cwd)")
    hk.add_argument("--coord", help="coordination repo checkout, for a polyrepo code repo")
    hk.add_argument("--dry-run", action="store_true")
    sub.add_parser("check", parents=[common, root], help="tools, stock anchors, ignored orch dirs")
    sub.add_parser("state", parents=[common, root], help="what setup already put in place")
    return p


COMMANDS = {"scan": cmd_scan, "write-registry": cmd_write_registry, "overrides": cmd_overrides, "ci": cmd_ci,
            "protection": cmd_protection, "hook": cmd_hook, "check": cmd_check, "state": cmd_state}


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
