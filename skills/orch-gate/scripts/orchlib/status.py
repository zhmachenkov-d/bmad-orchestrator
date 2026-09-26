"""Where every epic and story stands across all subprojects, and what needs a human.

State per story, first match wins: done (marker merged) · unknown (its repo was not read) · review (open PR
for `story/<key>`) · in-progress (claimed) · ready (every dependency merged) · blocked (named dependencies not
merged). Anomalies carry the orch.py arguments of the suggested action; callers add the orch.py prefix and
their own --coord flags.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from . import OrchError
from . import claims as claims_mod
from . import markers, sprint_status
from .config import Config
from .gitio import Tree, branch_tips, fetch_branches, normalize_repo, remote_url
from .registry import CONTRACTS, Registry
from .stories import StorySet, id_to_key

STORY_BRANCH = "story/"
HOST_TIMEOUT = 30
GH_LIMIT = 1000
GLAB_PER_PAGE, GLAB_PAGES = 100, 10


def story_branch(key: str) -> str:
    return f"{STORY_BRANCH}{key}"


def iso(ts: float | None) -> str | None:
    return datetime.fromtimestamp(ts, timezone.utc).isoformat(timespec="seconds") if ts else None


def _repo_url(repo: str, coord_root: Path) -> str | None:
    """Where a registry repo lives: '.' is the coordination repo's origin (else the checkout itself)."""
    return (remote_url(coord_root) or str(coord_root)) if repo == "." else repo


def branch_activity(reg: Registry, coord_root: Path, cache_root: Path, offline: bool) -> tuple[dict, list[dict]]:
    """(repo, key) -> (sha, unix time) of `story/<key>` tips per registry repo; plus repos whose branches were not read.

    Tips come from the remote, never from this clone's own branches, so every clone sees the same idle times.
    Only a coordination repo without a remote falls back to its local branches.
    """
    tips, unread, seen = {}, [], set()
    for sub in [*reg.values()]:
        ident = normalize_repo(sub.repo)
        if ident in seen:
            continue
        seen.add(ident)
        url = remote_url(coord_root) if ident == "." else sub.repo
        try:
            if ident == "." and not url:
                found = branch_tips(coord_root, (f"refs/heads/{STORY_BRANCH}",))
            elif ident == "." and offline:
                found = branch_tips(coord_root, (f"refs/remotes/origin/{STORY_BRANCH}",))
            elif offline:
                unread.append({"code": "branches-offline", "repo": sub.repo, "message": f"offline: story branches of {sub.repo} not read"})
                continue
            else:
                found = fetch_branches(url, STORY_BRANCH, cache_root / "repos")
        except (OrchError, subprocess.SubprocessError, OSError) as exc:
            unread.append({"code": "branches-unreadable", "repo": sub.repo, "message": f"cannot read story branches of {sub.repo}: {exc}"})
            continue
        for key, tip in found.items():
            tips[(ident, key)] = tip
    return tips, unread


def _host_tool(url: str) -> str | None:
    host = normalize_repo(url).split("/", 1)[0]
    if "/" not in normalize_repo(url) or url.startswith(("/", ".")):
        return None  # a local path has no review host
    return "glab" if "gitlab" in host else "gh"


def _run_host(argv: list[str]) -> list[dict]:
    proc = subprocess.run(argv, capture_output=True, text=True, timeout=HOST_TIMEOUT)
    if proc.returncode:
        text = (proc.stderr or proc.stdout).strip()
        raise OrchError(text.splitlines()[-1] if text else f"{argv[0]} failed")
    return json.loads(proc.stdout or "[]")


def _open_reviews(tool: str, url: str) -> tuple[list[dict], bool]:
    """[{branch, created_at, url}] of open PRs/MRs whose head is a story branch; True when the list was cut short."""
    if tool == "gh":
        rows = _run_host(["gh", "pr", "list", "-R", normalize_repo(url), "--state", "open", "--limit", str(GH_LIMIT),
                          "--json", "headRefName,createdAt,url"])
        keys, truncated = ("headRefName", "createdAt", "url"), len(rows) >= GH_LIMIT
    else:
        rows, truncated = [], True
        for page in range(1, GLAB_PAGES + 1):
            batch = _run_host(["glab", "mr", "list", "-R", url, "--output", "json", "--per-page", str(GLAB_PER_PAGE),
                               "--page", str(page)])
            rows += batch
            if len(batch) < GLAB_PER_PAGE:
                truncated = False
                break
        keys = ("source_branch", "created_at", "web_url")
    return ([{"branch": r.get(keys[0], ""), "created_at": r.get(keys[1]), "url": r.get(keys[2])}
             for r in rows if str(r.get(keys[0], "")).startswith(STORY_BRANCH)], truncated)


