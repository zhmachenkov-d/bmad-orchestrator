"""Module config for orch, read from the coordination repo's `_bmad` TOML layers with plan defaults.

Layers are read through a git ref (the coordination main or the gate's base), never the working tree:
a PR cannot move its own boundaries, and uncommitted or gitignored `*.user.toml` files never reach
the verdict, so local and CI runs see the same config. Paths come back relative to the coordination
repo root, POSIX style.
"""

from __future__ import annotations

import tomllib
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath

from . import OrchError
from .gitio import Tree, default_base, remote_head

LAYERS = ("_bmad/config.toml", "_bmad/config.user.toml", "_bmad/custom/config.toml", "_bmad/custom/config.user.toml")

DEFAULTS = {
    "orch_coordination_repo": ".",
    "orch_registry_dir": "_bmad-output/orch/subprojects",
    "orch_contracts_dir": "_bmad-output/orch/contracts",
    "orch_worktrees_dir": "../{project_name}-worktrees",
    "orch_stale_claim_hours": 48,
    "orch_review_wait_hours": 24,
    "orch_main_branch": "main",
    "planning_artifacts": "_bmad-output/planning-artifacts",
    "implementation_artifacts": "_bmad-output/implementation-artifacts",
    "project_name": "",
}


@dataclass
class Config:
    coordination_repo: str
    registry_dir: str
    contracts_dir: str
    worktrees_dir: str
    stale_claim_hours: float
    review_wait_hours: float
    main_branch: str
    planning_artifacts: str
    implementation_artifacts: str

    @property
    def closed_dir(self) -> str:
        """Epic close records live beside the registry: `<orch dir>/closed/epic-N.yaml`."""
        return str(PurePosixPath(self.registry_dir).parent / "closed")

    @property
    def sprint_status(self) -> str:
        return f"{self.implementation_artifacts}/sprint-status.yaml"

    @property
    def prd(self) -> str:
        return f"{self.planning_artifacts}/prd.md"

    def to_dict(self) -> dict:
        return {**asdict(self), "closed_dir": self.closed_dir, "sprint_status": self.sprint_status, "prd": self.prd}


def _rel(value: str, project_name: str) -> str:
    v = str(value).replace("{project_name}", project_name)
    for prefix in ("{project-root}/", "{project-root}"):
        if v.startswith(prefix):
            v = v[len(prefix):]
    return v.strip("/") if not v.startswith("..") else v.rstrip("/")


def resolve(coord_root: Path, explicit_ref: str | None = None) -> tuple[Config, str]:
    """Config plus the ref it was read at.

    With no explicit ref, bootstrap from the remote's default branch (origin/HEAD, else origin/main or
    main) and follow its `orch_main_branch` one hop. The PR head is never consulted, so a PR cannot
    redirect the gate by editing `orch_main_branch`.
    """
    if explicit_ref:
        return load(Tree(coord_root, explicit_ref)), explicit_ref
    ref = remote_head(coord_root) or default_base(coord_root, DEFAULTS["orch_main_branch"])
    cfg = load(Tree(coord_root, ref))
    if cfg.main_branch not in (ref, ref.removeprefix("origin/")):
        try:
            ref = default_base(coord_root, cfg.main_branch)
        except OrchError:
            return cfg, ref
        cfg = load(Tree(coord_root, ref))
    return cfg, ref


def load(tree: Tree) -> Config:
    merged: dict = {}
    for layer in LAYERS:
        raw = tree.read(layer)
        if raw is None:
            continue
        try:
            data = tomllib.loads(raw.decode("utf-8"))
        except (tomllib.TOMLDecodeError, UnicodeDecodeError) as exc:
            raise OrchError(f"{layer} at {tree.ref} is not valid TOML: {exc}") from exc
        merged.update(data.get("core", {}))
        modules = data.get("modules", {})
        merged.update(modules.get("bmm", {}))
        # Module config may store orch keys with or without the orch_ prefix.
        for key, value in modules.get("orch", {}).items():
            merged[key if key.startswith("orch_") else f"orch_{key}"] = value
    values = {k: merged.get(k, d) for k, d in DEFAULTS.items()}
    name = str(values["project_name"] or Path(tree.repo).resolve().name)
    return Config(
        coordination_repo=str(values["orch_coordination_repo"]),
        registry_dir=_rel(values["orch_registry_dir"], name),
        contracts_dir=_rel(values["orch_contracts_dir"], name),
        worktrees_dir=_rel(values["orch_worktrees_dir"], name),
        stale_claim_hours=float(values["orch_stale_claim_hours"]),
        review_wait_hours=float(values["orch_review_wait_hours"]),
        main_branch=str(values["orch_main_branch"]),
        planning_artifacts=_rel(values["planning_artifacts"], name),
        implementation_artifacts=_rel(values["implementation_artifacts"], name),
    )
