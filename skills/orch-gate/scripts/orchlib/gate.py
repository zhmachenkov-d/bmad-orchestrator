"""The pre-merge verdict. Pure git + registry + detectors; no network except pull-based reads of registry repos."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from . import markers, sprint_status
from .config import Config
from .detectors import run as run_detector
from .gitio import Tree, changed_files, merge_base, resolve_head
from .globs import matches
from .registry import CONTRACTS, Registry, pin_paths
from .stories import KEY_RE, StorySet, id_to_key


@dataclass
class Context:
    repo: Path
    base: str
    head: str
    repo_id: str                 # "." when this repo is the coordination repo, else normalized URL
    coord: Tree                  # coordination repo at main
    cfg: Config
    reg: Registry
    stories: StorySet
    cache_root: Path
    ci: bool = False
    offline: bool = False
    local_repos: dict = field(default_factory=dict)   # normalized repo id -> Tree (tests, pre-fetched clones)
    detector_runner: object = None
    detector_which: object = None


class Check:
    def __init__(self, id_: str):
        self.id, self.status, self.findings = id_, "pass", []

    def fail(self, message: str, **extra):
        self.status = "fail"
        self.findings.append({"level": "fail", "message": message, **extra})

    def warn(self, message: str, **extra):
        if self.status == "pass":
            self.status = "warn"
        self.findings.append({"level": "warn", "message": message, **extra})

    def skip(self, reason: str):
        self.status = "skip"
        self.findings.append({"level": "info", "message": reason})

    def to_dict(self) -> dict:
        return {"id": self.id, "status": self.status, "findings": self.findings}


def _norm(data: bytes) -> bytes:
    return b"\n".join(line.rstrip() for line in data.replace(b"\r\n", b"\n").split(b"\n")).rstrip() + b"\n"


def run(ctx: Context) -> dict:
    head_sha, head_note = resolve_head(ctx.repo, ctx.base, ctx.head)
    changes = changed_files(ctx.repo, ctx.base, head_sha)
    mb = merge_base(ctx.repo, ctx.base, head_sha)
    head, base_tip, mb_tree = Tree(ctx.repo, head_sha), Tree(ctx.repo, ctx.base), Tree(ctx.repo, mb)
    is_coord = ctx.repo_id == "."
    live = [c for c in changes if c["status"] != "D" and markers.MARKER_RE.match(c["path"])]
    archive_ok, archive_problems = markers.is_archive_move(changes, head, mb_tree)
    checks = {k: Check(k) for k in ("marker", "scope", "pins", "conformance", "breaking", "sprint-status", "merge-driver")}
    result = {"verdict": "pass", "mode": "story" if live else "no-story", "story": None, "subproject": None,
              "repo": ctx.repo_id, "base": ctx.base, "head": ctx.head, "head_sha": head_sha, "merge_base": mb,
              "changed": [c["path"] for c in changes]}
    if head_note:
        result["head_resolved"] = head_note

    for p in archive_problems:
        checks["scope"].fail(p)

    story = sub = marker = None
    if len(live) > 1:
        checks["marker"].fail("a PR carries exactly one story; found markers: " + ", ".join(c["path"] for c in live),
                              hint="split the PR per story")
    elif live:
        path = live[0]["path"]
        key = markers.MARKER_RE.match(path).group(1)
        result["story"] = key
        story = ctx.stories.get(key) if KEY_RE.match(key) else None
        if story is None:
            checks["marker"].fail(f"{path}: story '{key}' is not in the epics on the coordination main", path=path)
        else:
            marker, problems = markers.parse(head.read(path) or b"", key)
            for p in problems:
                checks["marker"].fail(f"{path}: {p}", path=path)
            if marker and marker.get("epic") not in (None, story.epic):
                checks["marker"].fail(f"{path}: epic {marker.get('epic')} but story {story.id} is in epic {story.epic}", path=path)
            if not story.subproject or story.subproject not in ctx.reg:
                checks["marker"].fail(f"story {story.id} has no valid subproject in the plan ({story.subproject!r})",
                                      hint="fix the **Subproject:** line in the epics file")
                story = None
            else:
                sub = ctx.reg[story.subproject]
                result["subproject"] = sub.name
                expected = "." if sub.repo == "." else markers.normalize_repo(sub.repo)
                if expected != ctx.repo_id:
                    checks["marker"].fail(f"story {story.id} belongs to subproject '{sub.name}' in repo '{sub.repo}', not this repo ({ctx.repo_id})")
                    story = None
    else:
        checks["marker"].skip("no story marker: non-story change (planning, infra, docs)")

    # --- scope ---
    bookkeeping = [f"{ctx.cfg.implementation_artifacts}/**"] if is_coord else []
    if story and sub:
        allowed = sub.allowed_write + [markers.marker_path(story.key)] + bookkeeping
        for c in changes:
            if c["path"] in archive_ok or matches(c["path"], allowed):
                continue
            checks["scope"].fail(f"{c['path']} is outside {sub.name}'s allowed_write", path=c["path"],
                                 hint=f"move this change to a story of the owning subproject, or amend allowed_write via a registry PR")
    elif not live:
        protected = {}
        for s in ctx.reg.values():
            if s.name == CONTRACTS and not is_coord:
                continue
            if ("." if s.repo == "." else markers.normalize_repo(s.repo)) == ctx.repo_id:
                for pattern in s.allowed_write:
                    protected[pattern] = s.name
        for c in changes:
            if c["path"] in archive_ok:
                continue
            owner = next((n for pat, n in protected.items() if matches(c["path"], [pat])), None)
            if owner:
                checks["scope"].fail(f"{c['path']} belongs to subproject '{owner}'; changes there need a story marker",
                                     path=c["path"], hint="run orch-next for the story, or add .orch/stories/<story>.yaml via bmad-build")

    # --- pins ---
    touched = {}
    for c in changes:
        owned = ctx.reg.canonical_owner(c["path"])
        if owned:
            touched[owned[1].canonical] = owned
    if story and sub and marker is not None:
        pins = marker.get("contract_pins", {})
        if sub.name == CONTRACTS:
            for canonical in sorted(touched):
                if mb_tree.blob_sha(canonical) != base_tip.blob_sha(canonical):
                    checks["pins"].fail(f"{canonical} changed on main since this branch started",
                                        hint="rebase onto the latest contract and get the contract change re-approved")
                want = head.blob_sha(canonical)
                if pins.get(canonical) != want:
                    checks["pins"].fail(f"pin for {canonical} is {pins.get(canonical)!r}, expected the PR's version {want}",
                                        hint="regenerate the marker: orch.py marker write --story " + story.key)
        else:
            for canonical in pin_paths(ctx.reg, sub.name):
                want = ctx.coord.blob_sha(canonical)
                if want is None:
                    continue
                if canonical not in pins:
                    checks["pins"].fail(f"marker does not pin {canonical}", hint="regenerate the marker: orch.py marker write --story " + story.key)
                elif pins[canonical] != want:
                    checks["pins"].fail(f"{canonical} is pinned at {pins[canonical][:10]} but main has {want[:10]}",
                                        hint="rebase onto the latest contract, re-check the implementation, regenerate the marker")
        prd_now = ctx.coord.blob_sha(ctx.cfg.prd)
        if marker.get("prd_pin") and prd_now and marker["prd_pin"] != prd_now:
            checks["pins"].warn(f"PRD changed since the story was pinned ({marker['prd_pin'][:10]} -> {prd_now[:10]})",
                                hint="re-read the PRD changes before merging")
    else:
        checks["pins"].skip("no story")

    # --- conformance ---
    if story and sub and sub.name != CONTRACTS:
        copies = [e for e in sub.exports if e.copy]
        if not copies:
            checks["conformance"].skip(f"{sub.name} declares no contract copies")
        for exp in copies:
            canonical = ctx.coord.read(exp.canonical)
            copy = head.read(exp.copy)
            if canonical is None:
                checks["conformance"].warn(f"canonical {exp.canonical} missing on coordination main")
            elif copy is None:
                checks["conformance"].fail(f"contract copy {exp.copy} is missing", path=exp.copy,
                                           hint=f"copy {exp.canonical} from the coordination repo")
            elif _norm(copy) != _norm(canonical):
                checks["conformance"].fail(f"{exp.copy} differs from canonical {exp.canonical}", path=exp.copy,
                                           hint="copy or regenerate from the canonical contract; contract edits go through a contract story")
    else:
        checks["conformance"].skip("no implementation story")

    # --- breaking changes ---
    merged_cache: dict = {}

    def merged_map():
        if "m" not in merged_cache:
            merged_cache["m"], merged_cache["w"] = markers.merged(ctx.reg, head if is_coord else ctx.coord, ctx.cache_root,
                                                                  ctx.local_repos, ctx.offline)
        return merged_cache["m"]

    if touched and is_coord:
        det_kw = {k: v for k, v in (("runner", ctx.detector_runner), ("which", ctx.detector_which)) if v}
        for canonical, (owner, exp) in sorted(touched.items()):
            res = run_detector(exp.type, canonical, mb_tree, head, ctx.repo, mb, exp.extra, **det_kw)
            if res["status"] in ("ok", "added"):
                continue
            if res["status"] == "missing":
                checks["breaking"].fail(f"{canonical} ({exp.type}): {res['output']}")
                continue
            consumers = ctx.reg.consumers(owner.name)
            if story and story.contract_change == "narrow":
                merged_now = merged_map()
                dep_subs = {ctx.stories[id_to_key(d)].subproject: id_to_key(d) for d in story.depends_on if id_to_key(d) in ctx.stories}
                pending = [c for c in consumers if c not in dep_subs or dep_subs[c] not in merged_now]
                if not pending:
                    checks["breaking"].warn(f"{canonical}: breaking change accepted — narrow story, all consumers migrated ({', '.join(consumers) or 'none'})")
                    continue
                checks["breaking"].fail(f"{canonical}: breaking change, consumers not yet migrated: {', '.join(pending)}",
                                        hint="the narrow story must depend on a merged migrate story for each consumer",
                                        detail=res["output"])
            else:
                checks["breaking"].fail(f"{canonical}: breaking change ({res['status']})",
                                        hint="split into expand -> migrate -> contract; only a 'Contract change: narrow' story may break, after every consumer migrated",
                                        detail=res["output"])
    else:
        checks["breaking"].skip("no canonical contract changed")

    # --- sprint status ---
    status_path, closed_prefix = ctx.cfg.sprint_status, ctx.cfg.closed_dir + "/"
    touches_status = any(c["path"] == status_path or c["path"].startswith(closed_prefix) for c in changes)
    if is_coord and touches_status:
        text = head.text(status_path)
        closed = markers.closed_epics(head, ctx.cfg)
        mm = merged_map()
        for w in merged_cache.get("w", []):
            checks["sprint-status"].warn(w)
        if text is not None:
            res = sprint_status.check(text, set(mm), closed)
            for f in res["fail"]:
                checks["sprint-status"].fail(f.pop("message"), **f)
            for f in res["warn"]:
                checks["sprint-status"].warn(f.pop("message"), **f)
        newly_closed = closed - markers.closed_epics(mb_tree, ctx.cfg)
        for epic in sorted(newly_closed):
            for p in markers.close_check(epic, ctx.stories, ctx.reg, mm, ctx.coord):
                checks["sprint-status"].fail(f"epic {epic} close record added but close check fails: {p['message']}",
                                             hint="close epics through orch-status")
    else:
        checks["sprint-status"].skip("sprint status not touched" if is_coord else "not the coordination repo")

    # --- merge driver (local clones only; CI never merges) ---
    if ctx.ci:
        checks["merge-driver"].skip("CI run")
    elif sprint_status.attribute_present(ctx.repo) and not sprint_status.driver_registered(ctx.repo):
        checks["merge-driver"].fail("this clone has no sprint-status merge driver registered",
                                    hint="run: orch.py sprint-status install-driver")
    else:
        checks["merge-driver"].skip("driver registered or not used in this repo")

    result["checks"] = [c.to_dict() for c in checks.values()]
    result["verdict"] = "fail" if any(c.status == "fail" for c in checks.values()) else "pass"
    result["ok"] = result["verdict"] == "pass"
    return result


def is_ci() -> bool:
    return bool(os.environ.get("CI"))


def render_text(result: dict) -> str:
    tags = {"pass": "PASS", "fail": "FAIL", "warn": "WARN", "skip": "SKIP"}
    who = f"story {result['story']} ({result['subproject'] or '?'})" if result.get("story") else "non-story change"
    lines = [f"orch-gate: {result['verdict'].upper()} - {who} - {result['base']}..{result['head']}"]
    if result.get("head_resolved"):
        lines.append(f"  note: {result['head_resolved']}")
    for c in result["checks"]:
        lines.append(f"  [{tags[c['status']]}] {c['id']}")
        for f in c["findings"]:
            if f["level"] == "info":
                continue
            lines.append(f"      - {f['message']}")
            if f.get("hint"):
                lines.append(f"        fix: {f['hint']}")
            if f.get("detail"):
                lines += ["        | " + d for d in f["detail"].splitlines()[:20]]
    return "\n".join(lines)
