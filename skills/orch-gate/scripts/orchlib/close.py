"""Close an epic in two reviewable passes, never by committing to main.

archive: every repo holding live markers of the epic gets branch `orch/close-epic-N`, moving each marker
         unchanged to `.orch/archive/epic-N/`. The gate accepts exactly that move.
record:  once those are merged, the close check passes and every registry repo was read, the coordination repo gets branch
         `orch/close-epic-N-record` adding `<orch dir>/closed/epic-N.yaml`, sprint status with `epic-N: done`,
         and the retro data file `{implementation_artifacts}/orch/reports/orch-epic-N.json`.

Branches are built with git plumbing on top of each repo's main (the shared fetch cache for other repos), so no
checkout is touched. Without --push nothing is written: the plan is returned for the user to confirm.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path

import yaml

from . import OrchError
from . import markers, sprint_status
from .config import Config
from .gitio import Tree, git, normalize_repo, out, ref_exists, remote_url, sha
from .registry import Registry
from .status import critical_path, iso


def branch_for(epic: int, record: bool = False) -> str:
    return f"orch/close-epic-{epic}" + ("-record" if record else "")


def plan(epic: int, stories, reg: Registry, coord: Tree, cfg: Config, merged_map: dict, unread: list[dict]) -> dict:
    mine = [s for s in stories.values() if s.epic == epic]
    if not mine:
        raise OrchError(f"epic {epic} has no stories in the epics on {coord.ref}")
    base = {"epic": epic, "stories": [s.key for s in mine]}
    if epic in markers.closed_epics(coord, cfg):
        return {**base, "pass": "closed", "message": f"epic {epic} is already closed"}
    unknown = markers.unknown_keys(stories, unread)
    blocked = [{"story": s.key, "message": f"story {s.id} is not merged" if s.key not in unknown
                else f"cannot verify story {s.id}: the {s.subproject} repo was not read"}
               for s in mine if s.key not in merged_map]
    if blocked:
        return {**base, "pass": "blocked", "problems": blocked, "unread_repos": unread}
    repos: dict[str, dict] = {}
    for s in mine:
        hit = merged_map[s.key]
        if markers.MARKER_RE.match(hit["path"]):
            r = repos.setdefault(normalize_repo(hit["repo"]), {"repo": hit["repo"], "tree": hit["tree"],
                                                               "branch": branch_for(epic), "moves": []})
            r["moves"].append({"from": hit["path"], "to": markers.archive_path(epic, s.key)})
    if repos:
        return {**base, "pass": "archive", "repos": [{k: v for k, v in r.items() if k != "tree"} for r in repos.values()],
                "_trees": {k: r["tree"] for k, r in repos.items()}}
    problems = markers.close_check(epic, stories, reg, merged_map, coord, unknown)
    if problems:
        return {**base, "pass": "blocked", "problems": problems}
    if unread:
        # the record pass rewrites sprint status, which would demote the stories of repos it could not read
        return {**base, "pass": "blocked", "unread_repos": unread,
                "problems": [{"story": None, "code": "repos-unread", "message": u["message"]} for u in unread]}
    return {**base, "pass": "record", "repo": ".", "branch": branch_for(epic, record=True),
            "files": [record_path(cfg, epic), cfg.sprint_status, retro_path(cfg, epic)]}


def record_path(cfg: Config, epic: int) -> str:
    return f"{cfg.closed_dir}/epic-{epic}.yaml"


def retro_path(cfg: Config, epic: int) -> str:
    return f"{cfg.implementation_artifacts}/orch/reports/orch-epic-{epic}.json"


def claim_history(repo: Path, claim_sha: str | None) -> list[dict]:
    """Claim commits from newest to oldest: [{action, user, at}]."""
    if not claim_sha:
        return []
    raw = out(repo, "log", "--format=%ct%x00%B%x01", claim_sha)
    hist = []
    for rec in [r.strip() for r in raw.split("\x01") if r.strip()]:
        ts, body = rec.split("\0", 1)
        try:
            meta = json.loads(body.strip())
        except json.JSONDecodeError:
            continue
        hist.append({"action": meta.get("action"), "user": meta.get("user"), "at": iso(int(ts))})
    return hist


def retro_data(epic: int, stories, status: dict, coord_root: Path) -> dict:
    rows = [r for r in status["stories"] if r["epic"] == epic]
    out_rows = []
    for r in rows:
        hist = claim_history(coord_root, r["claim_sha"])
        out_rows.append({"story": r["key"], "title": r["title"], "subproject": r["subproject"],
                         "claims": hist, "take_overs": sum(h["action"] == "take-over" for h in hist),
                         "downstream": r["downstream"]})
    contract = sorted((r for r in rows if r["subproject"] == "contracts"), key=lambda r: -r["downstream"])
    return {
        "epic": epic, "generated_at": status["now"],
        "planned_critical_path": critical_path(stories, epic, set()),
        "stories": out_rows,
        "contract_stories_by_downstream": [{"story": r["key"], "downstream": r["downstream"], "dependents": r["dependents"]}
                                           for r in contract],
        "anomalies_at_close": [a for a in status["anomalies"] if a.get("story") in {r["key"] for r in rows}],
        "notes": "claims of released stories are gone, so claim history covers only claims still present; "
                 "review waits come from the review host at close time",
    }


def _identity(user: str) -> dict:
    name, _, email = user.partition("<")
    name, email = name.strip() or "orch", email.rstrip(">").strip() or "orch@localhost"
    return {"GIT_AUTHOR_NAME": name, "GIT_AUTHOR_EMAIL": email, "GIT_COMMITTER_NAME": name, "GIT_COMMITTER_EMAIL": email}


def build_commit(repo: Path, base_ref: str, moves: list[dict], adds: dict[str, bytes], message: str, user: str) -> str:
    """A commit on top of `base_ref` with `moves` (unchanged blobs) and `adds`, built in a private index."""
    with tempfile.TemporaryDirectory() as tmp:
        env = {"GIT_INDEX_FILE": os.path.join(tmp, "index")}
        git(repo, "read-tree", base_ref, env=env)
        # --index-info works without a work tree, so the same code runs in a checkout and in the bare fetch cache.
        lines = []
        for m in moves:
            entry = out(repo, "ls-tree", base_ref, "--", m["from"])
            if not entry:
                raise OrchError(f"{m['from']} is not on {base_ref} in {repo}")
            mode, _, obj = entry.split("\t", 1)[0].split()
            lines += [f"{mode} {obj}\t{m['to']}", f"0 {'0' * 40}\t{m['from']}"]
        for path, data in adds.items():
            lines.append(f"100644 {out(repo, 'hash-object', '-w', '--stdin', input=data)}\t{path}")
        git(repo, "update-index", "--index-info", input="".join(f"{l}\n" for l in lines).encode(), env=env)
        tree = out(repo, "write-tree", env=env)
    return out(repo, "commit-tree", tree, "-p", sha(repo, base_ref), "-m", message, env=_identity(user))


def publish(repo: Path, commit: str, branch: str, target: str | None) -> dict:
    """Push `commit` as a new branch to `target` (a remote name or URL); a local branch when there is no target."""
    ref = f"refs/heads/{branch}"
    if target is None:
        if ref_exists(repo, ref):
            return {"branch": branch, "status": "exists"}
        git(repo, "update-ref", ref, commit, "")
        return {"branch": branch, "status": "created-local"}
    if out(repo, "ls-remote", target, ref, env={"GIT_TERMINAL_PROMPT": "0"}):
        return {"branch": branch, "status": "exists"}
    git(repo, "push", "--quiet", target, f"{commit}:{ref}", env={"GIT_TERMINAL_PROMPT": "0"})
    return {"branch": branch, "status": "pushed"}


def execute(p: dict, coord_root: Path, coord: Tree, cfg: Config, merged_map: dict, stories, user: str,
            status: dict | None = None) -> list[dict]:
    """Build and publish the branches of a plan; returns one result per repo."""
    epic = p["epic"]
    results = []
    if p["pass"] == "archive":
        for r in p["repos"]:
            ident = normalize_repo(r["repo"])
            tree = p["_trees"][ident]
            local = ident == "."
            repo = coord_root if local else tree.repo
            commit = build_commit(repo, tree.ref, r["moves"], {}, f"chore(orch): archive epic {epic} story markers", user)
            target = ("origin" if remote_url(coord_root) else None) if local else r["repo"]
            results.append({"repo": r["repo"], "base": _base_branch(tree), **publish(repo, commit, r["branch"], target)})
    elif p["pass"] == "record":
        record = {"epic": epic, "closed_at": iso(time.time()), "closed_by": user,
                  "stories": {k: {"repo": merged_map[k]["repo"], "marker": merged_map[k]["path"]} for k in p["stories"]}}
        adds = {record_path(cfg, epic): yaml.safe_dump(record, sort_keys=False).encode()}
        text = coord.text(cfg.sprint_status)
        if text is not None:
            new_text, _ = sprint_status.derive(text, set(merged_map), markers.closed_epics(coord, cfg) | {epic})
            adds[cfg.sprint_status] = new_text.encode()
        if status is not None:
            adds[retro_path(cfg, epic)] = (json.dumps(retro_data(epic, stories, status, coord_root), indent=2) + "\n").encode()
        commit = build_commit(coord_root, coord.ref, [], adds, f"chore(orch): close epic {epic}", user)
        target = "origin" if remote_url(coord_root) else None
        results.append({"repo": ".", "base": _base_branch(coord), **publish(coord_root, commit, p["branch"], target)})
    return results


def _base_branch(tree: Tree) -> str:
    return tree.ref.removeprefix("refs/heads/").removeprefix("origin/")


def public(p: dict) -> dict:
    return {k: v for k, v in p.items() if not k.startswith("_")}

