"""Story merge markers (`.orch/stories/<key>.yaml`), contract pins, and pull-based merge status."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path, PurePosixPath

import yaml

from . import OrchError
from .config import Config
from .gitio import Tree, fetch_tree, normalize_repo, sha
from .registry import CONTRACTS, Registry, pin_paths
from .stories import KEY_RE, Story

MARKER_DIR = ".orch/stories"
ARCHIVE_DIR = ".orch/archive"
CACHE_DIR = ".orch/cache"
ARCHIVE_RE = re.compile(rf"^{re.escape(ARCHIVE_DIR)}/epic-(\d+)/(\d+-\d+[a-z]?)\.yaml$")
MARKER_RE = re.compile(rf"^{re.escape(MARKER_DIR)}/([^/]+)\.yaml$")


def marker_path(key: str) -> str:
    return f"{MARKER_DIR}/{key}.yaml"


def archive_path(epic: int, key: str) -> str:
    return f"{ARCHIVE_DIR}/epic-{epic}/{key}.yaml"


def parse(data: bytes | str, key: str) -> tuple[dict | None, list[str]]:
    """Parse and schema-check a marker; returns (marker, problems)."""
    try:
        m = yaml.safe_load(data) or {}
    except yaml.YAMLError as exc:
        return None, [f"marker is not valid YAML: {exc}"]
    if not isinstance(m, dict):
        return None, ["marker must be a mapping"]
    problems = []
    if str(m.get("story", "")) != key:
        problems.append(f"'story' must be '{key}' (file name), got '{m.get('story')}'")
    if not isinstance(m.get("epic"), int):
        problems.append("'epic' must be an integer")
    pins = m.get("contract_pins", {})
    if pins is None:
        pins = m["contract_pins"] = {}
    if not isinstance(pins, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in pins.items()):
        problems.append("'contract_pins' must map canonical paths to git object shas")
    if "prd_pin" in m and m["prd_pin"] is not None and not isinstance(m["prd_pin"], str):
        problems.append("'prd_pin' must be a git blob sha")
    return m, problems


def compute_pins(reg: Registry, story: Story, coord: Tree, cfg: Config,
                 head: Tree | None = None, changed: list[str] | None = None) -> dict:
    """Pins a marker for `story` must carry.

    Implementation stories pin what they build against: canonical contracts on the coordination main.
    Contract stories pin what they produce: each canonical they change, at the story's committed `head`.
    """
    if story.subproject == CONTRACTS:
        touched = sorted({owned[1].canonical for p in (changed or []) if (owned := reg.canonical_owner(p))})
        pins = {p: (head or coord).blob_sha(p) for p in touched}
    elif story.subproject in reg:
        pins = {p: coord.blob_sha(p) for p in pin_paths(reg, story.subproject)}
    else:
        pins = {}
    return {
        "story": story.key,
        "epic": story.epic,
        "contract_pins": {p: s for p, s in pins.items() if s},
        "prd_pin": coord.blob_sha(cfg.prd),
    }


def render(marker: dict) -> str:
    body = {k: marker[k] for k in ("story", "epic", "contract_pins", "prd_pin") if marker.get(k) is not None}
    return yaml.safe_dump(body, sort_keys=False)


def write(repo_root: Path, marker: dict) -> Path:
    path = Path(repo_root) / marker_path(marker["story"])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render(marker), encoding="utf-8")
    return path


def keys_in(tree: Tree) -> dict[str, str]:
    """Story keys that have a marker in this tree (live or archived) -> marker path."""
    found = {}
    for p in tree.list(".orch"):
        m = MARKER_RE.match(p)
        if m and KEY_RE.match(m.group(1)):
            found[m.group(1)] = p
            continue
        a = ARCHIVE_RE.match(p)
        if a:
            found.setdefault(a.group(2), p)
    return found


def repo_tree(repo: str, branch: str, coord: Tree, cache_root: Path, local: dict[str, Tree] | None = None) -> Tree:
    """Tree at the main branch of a registry repo: the coordination tree for '.', a cached shallow fetch otherwise."""
    if repo == ".":
        return coord
    ident = normalize_repo(repo)
    if local and ident in local:
        return local[ident]
    return fetch_tree(repo, branch, cache_root / "repos")


def merged(reg: Registry, coord: Tree, cache_root: Path, local: dict[str, Tree] | None = None,
           offline: bool = False, read: list | None = None) -> tuple[dict[str, dict], list[dict]]:
    """Merged story keys across every registry repo -> {repo, path, tree}; plus the repos that could not be read.

    An unread repo means "merge status unknown", never "not merged": callers must report it as such.
    `read`, if given, collects {repo, branch, sha} for every other repo that was read, so a verdict records them.
    """
    result, unread, seen = {}, [], {}
    for sub in sorted(reg.values(), key=lambda s: s.name):
        ident = normalize_repo(sub.repo)
        if ident in seen:
            seen[ident].append(sub.name)
            continue
        seen[ident] = [sub.name]
        if offline and ident != "." and not (local and ident in local):
            unread.append({"code": "repo-offline", "repo": sub.repo, "subprojects": seen[ident],
                           "message": f"offline: merge status of {sub.repo} not checked"})
            continue
        try:
            tree = repo_tree(sub.repo, sub.branch, coord, cache_root, local)
        except (OrchError, subprocess.CalledProcessError, OSError) as exc:
            unread.append({"code": "repo-unreadable", "repo": sub.repo, "subprojects": seen[ident],
                           "message": f"cannot read {sub.repo} (check network and read access for this runner): {exc}"})
            continue
        if read is not None and ident != ".":
            read.append({"repo": sub.repo, "branch": sub.branch, "sha": sha(tree.repo, tree.ref)})
        for key, path in keys_in(tree).items():
            result.setdefault(key, {"repo": sub.repo, "path": path, "tree": tree})
    return result, unread


def unknown_keys(stories, unread: list[dict]) -> set[str]:
    """Story keys whose merge status is unknown because their subproject's repo was not read."""
    subs = {s for u in unread for s in u["subprojects"]}
    return {s.key for s in stories.values() if s.subproject in subs}


