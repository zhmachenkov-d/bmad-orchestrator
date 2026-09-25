"""The pre-merge verdict. Pure git + registry + detectors; no network except pull-based reads of registry repos.

Every finding carries a stable `code`. A finding that orch can fix by itself also carries
`fix = {mechanical: true, command: [...], precondition: str|None}`; callers offer that command and never
map messages to fixes themselves.
"""

from __future__ import annotations

import difflib
import os
from dataclasses import dataclass, field
from pathlib import Path

from . import markers, sprint_status
from .config import Config
from .detectors import run as run_detector
from .detectors import version as detector_version
from .gitio import Tree, changed_files, dirty_paths, merge_base, resolve_head, sha
from .globs import matches
from .registry import CONTRACTS, Registry, pin_paths
from .stories import KEY_RE, StorySet, id_to_key

DETAIL_LINES = 60


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


def fix(*command: str, precondition: str | None = None) -> dict:
    return {"mechanical": True, "command": ["orch.py", *command], "precondition": precondition}


class Check:
    def __init__(self, id_: str):
        self.id, self.status, self.findings = id_, "pass", []

    def fail(self, code: str, message: str, **extra):
        self.status = "fail"
        self.findings.append({"level": "fail", "code": code, "message": message, **extra})

    def warn(self, code: str, message: str, **extra):
        if self.status == "pass":
            self.status = "warn"
        self.findings.append({"level": "warn", "code": code, "message": message, **extra})

    def skip(self, reason: str):
        self.status = "skip"
        self.findings.append({"level": "info", "code": "skipped", "message": reason})

    def to_dict(self) -> dict:
        return {"id": self.id, "status": self.status, "findings": self.findings}


def _norm(data: bytes) -> bytes:
    return b"\n".join(line.rstrip() for line in data.replace(b"\r\n", b"\n").split(b"\n")).rstrip() + b"\n"