def reviews(reg: Registry, coord_root: Path) -> tuple[dict, dict, list[dict]]:
    """(repo ident, key) -> open review; per-repo source ('gh' | 'glab' | 'none'); notices for degraded repos."""
    found, sources, notices = {}, {}, []
    for ident, repo in {normalize_repo(s.repo): s.repo for s in reg.values()}.items():
        url = _repo_url(repo, coord_root)
        tool = _host_tool(url) if url else None
        if not tool or not shutil.which(tool):
            sources[ident] = "none"
            if tool:
                notices.append({"code": "review-host-unavailable", "repo": repo,
                                "message": f"{tool} not installed: review waits in {repo} fall back to branch age"})
            continue
        try:
            rows, truncated = _open_reviews(tool, url)
        except (OrchError, subprocess.SubprocessError, OSError, json.JSONDecodeError) as exc:
            sources[ident] = "none"
            notices.append({"code": "review-host-unavailable", "repo": repo,
                            "message": f"{tool} could not list reviews of {repo} ({exc}); falling back to branch age"})
            continue
        sources[ident] = tool
        if truncated:
            notices.append({"code": "review-list-truncated", "repo": repo,
                            "message": f"{tool} listed only the newest open reviews of {repo}; older story reviews may show as in-progress"})
        for r in rows:
            found[(ident, r["branch"][len(STORY_BRANCH):])] = r
    return found, sources, notices


