"""What to work on next, and the worktree and node context a claimed story is built in.

`pick` turns a status snapshot into the choice orch-next offers: warnings first, the caller's own open claims,
then the ready stories ranked by what they unblock. `prepare` puts a claimed story's branch `story/<key>` in a
worktree of the story's repo; `context` is what bmad-build may read and write there, written to `.orch/context/`,
which the clone's info/exclude keeps out of every commit.
"""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path, PurePosixPath

from . import OrchError
from .config import Config
from .gitio import Tree, default_base, git, normalize_repo, out, ref_exists, remote_url, sha
from .markers import compute_pins, marker_path
from .registry import CONTRACTS, Registry, Subproject, pin_paths
from .status import story_branch
from .stories import EPIC_RE, FENCE_RE, STORY_RE, Story, StorySet

CONTEXT_DIR = ".orch/context"
CONTEXT_MD = f"{CONTEXT_DIR}/node-context.md"
CONTEXT_JSON = f"{CONTEXT_DIR}/node-context.json"
EXCLUDE = f"/{CONTEXT_DIR}/"
OPEN = ("in-progress", "review")


# ---- next: the choice ----

def _email(user: str | None) -> str | None:
    m = re.search(r"<([^>]+)>", user or "")
    return m.group(1).strip().lower() if m else None


def same_user(a: str | None, b: str | None) -> bool:
    """Same person: equal emails when both carry one, else equal strings."""
    if not a or not b:
        return False
    ea, eb = _email(a), _email(b)
    return ea == eb if ea and eb else a.strip() == b.strip()


def pick(snapshot: dict, stories: StorySet, reg: Registry, user: str) -> dict:
    """Warnings, the user's open stories and the ranked ready list from a `status.build` snapshot.

    Rank: on an epic's critical path first, then most stories downstream, then plan order. A ready story the plan
    does not define cleanly is held back: the gate would fail its marker.
    """
    rows = snapshot["stories"]
    by_key = {r["key"]: r for r in rows}
    order = {k: i for i, k in enumerate(stories)}
    flawed = {}
    for issue in stories.issues:
        if issue.get("story"):
            flawed.setdefault(issue["story"].replace(".", "-"), []).append(issue)

    ready, held = [], []
    for r in rows:
        if r["state"] != "ready":
            continue
        s = stories[r["key"]]
        # dependents for which this is the last unmerged dependency become ready once it merges
        unblocks = [d for d in r["dependents"] if by_key.get(d, {}).get("blocked_by") == [r["key"]]]
        entry = {"key": r["key"], "id": r["id"], "epic": r["epic"], "title": r["title"], "subproject": r["subproject"],
                 "repo": _repo_of(reg, s), "contract_change": s.contract_change, "critical": r["critical"],
                 "downstream": r["downstream"], "unblocks": unblocks}
        if r["key"] in flawed:
            held.append({**entry, "plan_issues": flawed[r["key"]]})
        else:
            ready.append(entry)
    ready.sort(key=lambda e: (not e["critical"], -e["downstream"], order[e["key"]]))
    for rank, e in enumerate(ready, 1):
        e["rank"] = rank

    mine = [{"key": r["key"], "id": r["id"], "title": r["title"], "subproject": r["subproject"], "state": r["state"],
             "repo": _repo_of(reg, stories[r["key"]]), "claim_sha": r["claim_sha"], "branch": r["branch"],
             "idle_hours": r["idle_hours"]}
            for r in rows if r["state"] in OPEN and same_user(r["claimant"], user)]
    severity = {"warn": 0, "info": 1}
    warnings = sorted(snapshot["anomalies"], key=lambda a: severity.get(a.get("severity"), 2))
    counts = {st: sum(r["state"] == st for r in rows) for st in ("done", "review", "in-progress", "ready", "blocked", "unknown")}
    res = {"user": user, "warnings": warnings, "mine": mine, "ready": ready, "held": held, "counts": counts,
           "unread_repos": snapshot["unread_repos"], "notices": snapshot["notices"]}
    if not ready:
        # nothing to offer: say what the work waits for, the open stories first since they unblock the rest
        res["waiting"] = [{"key": r["key"], "id": r["id"], "title": r["title"], "state": r["state"],
                           "blocked_by": r["blocked_by"], "claimant": r["claimant"], "idle_hours": r["idle_hours"],
                           "review": (r["review"] or {}).get("url")}
                          for state in ("review", "in-progress", "blocked") for r in rows
                          if r["state"] == state and not same_user(r["claimant"], user)]
    return res


