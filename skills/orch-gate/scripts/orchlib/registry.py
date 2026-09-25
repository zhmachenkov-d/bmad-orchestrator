"""Subproject registry: one YAML per subproject in the coordination repo, plus the implicit `contracts` pseudo-subproject."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import PurePosixPath

import yaml

from . import OrchError
from .config import Config
from .gitio import Tree, normalize_repo
from .globs import may_overlap

CONTRACTS = "contracts"
CONTRACT_TYPES = ("openapi", "protobuf", "asyncapi", "db-schema")
# What a canonical may be per type: a single spec file, a proto package directory, a migrations directory.
CANONICAL_KINDS = {"openapi": ("blob",), "asyncapi": ("blob",), "protobuf": ("blob", "tree"), "db-schema": ("tree",)}
# Things that do not exist *yet* (a new subproject's first story, a contract story adding its canonical).
# The gate warns on these; every other issue is structural and fails it.
EXISTENCE_ISSUES = ("path-missing", "canonical-missing")


@dataclass
class Export:
    type: str
    canonical: str
    copy: str | None = None
    extra: dict = field(default_factory=dict)


@dataclass
class Subproject:
    name: str
    repo: str
    path: str
    allowed_read: list[str]
    allowed_write: list[str]
    exports: list[Export] = field(default_factory=list)
    imports: list[str] = field(default_factory=list)
    branch: str = "main"
    source: str = ""

    def to_dict(self) -> dict:
        return {
            "name": self.name, "repo": self.repo, "path": self.path, "branch": self.branch,
            "allowed_read": self.allowed_read, "allowed_write": self.allowed_write,
            "exports": [{"type": e.type, "canonical": e.canonical, "copy": e.copy, **e.extra} for e in self.exports],
            "imports": self.imports, "source": self.source,
        }


class Registry(dict):
    """name -> Subproject; `issues` holds load-time problems."""

    issues: list[dict]

    def consumers(self, name: str) -> list[str]:
        return sorted(s.name for s in self.values() if name in s.imports)

    def canonical_owner(self, path: str) -> tuple[Subproject, Export] | None:
        for sub in self.values():
            for exp in sub.exports:
                c = exp.canonical.rstrip("/")
                if path == c or path.startswith(c + "/"):
                    return sub, exp
        return None

    def to_dict(self) -> dict:
        return {name: sub.to_dict() for name, sub in sorted(self.items())}


def _issue(code: str, message: str, subproject: str | None = None) -> dict:
    return {"code": code, "subproject": subproject, "message": message}


def _strings(value, key: str, name: str, issues: list) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, list) and all(isinstance(v, str) for v in value):
        return value
    issues.append(_issue("bad-field", f"'{key}' must be a list of strings", name))
    return []


def contracts_pseudo(cfg: Config) -> Subproject:
    return Subproject(
        name=CONTRACTS, repo=".", path=cfg.contracts_dir,
        allowed_read=[f"{cfg.contracts_dir}/**", f"{cfg.registry_dir}/**", f"{cfg.planning_artifacts}/**"],
        allowed_write=[f"{cfg.contracts_dir}/**"], source="(implicit)",
    )


def _entry(data: dict, path: str, name: str, cfg: Config, issues: list) -> Subproject | None:
    """One registry entry, or None when a required field is missing, blank or the wrong shape.

    Never fill a boundary with a permissive default: a dropped entry leaves an issue that fails the gate.
    """
    before = len(issues)
    for req in ("repo", "path"):
        if not isinstance(data.get(req), str) or not data[req].strip():
            issues.append(_issue("missing-field", f"{path}: '{req}' must be a non-empty string", name))
    allowed_write = _strings(data.get("allowed_write"), "allowed_write", name, issues)
    if not allowed_write or not all(p.strip() for p in allowed_write):
        issues.append(_issue("missing-field", f"{path}: 'allowed_write' must list at least one non-empty pattern", name))
    if not isinstance(data.get("branch", ""), str):
        issues.append(_issue("bad-field", f"{path}: 'branch' must be a string", name))
    contracts = data.get("contracts")
    contracts = {} if contracts is None else contracts
    if not isinstance(contracts, dict):
        issues.append(_issue("bad-field", f"{path}: 'contracts' must be a mapping with exports and imports", name))
        contracts = {}
    raw_exports = contracts.get("exports")
    raw_exports = [] if raw_exports is None else raw_exports
    if not isinstance(raw_exports, list):
        issues.append(_issue("bad-field", f"{path}: 'contracts.exports' must be a list", name))
        raw_exports = []
    exports = []
    for i, raw in enumerate(raw_exports):
        if not (isinstance(raw, dict) and isinstance(raw.get("type"), str) and isinstance(raw.get("canonical"), str)
                and raw["canonical"].strip("/") and isinstance(raw.get("copy") or "", str)):
            issues.append(_issue("bad-export", f"{path}: exports[{i}] needs string 'type' and 'canonical' (and 'copy', if given)", name))
            continue
        extra = {k: v for k, v in raw.items() if k not in ("type", "canonical", "copy")}
        exports.append(Export(raw["type"], raw["canonical"].strip("/"), raw.get("copy") or None, extra))
    imports = _strings(contracts.get("imports"), "contracts.imports", name, issues)
    allowed_read = _strings(data.get("allowed_read"), "allowed_read", name, issues)
    if len(issues) > before:
        return None
    return Subproject(name=name, repo=data["repo"].strip(), path=data["path"].strip().strip("/"),
                      allowed_read=allowed_read, allowed_write=allowed_write, exports=exports, imports=imports,
                      branch=data.get("branch") or cfg.main_branch, source=path)


def load(tree: Tree, cfg: Config) -> Registry:
    """Total over its input: any file content yields issues, never an exception, so a repair PR can always run."""
    reg = Registry()
    reg.issues = []
    for path in sorted(tree.list(cfg.registry_dir)):
        if not path.endswith((".yaml", ".yml")):
            continue
        stem = PurePosixPath(path).stem
        try:
            data = yaml.safe_load(tree.text(path) or "") or {}
        except (yaml.YAMLError, OrchError) as exc:
            reg.issues.append(_issue("yaml", f"{path}: {exc}", stem))
            continue
        if not isinstance(data, dict):
            reg.issues.append(_issue("yaml", f"{path}: top level must be a mapping", stem))
            continue
        name = str(data.get("name") or stem)
        if name != stem:
            reg.issues.append(_issue("name-mismatch", f"{path}: name '{name}' differs from file name '{stem}'", name))
        if name == CONTRACTS:
            reg.issues.append(_issue("reserved-name", f"{path}: '{CONTRACTS}' is the reserved pseudo-subproject", name))
            continue
        if (sub := _entry(data, path, name, cfg, reg.issues)) is not None:
            reg[name] = sub
    reg[CONTRACTS] = contracts_pseudo(cfg)
    return reg


def validate(reg: Registry, coord: Tree, cfg: Config) -> list[dict]:
    """Structural checks; paths are only verified for subprojects living in the coordination repo."""
    issues = list(reg.issues)
    real = {n: s for n, s in reg.items() if n != CONTRACTS}
    for sub in real.values():
        if sub.repo == "." and sub.path and not coord.exists(sub.path):
            issues.append(_issue("path-missing", f"path '{sub.path}' does not exist in the coordination repo", sub.name))
        for pattern in sub.allowed_write:
            if may_overlap(pattern, f"{cfg.contracts_dir}/**") and sub.repo == ".":
                issues.append(_issue("writes-contracts", f"allowed_write '{pattern}' reaches the contracts dir; contract changes go through contract stories", sub.name))
        for imp in sub.imports:
            if imp == sub.name:
                issues.append(_issue("self-import", "imports itself", sub.name))
            elif imp not in real:
                issues.append(_issue("unknown-import", f"imports unknown subproject '{imp}'", sub.name))
        for exp in sub.exports:
            if exp.type not in CONTRACT_TYPES:
                issues.append(_issue("unknown-contract-type", f"export type '{exp.type}' not one of {', '.join(CONTRACT_TYPES)}", sub.name))
            expected = f"{cfg.contracts_dir}/{sub.name}/"
            kind = coord.kind(exp.canonical)
            if not exp.canonical.startswith(expected):
                issues.append(_issue("canonical-location", f"canonical '{exp.canonical}' must live under '{expected}'", sub.name))
            elif kind is None:
                issues.append(_issue("canonical-missing", f"canonical '{exp.canonical}' not found in the coordination repo", sub.name))
            elif exp.type in CANONICAL_KINDS and kind not in CANONICAL_KINDS[exp.type]:
                want = " or ".join("a file" if k == "blob" else "a directory" for k in CANONICAL_KINDS[exp.type])
                issues.append(_issue("canonical-kind", f"{exp.type} canonical '{exp.canonical}' must be {want}", sub.name))
    names = sorted(real)
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            sa, sb = real[a], real[b]
            if normalize_repo(sa.repo) != normalize_repo(sb.repo):
                continue
            for pa in sa.allowed_write:
                for pb in sb.allowed_write:
                    if may_overlap(pa, pb):
                        issues.append(_issue("write-overlap", f"allowed_write '{pa}' overlaps '{pb}' of '{b}'", a))
    issues += [_issue("import-cycle", "import cycle: " + " -> ".join(c)) for c in _cycles(real)]
    return issues


def _cycles(real: dict[str, Subproject]) -> list[list[str]]:
    found, state, stack = [], {}, []

    def visit(n: str):
        state[n] = 1
        stack.append(n)
        for m in real[n].imports:
            if m not in real:
                continue
            if state.get(m) == 1:
                found.append(stack[stack.index(m):] + [m])
            elif m not in state:
                visit(m)
        stack.pop()
        state[n] = 2

    for n in sorted(real):
        if n not in state:
            visit(n)
    return found


def subprojects_in(reg: Registry, repo_id: str) -> list[Subproject]:
    """Real subprojects whose `repo` is this repo ('.' = the coordination repo, else a normalized URL)."""
    return [s for n, s in sorted(reg.items()) if n != CONTRACTS
            and ("." if s.repo == "." else normalize_repo(s.repo)) == repo_id]


def pin_paths(reg: Registry, subproject: str) -> list[str]:
    """Canonical contracts a story in `subproject` must pin: its own exports plus everything it imports."""
    sub = reg[subproject]
    paths = [e.canonical for e in sub.exports]
    for imp in sub.imports:
        if imp in reg:
            paths += [e.canonical for e in reg[imp].exports]
    return sorted(set(paths))