def _parse_time(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _downstream(stories: StorySet) -> dict[str, set[str]]:
    """key -> every story that transitively depends on it."""
    direct = {k: set(stories.dependents(k)) for k in stories}
    memo: dict[str, set[str]] = {}

    def walk(k: str, path: frozenset) -> set[str]:
        if k in memo:
            return memo[k]
        acc = set()
        for d in direct[k]:
            if d not in path:
                acc |= {d} | walk(d, path | {d})
        memo[k] = acc
        return acc

    return {k: walk(k, frozenset({k})) for k in stories}


def critical_path(stories: StorySet, epic: int, done: set[str]) -> list[str]:
    """Longest chain of not-done stories in an epic (plan order breaks ties)."""
    best: dict[str, list[str]] = {}
    for s in stories.values():  # plan order: dependencies come first
        if s.epic != epic or s.key in done:
            continue
        chains = [best[k] for dep in s.depends_on if (k := id_to_key(dep)) in best]
        best[s.key] = max(chains, key=len, default=[]) + [s.key]
    return max(best.values(), key=len, default=[])


def build(reg: Registry, stories: StorySet, coord_root: Path, coord: Tree, cfg: Config, cache_root: Path, *,
          remote: str | None, offline: bool = False, host: bool = True, local_run: bool = True,
          epic: int | None = None, now: float | None = None) -> dict:
    now = now if now is not None else time.time()
    merged_map, unread = markers.merged(reg, coord, cache_root, offline=offline)
    unknown = markers.unknown_keys(stories, unread)
    claim_list = claims_mod.list_claims(coord_root, remote, now)
    claimed = {c["story"]: c for c in claim_list}
    tips, branch_unread = branch_activity(reg, coord_root, cache_root, offline)
    open_reviews, review_source, notices = reviews(reg, coord_root) if host and not offline else ({}, {}, [])
    closed = markers.closed_epics(coord, cfg)
    sprint_text = coord.text(cfg.sprint_status)
    sprint = (sprint_status.parsed(sprint_text) or {}) if sprint_text else {}
    done = set(merged_map)
    downstream = _downstream(stories)
    # states and anomalies over every epic, so --epic filters the output without changing it
    all_epics = sorted({s.epic for s in stories.values()})
    epics = all_epics if epic is None else [epic]
    critical = {e: critical_path(stories, e, done) for e in all_epics}
    branch_blind = {normalize_repo(u["repo"]) for u in branch_unread}
    on_critical = {k for path in critical.values() for k in path}

    rows, anomalies = [], []
    for s in stories.values():
        sub = reg.get(s.subproject) if s.subproject else None
        ident = normalize_repo(sub.repo) if sub else "."
        claim = claimed.get(s.key)
        tip = tips.get((ident, s.key))
        review = open_reviews.get((ident, s.key))
        deps = [id_to_key(d) for d in s.depends_on]
        activity = max([t for t in ((claim or {}).get("claimed_at"), tip[1] if tip else None) if t], default=None)
        if s.key in done:
            state = "done"
        elif s.key in unknown:
            state = "unknown"
        elif review:
            state = "review"
        elif claim:
            state = "in-progress"
        elif all(d in done for d in deps):
            state = "ready"
        elif all(d in done or d in unknown for d in deps):
            state = "unknown"
        else:
            state = "blocked"
        row = {
            "key": s.key, "id": s.id, "epic": s.epic, "title": s.title, "subproject": s.subproject, "state": state,
            "claimant": (claim or {}).get("user"), "claim_sha": (claim or {}).get("sha"),
            "claimed_at": iso((claim or {}).get("claimed_at")), "branch": story_branch(s.key) if tip else None,
            "last_activity": iso(activity), "idle_hours": round((now - activity) / 3600, 1) if activity else None,
            # claim-only: the story branch could not be read, so the claim time is all that is known
            "activity_source": "branch" if tip and tip[1] >= ((claim or {}).get("claimed_at") or 0) else (
                "claim-only" if ident in branch_blind else "claim") if claim else ("branch" if tip else None),
            "review": {**review, "age_hours": round((now - (_parse_time(review["created_at"]) or now)) / 3600, 1)} if review else None,
            "blocked_by": [d for d in deps if d not in done],
            "dependents": stories.dependents(s.key), "downstream": len(downstream[s.key]),
            "critical": s.key in on_critical,
        }
        rows.append(row)
        anomalies += _story_anomalies(row, s, cfg, sprint, review_source.get(ident, "none"), now)

    by_key = {r["key"]: r for r in rows}
    for r in rows:
        s = stories[r["key"]]
        if s.subproject == CONTRACTS and r["state"] not in ("done", "unknown"):
            waiting = [d for d in r["dependents"] if by_key.get(d, {}).get("blocked_by") == [r["key"]]]
            if len(waiting) >= 2 or r["critical"]:
                anomalies.append({"code": "contract-bottleneck", "story": r["key"], "severity": "warn",
                                  "message": f"contract story {r['id']} ({r['state']}) blocks {len(waiting)} waiting stories"
                                             f"{' and is on the critical path' if r['critical'] else ''}; {r['downstream']} downstream",
                                  "waiting": waiting, "downstream": r["downstream"], "actions": []})

    index = markers.PinIndex(stories, merged_map, coord)
    migrate: dict[str, list[tuple[dict, object, dict]]] = {}
    for key, hit in merged_map.items():
        s = stories.get(key)
        if not s or s.epic in closed or not (m := markers.load_marker(hit, key)):
            continue
        for d in markers.pin_drift(s, m, stories, merged_map, coord, index):
            a = {"code": "pin-drift", "story": key, "severity": "warn", "message": f"story {s.id}: {d['message']}",
                 "path": d["path"], "reason": d["reason"], "actions": []}
            # a new story of this subproject converges only a not-migrated pin; an unrecorded change needs a
            # contract story, and a contract story's own drift is the contract owner's call
            if d["reason"] == "not-migrated" and s.subproject != CONTRACTS:
                migrate.setdefault(s.subproject, []).append((a, s, d))
            anomalies.append(a)
    drafted: dict[int, int] = {}
    for sub, found in sorted(migrate.items()):
        draft = migration_draft(stories, merged_map, sub, [(s, d) for _, s, d in found], index, drafted)
        for a, _, _ in found:
            a["migration_draft"] = draft

    for key, c in claimed.items():
        if key in done:
            anomalies.append({"code": "claim-on-merged", "story": key, "severity": "info",
                              "message": f"story {key} is merged but its claim ref is still there",
                              "actions": [{"label": "release", "args": ["claim", "release", "--story", key, "--expect", c["sha"]]}]})

    if local_run and sprint_status.attribute_present(coord_root) and not sprint_status.driver_registered(coord_root):
        anomalies.append({"code": "merge-driver-missing", "story": None, "severity": "warn",
                          "message": "this clone has no sprint-status merge driver registered",
                          "actions": [{"label": "install driver", "args": ["sprint-status", "install-driver"]}]})
    lag = [k for k, v in sprint.items() if (sk := sprint_status.story_key(k)) and sk in done and v != "done"]
    if lag and not unread:
        anomalies.append({"code": "sprint-status-lag", "story": None, "severity": "info",
                          "message": f"{len(lag)} merged stories are not done in sprint-status.yaml",
                          "keys": lag, "actions": [{"label": "rebuild sprint status", "args": ["sprint-status", "derive"]}]})

    if epic is not None:
        rows = [r for r in rows if r["epic"] == epic]
        shown = {r["key"] for r in rows}
        anomalies = [a for a in anomalies if a.get("story") is None or a["story"] in shown]

    epic_rows = []
    for e in epics:
        mine = [r for r in rows if r["epic"] == e]
        counts = {st: sum(r["state"] == st for r in mine) for st in ("done", "review", "in-progress", "ready", "blocked", "unknown")}
        if e in closed:
            state, problems = "closed", []
        elif mine and counts["done"] == len(mine):
            problems = markers.close_check(e, stories, reg, merged_map, coord, unknown)
            drift = [p for p in problems if p.get("code") == "pin-drift"]
            state = "drift" if drift else ("archive-needed" if problems else "closable")
        else:
            state, problems = "open", []
        epic_rows.append({"epic": e, "state": state, "stories": len(mine), "counts": counts,
                          "critical_path": critical[e], "close_problems": problems,
                          "subprojects": _by_subproject(mine)})

    return {"now": iso(now), "epics": epic_rows, "stories": rows, "anomalies": anomalies,
            "unread_repos": unread + branch_unread, "notices": notices,
            "review_source": review_source, "claims": claim_list}


def migration_draft(stories: StorySet, merged_map: dict, sub: str, found: list, index, drafted: dict[int, int]) -> dict:
    """One story that makes `sub` build against every contract it drifts from, as a ready-to-paste plan block.

    A new story of the subproject pins all of its contracts at once, so one draft covers every drifting path. It
    goes last in the latest epic among the drifting stories and its dependencies, so it depends on nothing later
    in the plan, and takes a number no story in the plan or on main has used.
    """
    paths = sorted({d["path"] for _, d in found})
    deps = [stories[k] for k in stories if k in {c[-1][0].key for p in paths if (c := index.chain.get(p))}]
    depends = [x.id for x in deps]
    epic = max([s.epic for s, _ in found] + [x.epic for x in deps])
    taken = [int(m.group(1)) for x in stories.values() if x.epic == epic and (m := re.match(r"^\d+\.(\d+)", x.id))]
    taken += [int(m.group(1)) for k in merged_map if (m := re.match(rf"^{epic}-(\d+)", k))]
    drafted[epic] = max([*taken, drafted.get(epic, 0)], default=0) + 1
    story_id = f"{epic}.{drafted[epic]}"
    names = ", ".join(PurePosixPath(p).name for p in paths)
    title = f"{sub} builds against the current {names}"
    block = (f"### Story {story_id}: {title}\n**Subproject:** {sub}\n"
             f"**Depends on:** {', '.join(depends) or 'none'}\n**Contract change:** none\n")
    return {"id": story_id, "title": title, "subproject": sub, "epic": epic, "depends_on": depends,
            "contract_change": "none", "contracts": paths, "markdown": block}


def _story_anomalies(row: dict, s, cfg: Config, sprint: dict, source: str, now: float) -> list[dict]:
    found = []
    key, idle = row["key"], row["idle_hours"]
    if row["state"] == "in-progress" and idle is not None and idle > cfg.stale_claim_hours:
        blind = row["activity_source"] == "claim-only"
        found.append({"code": "stale-claim", "story": key, "severity": "info" if blind else "warn",
                      "message": f"story {s.id} claimed by {row['claimant']}: no activity for {idle:.0f}h (> {cfg.stale_claim_hours:g}h)"
                                 + (", counting from the claim only: its story branch could not be read" if blind else ""),
                      "actions": [] if blind else [{"label": "take over", "args": ["claim", "take-over", "--story", key, "--expect", row["claim_sha"]]},
                                  {"label": "release", "args": ["claim", "release", "--story", key, "--expect", row["claim_sha"]]}]})
    if row["review"] and row["review"]["age_hours"] > cfg.review_wait_hours:
        found.append({"code": "stuck-review", "story": key, "severity": "warn", "source": source,
                      "message": f"story {s.id} in review for {row['review']['age_hours']:.0f}h (> {cfg.review_wait_hours:g}h), "
                                 f"{row['downstream']} stories downstream", "url": row["review"]["url"], "actions": []})
    elif (source == "none" and row["state"] == "in-progress" and sprint.get(s.sprint_key) == "review"
          and idle is not None and idle > cfg.review_wait_hours):
        found.append({"code": "stuck-review", "story": key, "severity": "warn", "source": "branch-age",
                      "message": f"story {s.id} is 'review' in sprint status and its branch is idle for {idle:.0f}h "
                                 f"(> {cfg.review_wait_hours:g}h)", "actions": []})
    return found


def _by_subproject(rows: list[dict]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for r in rows:
        d = out.setdefault(r["subproject"] or "?", {"stories": 0, "done": 0})
        d["stories"] += 1
        d["done"] += r["state"] == "done"
    return dict(sorted(out.items()))