def _repo_of(reg: Registry, s: Story) -> str | None:
    sub = reg.get(s.subproject) if s.subproject else None
    return sub.repo if sub else None


# ---- worktree ----

def story_subproject(reg: Registry, stories: StorySet, key: str) -> tuple[Story, Subproject]:
    s = stories.get(key)
    if s is None:
        raise OrchError(f"story {key} not found in the epics", "story-not-found")
    if not s.subproject or s.subproject not in reg:
        raise OrchError(f"story {s.id} has no registered subproject ({s.subproject or 'none'}); fix the plan first", "story-unregistered")
    return s, reg[s.subproject]


def local_clone(sub: Subproject, repo: Path, coord_root: Path) -> Path | None:
    """The checkout here that is the story's repo: the coordination repo for '.', else --repo when it matches."""
    if sub.repo == ".":
        return coord_root
    want = normalize_repo(sub.repo)
    for candidate in (repo, coord_root):
        if want in (normalize_repo(remote_url(candidate) or ""), normalize_repo(str(candidate))):
            return candidate
    return None


def worktrees(repo: Path) -> dict[str, Path]:
    """branch name -> worktree path, for every worktree of this clone."""
    found, path = {}, None
    for line in out(repo, "worktree", "list", "--porcelain").splitlines():
        if line.startswith("worktree "):
            path = Path(line[len("worktree "):])
        elif line.startswith("branch refs/heads/") and path is not None:
            found[line[len("branch refs/heads/"):]] = path
    return found


def default_path(coord_root: Path, worktrees_dir: str, key: str) -> Path:
    base = Path(worktrees_dir).expanduser()
    return (base if base.is_absolute() else coord_root / base).resolve() / f"story-{key}"


def prepare(repo: Path, key: str, main_branch: str, path: Path) -> dict:
    """Check out `story/<key>` in a worktree at `path`, reusing whatever branch or worktree already exists.

    Order: an existing worktree of the branch; a local branch; the pushed `origin/story/<key>` (a take-over
    continues there); else a new branch from the subproject's main. A new branch does not track main, so its
    first push creates `story/<key>` on the remote.
    """
    branch = story_branch(key)
    existing = worktrees(repo).get(branch)
    if existing is not None:
        return {"status": "exists", "path": str(existing), "branch": branch}
    if path.exists() and any(path.iterdir()):
        raise OrchError(f"{path} exists and is not empty; pass --path <empty dir> for the worktree", "path-not-empty", path=str(path))
    path.parent.mkdir(parents=True, exist_ok=True)
    remote_branch = f"origin/{branch}"
    if ref_exists(repo, f"refs/heads/{branch}"):
        git(repo, "worktree", "add", "--quiet", str(path), branch)
        status, start = "branch-reused", branch
    elif ref_exists(repo, f"refs/remotes/{remote_branch}"):
        git(repo, "worktree", "add", "--quiet", "--track", "-b", branch, str(path), remote_branch)
        status, start = "from-remote", remote_branch
    else:
        start = default_base(repo, main_branch)
        git(repo, "worktree", "add", "--quiet", "--no-track", "-b", branch, str(path), start)
        status = "created"
    exclude_context(repo)
    return {"status": status, "path": str(path.resolve()), "branch": branch, "start": start, "start_sha": sha(repo, start)}