def _diff(canonical: bytes, copy: bytes, canonical_path: str, copy_path: str) -> str:
    lines = list(difflib.unified_diff(_norm(canonical).decode("utf-8", "replace").splitlines(),
                                      _norm(copy).decode("utf-8", "replace").splitlines(),
                                      fromfile=canonical_path, tofile=copy_path, lineterm=""))
    if len(lines) > DETAIL_LINES:
        lines = lines[:DETAIL_LINES] + [f"... {len(lines) - DETAIL_LINES} more diff lines"]
    return "\n".join(lines)


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
              "repo": ctx.repo_id, "base": ctx.base, "base_sha": sha(ctx.repo, ctx.base), "head": ctx.head,
              "head_sha": head_sha, "merge_base": mb, "coord_ref": ctx.coord.ref,
              "coord_sha": sha(ctx.coord.repo, ctx.coord.ref), "offline": ctx.offline,
              "changed": [c["path"] for c in changes], "notices": [], "detectors_used": []}
    if head_note:
        result["head_resolved"] = head_note
    if not ctx.ci:
        # Informational only: the verdict is about committed history, so this never changes it.
        dirty = dirty_paths(ctx.repo, ignore_prefix=markers.CACHE_DIR + "/")
        if dirty:
            result["notices"].append({"code": "dirty-worktree",
                                      "message": f"{len(dirty)} uncommitted path(s), invisible to this verdict: "
                                                 + ", ".join(dirty[:5]) + (" ..." if len(dirty) > 5 else ""),
                                      "hint": "commit, then re-run the gate"})

    for p in archive_problems:
        checks["scope"].fail("archive-move-invalid", p)

    story = sub = marker = None
    if len(live) > 1:
        checks["marker"].fail("multiple-markers", "a PR carries exactly one story; found markers: " + ", ".join(c["path"] for c in live),
                              hint="split the PR per story")
    elif live:
        path = live[0]["path"]
        key = markers.MARKER_RE.match(path).group(1)
        result["story"] = key
        story = ctx.stories.get(key) if KEY_RE.match(key) else None
        if story is None:
            checks["marker"].fail("unknown-story", f"{path}: story '{key}' is not in the epics on the coordination main", path=path)
        else:
            marker, problems = markers.parse(head.read(path) or b"", key)
            for p in problems:
                checks["marker"].fail("marker-invalid", f"{path}: {p}", path=path,
                                      fix=fix("marker", "write", "--story", key, precondition="commit the story's changes first"))
            if marker and marker.get("epic") not in (None, story.epic):
                checks["marker"].fail("marker-epic-mismatch", f"{path}: epic {marker.get('epic')} but story {story.id} is in epic {story.epic}",
                                      path=path, fix=fix("marker", "write", "--story", key))
            if not story.subproject or story.subproject not in ctx.reg:
                checks["marker"].fail("no-subproject", f"story {story.id} has no valid subproject in the plan ({story.subproject!r})",
                                      hint="fix the **Subproject:** line in the epics file")
                story = None
            else:
                sub = ctx.reg[story.subproject]
                result["subproject"] = sub.name
                expected = "." if sub.repo == "." else markers.normalize_repo(sub.repo)
                if expected != ctx.repo_id:
                    checks["marker"].fail("wrong-repo", f"story {story.id} belongs to subproject '{sub.name}' in repo '{sub.repo}', not this repo ({ctx.repo_id})")
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
            checks["scope"].fail("out-of-scope", f"{c['path']} is outside {sub.name}'s allowed_write", path=c["path"],
                                 hint="move this change to a story of the owning subproject, or amend allowed_write via a registry PR")
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
                checks["scope"].fail("needs-story", f"{c['path']} belongs to subproject '{owner}'; changes there need a story marker",
                                     path=c["path"], hint="run orch-next for the story, or add .orch/stories/<story>.yaml via bmad-build")

    # --- pins ---
    touched = {}
    for c in changes:
        owned = ctx.reg.canonical_owner(c["path"])
        if owned:
            touched[owned[1].canonical] = owned
    if story and sub and marker is not None:
        pins = marker.get("contract_pins", {})
        rewrite = fix("marker", "write", "--story", story.key, precondition="commit the story's changes first")
        if sub.name == CONTRACTS:
            for canonical in sorted(touched):
                if mb_tree.blob_sha(canonical) != base_tip.blob_sha(canonical):
                    checks["pins"].fail("contract-moved-on-main", f"{canonical} changed on main since this branch started",
                                        hint="rebase onto the latest contract and get the contract change re-approved")
                want = head.blob_sha(canonical)
                if pins.get(canonical) != want:
                    checks["pins"].fail("contract-pin-mismatch", f"pin for {canonical} is {pins.get(canonical)!r}, expected the PR's version {want}",
                                        fix=rewrite)
        else:
            for canonical in pin_paths(ctx.reg, sub.name):
                want = ctx.coord.blob_sha(canonical)
                if want is None:
                    continue
                if canonical not in pins:
                    checks["pins"].fail("pin-missing", f"marker does not pin {canonical}", fix=rewrite)
                elif pins[canonical] != want:
                    checks["pins"].fail("pin-stale", f"{canonical} is pinned at {pins[canonical][:10]} but main has {want[:10]}",
                                        fix=fix("marker", "write", "--story", story.key,
                                                precondition=f"rebase onto {ctx.base} and re-check the implementation against the new contract"))
        prd_now = ctx.coord.blob_sha(ctx.cfg.prd)
        if marker.get("prd_pin") and prd_now and marker["prd_pin"] != prd_now:
            checks["pins"].warn("prd-changed", f"PRD changed since the story was pinned ({marker['prd_pin'][:10]} -> {prd_now[:10]})",
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
                checks["conformance"].warn("canonical-missing", f"canonical {exp.canonical} missing on coordination main")
            elif copy is None:
                checks["conformance"].fail("copy-missing", f"contract copy {exp.copy} is missing", path=exp.copy,
                                           hint=f"copy {exp.canonical} from the coordination repo")
            elif _norm(copy) != _norm(canonical):
                checks["conformance"].fail("copy-drift", f"{exp.copy} differs from canonical {exp.canonical}", path=exp.copy,
                                           hint="copy or regenerate from the canonical contract; contract edits go through a contract story",
                                           detail=_diff(canonical, copy, exp.canonical, exp.copy))
    else:
        checks["conformance"].skip("no implementation story")

    # --- breaking changes ---
    merged_cache: dict = {}

    def merged_map():
        if "m" not in merged_cache:
            merged_cache["m"], unread = markers.merged(ctx.reg, head if is_coord else ctx.coord, ctx.cache_root,
                                                       ctx.local_repos, ctx.offline)
            merged_cache["unknown"] = markers.unknown_keys(ctx.stories, unread)
            if unread:
                result["unread_repos"] = unread
        return merged_cache["m"]

    if touched and is_coord:
        det_kw = {k: v for k, v in (("runner", ctx.detector_runner), ("which", ctx.detector_which)) if v}
        for canonical, (owner, exp) in sorted(touched.items()):
            res = run_detector(exp.type, canonical, mb_tree, head, ctx.repo, mb, exp.extra, **det_kw)
            if res["status"] != "missing" and not any(d["type"] == exp.type for d in result["detectors_used"]):
                result["detectors_used"].append({"type": exp.type, **detector_version(exp.type, **det_kw),
                                                 **({"inputs": res["inputs"]} if res.get("inputs") else {})})
            if res["status"] in ("ok", "added"):
                continue
            if res["status"] == "missing":
                checks["breaking"].fail("detector-missing", f"{canonical} ({exp.type}): {res['output']}")
                continue
            consumers = ctx.reg.consumers(owner.name)
            if story and story.contract_change == "narrow":
                merged_now = merged_map()
                dep_subs = {ctx.stories[id_to_key(d)].subproject: id_to_key(d) for d in story.depends_on if id_to_key(d) in ctx.stories}
                pending = [c for c in consumers if c not in dep_subs or dep_subs[c] not in merged_now]
                if not pending:
                    checks["breaking"].warn("narrow-accepted", f"{canonical}: breaking change accepted — narrow story, all consumers migrated ({', '.join(consumers) or 'none'})")
                    continue
                unverified = [c for c in pending if c in dep_subs and dep_subs[c] in merged_cache["unknown"]]
                if unverified:
                    checks["breaking"].fail("consumers-unverifiable", f"{canonical}: cannot verify that consumers migrated, their repo was not read: {', '.join(unverified)}",
                                            hint="give this runner read access to those repos (or drop --offline) and re-run")
                missing = [c for c in pending if c not in unverified]
                if missing:
                    checks["breaking"].fail("consumers-not-migrated", f"{canonical}: breaking change, consumers not yet migrated: {', '.join(missing)}",
                                            hint="the narrow story must depend on a merged migrate story for each consumer",
                                            detail=res["output"])
            else:
                checks["breaking"].fail("breaking-change", f"{canonical}: breaking change ({res['status']})",
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
        unknown = merged_cache["unknown"]
        for u in result.get("unread_repos", []):
            checks["sprint-status"].warn(u["code"], u["message"])
        if text is not None:
            res = sprint_status.check(text, set(mm), closed, unknown)
            for f in res["fail"]:
                checks["sprint-status"].fail(f.pop("code"), f.pop("message"), **f)
            for f in res["warn"]:
                if f["code"] in ("merged-not-done", "closed-not-done"):
                    f["fix"] = fix("sprint-status", "derive", "--write")
                checks["sprint-status"].warn(f.pop("code"), f.pop("message"), **f)
        newly_closed = closed - markers.closed_epics(mb_tree, ctx.cfg)
        for epic in sorted(newly_closed):
            for p in markers.close_check(epic, ctx.stories, ctx.reg, mm, ctx.coord, unknown):
                checks["sprint-status"].fail("epic-close-failed", f"epic {epic} close record added but close check fails: {p['message']}",
                                             hint="close epics through orch-status")
    else:
        checks["sprint-status"].skip("sprint status not touched" if is_coord else "not the coordination repo")

    # --- merge driver (local clones only; CI never merges) ---
    if ctx.ci:
        checks["merge-driver"].skip("CI run")
    elif sprint_status.attribute_present(ctx.repo) and not sprint_status.driver_registered(ctx.repo):
        checks["merge-driver"].fail("driver-missing", "this clone has no sprint-status merge driver registered",
                                    fix=fix("sprint-status", "install-driver"))
    else:
        checks["merge-driver"].skip("driver registered or not used in this repo")

    result["checks"] = [c.to_dict() for c in checks.values()]
    result["verdict"] = "fail" if any(c.status == "fail" for c in checks.values()) else "pass"
    result["ok"] = result["verdict"] == "pass"
    return result


def is_ci() -> bool:
    return os.environ.get("CI", "").strip().lower() in ("1", "true", "yes")


def _fix_line(f: dict) -> str | None:
    if f.get("fix"):
        cmd = " ".join(f["fix"]["command"])
        pre = f["fix"].get("precondition")
        return f"{pre}, then run: {cmd}" if pre else f"run: {cmd}"
    return f.get("hint")


def render_text(result: dict) -> str:
    tags = {"pass": "PASS", "fail": "FAIL", "warn": "WARN", "skip": "SKIP"}
    who = f"story {result['story']} ({result['subproject'] or '?'})" if result.get("story") else "non-story change"
    lines = [f"orch-gate: {result['verdict'].upper()} - {who} - {result['base']}..{result['head']}"]
    if result.get("head_resolved"):
        lines.append(f"  note: {result['head_resolved']}")
    for n in result.get("notices", []):
        lines.append(f"  note: {n['message']}")
    for c in result["checks"]:
        lines.append(f"  [{tags[c['status']]}] {c['id']}")
        for f in c["findings"]:
            if f["level"] == "info":
                continue
            lines.append(f"      - {f['message']} [{f['code']}]")
            if fx := _fix_line(f):
                lines.append(f"        fix: {fx}")
            if f.get("detail"):
                lines += ["        | " + d for d in f["detail"].splitlines()[:20]]
    return "\n".join(lines)


def render_markdown(result: dict) -> str:
    """CI step summary / PR comment: a status table plus one entry per failing or warning finding."""
    icon = {"pass": "✅", "fail": "❌", "warn": "⚠️", "skip": "➖"}
    who = f"story `{result['story']}` ({result['subproject'] or '?'})" if result.get("story") else "non-story change"
    lines = [f"### orch-gate: {icon[result['verdict']]} {result['verdict'].upper()} — {who}", "",
             f"`{result['base']}` @ `{result['base_sha'][:10]}` ← `{result['head_sha'][:10]}`", ""]
    if result.get("head_resolved"):
        lines += [f"> {result['head_resolved']}", ""]
    lines += ["| Check | Status |", "| --- | --- |"]
    lines += [f"| {c['id']} | {icon[c['status']]} {c['status']} |" for c in result["checks"]]
    for c in result["checks"]:
        for f in c["findings"]:
            if f["level"] == "info":
                continue
            lines += ["", f"**{c['id']}** `{f['code']}`: {f['message']}"]
            if fx := _fix_line(f):
                lines.append(f"- fix: {fx}")
            if f.get("detail"):
                lines += ["", "<details><summary>detail</summary>", "", "```", f["detail"], "```", "", "</details>"]
    return "\n".join(lines) + "\n"