def public(merged_map: dict[str, dict]) -> dict[str, dict]:
    return {k: {"repo": v["repo"], "path": v["path"]} for k, v in sorted(merged_map.items())}


def close_check(epic: int, stories, reg: Registry, merged_map: dict[str, dict], coord: Tree,
                unknown: set[str] = frozenset()) -> list[dict]:
    """An epic may close only when every story is merged, every marker is archived, and every pin matches main."""
    problems = []
    for s in [s for s in stories.values() if s.epic == epic]:
        hit = merged_map.get(s.key)
        if not hit and s.key in unknown:
            problems.append({"story": s.key, "code": "unverifiable",
                             "message": f"cannot verify story {s.id}: the {s.subproject} repo was not read"})
            continue
        if not hit:
            problems.append({"story": s.key, "message": f"story {s.id} is not merged"})
            continue
        archived = ARCHIVE_RE.match(hit["path"])
        if not archived or int(archived.group(1)) != epic:
            problems.append({"story": s.key, "message": f"marker {hit['path']} in {hit['repo']} is not archived under {ARCHIVE_DIR}/epic-{epic}/"})
        marker, errs = parse(hit["tree"].read(hit["path"]) or b"", s.key)
        if errs or marker is None:
            problems.append({"story": s.key, "message": f"marker {hit['path']}: {'; '.join(errs)}"})
            continue
        for path, pinned in marker.get("contract_pins", {}).items():
            current = coord.blob_sha(path)
            if current != pinned:
                problems.append({"story": s.key, "message": f"pin {path} = {pinned[:10]} but main has {(current or 'nothing')[:10]} (pins not converged)"})
    return problems


def is_archive_move(changes: list[dict], tree_head: Tree, tree_base: Tree) -> tuple[set[str], list[str]]:
    """Paths that are part of a pure archive move (marker deleted, identical file added under the archive)."""
    deleted = {c["path"]: c for c in changes if c["status"] == "D" and MARKER_RE.match(c["path"])}
    added = {c["path"] for c in changes if c["status"] == "A" and ARCHIVE_RE.match(c["path"])}
    ok, problems = set(), []
    for path in added:
        epic, key = ARCHIVE_RE.match(path).groups()
        src = marker_path(key)
        story_epic = int(key.split("-")[0])
        if int(epic) != story_epic:
            problems.append(f"{path}: story {key} belongs to epic {story_epic}; archive it under {ARCHIVE_DIR}/epic-{story_epic}/")
        elif src in deleted and tree_base.read(src) == tree_head.read(path):
            ok |= {src, path}
        else:
            problems.append(f"{path}: archive entry must be an unchanged move of {src}")
    for path in deleted:
        if path not in ok:
            problems.append(f"{path}: markers may only be removed by moving them to {ARCHIVE_DIR}/epic-N/")
    return ok, problems


def closed_epics(coord: Tree, cfg: Config) -> set[int]:
    out = set()
    for p in coord.list(cfg.closed_dir):
        m = re.match(r"^epic-(\d+)\.ya?ml$", PurePosixPath(p).name)
        if m:
            out.add(int(m.group(1)))
    return out