def exclude_context(repo: Path) -> None:
    """Keep `.orch/context/` out of commits in every worktree of the clone (info/exclude is shared)."""
    common = Path(out(repo, "rev-parse", "--git-common-dir"))
    info = (common if common.is_absolute() else Path(repo) / common) / "info"
    info.mkdir(parents=True, exist_ok=True)
    exclude = info / "exclude"
    text = exclude.read_text(encoding="utf-8") if exclude.exists() else ""
    if EXCLUDE not in text.splitlines():
        exclude.write_text(text + ("" if not text or text.endswith("\n") else "\n") + EXCLUDE + "\n", encoding="utf-8")


# ---- node context ----

def story_text(coord: Tree, s: Story) -> str:
    """The story's section of its epics file: heading through the line before the next heading at its level or above."""
    lines = (coord.text(s.source) or "").splitlines()
    if not 0 < s.line <= len(lines):
        return ""
    level = len(lines[s.line - 1]) - len(lines[s.line - 1].lstrip("#"))
    body, fence = [lines[s.line - 1]], False
    for line in lines[s.line:]:
        if FENCE_RE.match(line):
            fence = not fence
        elif not fence and (STORY_RE.match(line) or EPIC_RE.match(line)
                            or (line.startswith("#") and len(line) - len(line.lstrip("#")) <= level)):
            break
        body.append(line)
    return "\n".join(body).rstrip() + "\n"


def context(reg: Registry, stories: StorySet, key: str, coord: Tree, cfg: Config, coord_sha: str) -> dict:
    s, sub = story_subproject(reg, stories, key)
    if s.subproject == CONTRACTS:
        canonicals = [{"path": e.canonical, "type": e.type, "owner": owner.name, "role": "canonical",
                       "sha": coord.blob_sha(e.canonical), "consumers": reg.consumers(owner.name)}
                      for owner in sorted(reg.values(), key=lambda x: x.name) for e in owner.exports]
    else:
        own = {e.canonical for e in sub.exports}
        canonicals = []
        for path in pin_paths(reg, s.subproject):
            owned = reg.canonical_owner(path)
            exp = owned[1] if owned else None
            canonicals.append({"path": path, "type": exp.type if exp else None, "owner": owned[0].name if owned else None,
                               "role": "export" if path in own else "import", "copy": exp.copy if path in own and exp else None,
                               "sha": coord.blob_sha(path), "consumers": reg.consumers(owned[0].name) if owned else []})
    deps = [{"id": d, "title": x.title if (x := stories.by_id(d)) else None} for d in s.depends_on]
    issues = [i for i in stories.issues if i.get("story") == s.id]
    return {
        "story": {"key": s.key, "id": s.id, "epic": s.epic, "title": s.title, "subproject": s.subproject,
                  "depends_on": deps, "contract_change": s.contract_change, "source": f"{s.source}:{s.line}",
                  "text": story_text(coord, s)},
        "subproject": {"name": sub.name, "repo": sub.repo, "path": sub.path, "branch": sub.branch,
                       "allowed_read": sub.allowed_read, "allowed_write": sub.allowed_write, "imports": sub.imports},
        "branch": story_branch(s.key), "base_branch": sub.branch, "marker": marker_path(s.key),
        "contracts": canonicals,
        "pins": compute_pins(reg, s, coord, cfg) if s.subproject != CONTRACTS else None,
        "prd": {"path": cfg.prd, "sha": coord.blob_sha(cfg.prd)},
        "coord_ref": coord.ref, "coord_sha": coord_sha, "plan_issues": issues,
    }


def render(ctx: dict, snapshots: dict[str, str]) -> str:
    """The node context as bmad-build reads it: the story, the boundaries, the contracts, and how to finish."""
    s, sub = ctx["story"], ctx["subproject"]
    contract = s["subproject"] == CONTRACTS
    lines = [f"# Node context: story {s['id']} ({s['key']})", "",
             f"Generated by orch from the coordination repo at `{ctx['coord_ref']}` ({ctx['coord_sha'][:10]}). "
             "Regenerate it rather than editing it.", "",
             "## Story", "", f"- Subproject: `{sub['name']}` in repo `{sub['repo']}`, path `{sub['path']}`",
             f"- Branch: `{ctx['branch']}`, PR into `{ctx['base_branch']}`",
             "- Depends on: " + (", ".join(f"{d['id']} {d['title'] or ''}".strip() for d in s["depends_on"]) or "none"),
             f"- Contract change: `{s['contract_change']}`", f"- Defined at: `{s['source']}`", "", s["text"].rstrip(), ""]
    if ctx["plan_issues"]:
        lines += ["**Plan issues (the gate will fail this story until the plan is fixed):**", ""]
        lines += [f"- {i['message']}" for i in ctx["plan_issues"]] + [""]
    lines += ["## Boundaries", "",
              "Read only what the story needs, within these paths and the contracts below. This keeps context small; "
              "it is not a security boundary.", "", "Read:"]
    lines += [f"- `{p}`" for p in sub["allowed_read"] or ["(not set: the write paths)"]]
    lines += ["", "Write, and the gate rejects any other path:"] + [f"- `{p}`" for p in sub["allowed_write"]]
    lines += [f"- `{ctx['marker']}` (the story marker)", ""]
    if contract:
        lines += ["## Contracts", "",
                  "This is a contract story: change canonical contracts only. Services build against them in their "
                  "own stories.", ""]
        for c in ctx["contracts"]:
            lines.append(f"- `{c['path']}` ({c['type']}), owner `{c['owner']}`, consumers: "
                         f"{', '.join(c['consumers']) or 'none'}")
        rule = {"none": "No contract change is planned: any breaking change fails the gate.",
                "expand": "Expand only: add, never remove or tighten. A breaking change fails the gate.",
                "narrow": "Narrow: a breaking change passes only when this story depends on a story of every "
                          "consumer of the contract and those are merged."}[s["contract_change"]]
        lines += ["", rule, ""]
    else:
        lines += ["## Contracts", "", "Build against these canonical versions. Do not change a canonical here; a "
                  "contract error goes back to a contract story.", ""]
        for c in ctx["contracts"]:
            where = snapshots.get(c["path"])
            lines.append(f"- `{c['path']}` ({c['type']}, {c['role']} of `{c['owner']}`), pinned at `{(c['sha'] or 'missing')[:10]}`"
                         + (f", snapshot `{where}`" if where else "")
                         + (f"; keep the copy `{c['copy']}` byte-equal to it" if c.get("copy") else ""))
        if not ctx["contracts"]:
            lines.append("- none: this subproject neither exports nor imports a contract")
        lines.append("")
    lines += ["## Finish", "",
              "1. Commit the story's changes on the story branch.",
              f"2. Write the marker: `orch.py marker write --story {s['key']}`, then commit `{ctx['marker']}`.",
              f"3. Run `orch.py gate --format text` and fix what it reports.",
              f"4. Push `{ctx['branch']}` and open a PR into `{ctx['base_branch']}`.", ""]
    return "\n".join(lines)


def write(worktree: Path, ctx: dict, coord: Tree, snapshot: bool) -> dict:
    """Write node-context.md/.json into the worktree; with `snapshot`, copy the canonicals beside them."""
    root = Path(worktree)
    snapshots = {}
    shutil.rmtree(root / CONTEXT_DIR / "contracts", ignore_errors=True)  # a canonical dropped since the last write
    if snapshot:
        for c in ctx["contracts"]:
            for rel, data in coord.files(c["path"]).items():
                dest = PurePosixPath(CONTEXT_DIR) / "contracts" / c["path"]
                if coord.kind(c["path"]) == "tree":
                    dest = dest / rel
                (root / dest).parent.mkdir(parents=True, exist_ok=True)
                (root / dest).write_bytes(data)
            snapshots[c["path"]] = str(PurePosixPath(CONTEXT_DIR) / "contracts" / c["path"])
    (root / CONTEXT_DIR).mkdir(parents=True, exist_ok=True)
    (root / CONTEXT_MD).write_text(render(ctx, snapshots), encoding="utf-8")
    (root / CONTEXT_JSON).write_text(json.dumps({**ctx, "snapshots": snapshots}, indent=2) + "\n", encoding="utf-8")
    return {"markdown": str(root / CONTEXT_MD), "json": str(root / CONTEXT_JSON), "snapshots": snapshots}
