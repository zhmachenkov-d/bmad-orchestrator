#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["pyyaml>=6.0"]
# ///
"""orch — the shared orch library CLI. Every orch skill and CI job calls this; output is JSON on stdout.

Other orch skills install beside orch-gate and call `uv run <their skill dir>/../orch-gate/scripts/orch.py <command>`
instead of reimplementing any of this.

Exit codes: 0 = ok/pass, 1 = failing verdict / lost race / validation issues, 2 = usage or environment error.

Repositories: --repo is the repo being acted on (default: current directory). --coord is the coordination
repo holding the registry, epics, contracts, claims and sprint status (default: $ORCH_COORD, else the local path
in this repo's committed orch_coordination_repo, else --repo, i.e. a monorepo). Reads always go through git refs,
never the working tree.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from orchlib import OrchError, SCHEMA_VERSION  # noqa: E402
from orchlib import claims, close, config, detectors, gate, gitio, markers, plan, registry, report, sprint_status, stories, work  # noqa: E402
from orchlib import status as status_mod  # noqa: E402
from orchlib.stories import key_from_any  # noqa: E402


class Env:
    def __init__(self, args):
        self.repo = gitio.toplevel(args.repo or os.getcwd())
        # A local gate refreshes origin/* before reading anything, so it sees the main CI will see.
        # CI checkouts are fresh already, and --offline means no network.
        self.refs_fetched = []
        refresh = (args.cmd in REFRESHING and not getattr(args, "ci", False) and not gate.ci_source()
                   and not args.offline)
        if refresh and (r := gitio.refresh(self.repo)):
            self.refs_fetched.append(r)
        coord = args.coord or os.environ.get("ORCH_COORD") or _configured_coord(self.repo)
        self.coord_root = gitio.toplevel(coord) if coord else self.repo
        self.same = self.coord_root.resolve() == self.repo.resolve()
        if refresh and not self.same and (r := gitio.refresh(self.coord_root)):
            self.refs_fetched.append(r)
        # Config, registry and epics are read at one trusted ref; in a monorepo that is the gate's --base.
        explicit = args.coord_ref or (getattr(args, "base", None) if self.same else None)
        try:
            self.cfg, self.coord_ref, self.coord_ref_source = config.resolve(self.coord_root, explicit)
        except OrchError:
            if self.same:
                raise
            # CI checkouts of the coordination repo are often a detached main
            self.coord_ref, self.coord_ref_source = "HEAD", "checked-out HEAD (no main ref found)"
            self.cfg = config.load(gitio.Tree(self.coord_root, self.coord_ref))
        self.coord = gitio.Tree(self.coord_root, self.coord_ref)
        self.cache_root = gitio.cache_dir(self.repo)

    def registry(self):
        return registry.load(self.coord, self.cfg)

    def stories(self, reg=None):
        return stories.load(self.coord, self.cfg, reg if reg is not None else self.registry())


def _configured_coord(repo: Path) -> str | None:
    """Coordination repo named by this repo's committed `orch_coordination_repo`; None means this repo."""
    try:
        value = config.resolve(repo)[0].coordination_repo.strip()
    except OrchError:
        return None  # no trusted ref or no config here: treat as a monorepo
    if value in ("", "."):
        return None
    if gitio.normalize_repo(value) == gitio.normalize_repo(gitio.remote_url(repo) or ""):
        return None  # the coordination repo names itself
    path = Path(value).expanduser()
    path = path if path.is_absolute() else repo / path
    if path.is_dir():
        return str(path)
    raise OrchError(f"this repo's orch_coordination_repo is {value!r}, which is not a local checkout; "
                    "clone it and pass --coord <path> or set ORCH_COORD", "coord-not-local", repo=value)


def emit(payload: dict, code: int = 0, out: str | None = None) -> int:
    text = json.dumps({"schema": SCHEMA_VERSION, **payload}, indent=2, default=str)
    if out:
        Path(out).write_text(text + "\n", encoding="utf-8")
    print(text)
    return code


def need_key(value: str) -> str:
    key = key_from_any(value)
    if not key:
        raise OrchError(f"'{value}' is not a story id (N.M, N-M or a sprint key)", "bad-story-id")
    return key


# ---- commands ----

def cmd_config(args, env: Env):
    # User settings resolve from this checkout's working tree (personal layers included); the gate never reads them.
    cfg = {**env.cfg.to_dict(), **config.user_settings(env.coord_root, env.cfg)}
    return emit({"ok": True, "repo": str(env.repo), "coord": str(env.coord_root), "coord_ref": env.coord_ref,
                 "coord_ref_source": env.coord_ref_source, "config": cfg})


def cmd_registry(args, env: Env):
    reg = env.registry()
    issues = registry.validate(reg, env.coord, env.cfg)
    return emit({"ok": not issues, "subprojects": reg.to_dict(), "issues": issues}, 1 if issues else 0)


def cmd_stories(args, env: Env):
    s = env.stories()
    return emit({"ok": not s.issues, **s.to_dict()}, 1 if s.issues else 0)


def cmd_merged(args, env: Env):
    reg = env.registry()
    m, unread = markers.merged(reg, env.coord, env.cache_root, offline=args.offline)
    return emit({"ok": True, "merged": markers.public(m), "unread_repos": unread})


def cmd_deps(args, env: Env):
    reg = env.registry()
    types = {e.type for s in reg.values() for e in s.exports}
    report = detectors.deps(types, probe=args.probe)
    bad = [d for d in report if not d["available"] or d.get("probe", {}).get("status") == "fail"]
    return emit({"ok": not bad, "detectors": report}, 1 if bad else 0)


def cmd_marker(args, env: Env):
    reg = env.registry()
    st = env.stories(reg)
    key = need_key(args.story)
    story = st.get(key)
    if story is None:
        raise OrchError(f"story {key} not found in the epics on {env.coord_ref}", "story-not-found")
    changed = []
    if story.subproject == registry.CONTRACTS:
        base = args.base or gitio.default_base(env.repo, env.cfg.main_branch)
        changed = [c["path"] for c in gitio.changed_files(env.repo, base, "HEAD")]
    marker = markers.compute_pins(reg, story, env.coord, env.cfg, gitio.Tree(env.repo, "HEAD"), changed)
    path = None if args.dry_run else str(markers.write(env.repo, marker))
    return emit({"ok": True, "marker": marker, "path": path, "yaml": markers.render(marker)})


def cmd_claim(args, env: Env):
    remote = None if args.local else (args.remote or ("origin" if gitio.remote_url(env.coord_root, "origin") else None))
    repo = env.coord_root
    if args.action == "list":
        return emit({"ok": True, "remote": remote,
                     "claims": claims.list_claims(repo, remote, fetch=not args.offline)})
    if args.offline:
        # a claim only counts once pushed; --local is the explicit no-remote mode
        raise OrchError(f"claim {args.action} needs the remote; drop --offline (or use --local for a repo without one)",
                        "offline-claim")
    key = need_key(args.story or "")
    user = args.user or _git_user(repo)
    if args.action == "create":
        res = claims.create(repo, key, user, remote)
    elif args.action == "take-over":
        if not args.expect:
            raise OrchError("take-over needs --expect <sha of the claim you saw>")
        res = claims.take_over(repo, key, user, args.expect, remote)
    else:
        if not args.expect:
            raise OrchError("release needs --expect <sha of the claim>")
        res = claims.release(repo, key, args.expect, remote)
    return emit({**res, "remote": remote}, 0 if res["ok"] else 1)


def _git_user(repo: Path) -> str:
    name = gitio.git(repo, "config", "user.name", check=False).stdout.decode().strip()
    email = gitio.git(repo, "config", "user.email", check=False).stdout.decode().strip()
    if not name:
        raise OrchError("no git user.name configured; pass --user 'Name <email>'", "no-git-user")
    return f"{name} <{email}>" if email else name


def cmd_sprint_status(args, env: Env | None):
    if args.action == "merge":
        # git merge driver: %O %A %B; result goes to %A. Exit non-zero leaves a conflict for the user.
        base, ours, theirs = (Path(p).read_text(encoding="utf-8") for p in (args.base_file, args.ours, args.theirs))
        merged_text = sprint_status.merge(base, ours, theirs)
        if merged_text is None:
            print(json.dumps({"ok": False, "reason": "non-status sections conflict"}), file=sys.stderr)
            return 1
        Path(args.ours).write_text(merged_text, encoding="utf-8")
        return 0
    if args.action == "install-driver":
        res = sprint_status.install_driver(env.repo, env.cfg.sprint_status, Path(__file__))
        return emit({"ok": True, **res})
    tree = gitio.Tree(env.coord_root, args.ref) if args.ref else None
    path = env.coord_root / env.cfg.sprint_status
    text = tree.text(env.cfg.sprint_status) if tree else (path.read_text(encoding="utf-8") if path.exists() else None)
    if text is None:
        raise OrchError(f"{env.cfg.sprint_status} not found")
    reg = env.registry()
    m, unread = markers.merged(reg, env.coord, env.cache_root, offline=args.offline)
    closed = markers.closed_epics(env.coord, env.cfg)
    if args.action == "check":
        res = sprint_status.check(text, set(m), closed, markers.unknown_keys(env.stories(reg), unread))
        return emit({"ok": not res["fail"], **res, "unread_repos": unread}, 1 if res["fail"] else 0)
    if unread:
        # derive would demote stories whose markers it could not see
        raise OrchError("cannot derive sprint status while registry repos are unread: "
                        + "; ".join(u["message"] for u in unread))
    new_text, changes = sprint_status.derive(text, set(m), closed)
    if args.write and changes:
        path.write_text(new_text, encoding="utf-8")
    return emit({"ok": True, "changes": changes, "written": bool(args.write and changes)})


def cmd_epic(args, env: Env):
    reg = env.registry()
    st = env.stories(reg)
    m, unread = markers.merged(reg, env.coord, env.cache_root, offline=args.offline)
    if args.action == "close":
        p = close.plan(args.epic, st, reg, env.coord, env.cfg, m, unread)
        res = {"ok": p["pass"] not in ("blocked",), **close.public(p)}
        if args.push and args.expect_pass and p["pass"] != args.expect_pass:
            # state moved since the user confirmed the plan: show the new one instead of pushing it
            return emit({**res, "ok": False, "code": "pass-changed", "expected_pass": args.expect_pass}, 1)
        if args.push and p["pass"] in ("archive", "record"):
            snap = _status(args, env, reg, st, args.epic) if p["pass"] == "record" else None
            res["published"] = close.execute(p, env.coord_root, env.coord, env.cfg, m, st,
                                             args.user or _git_user(env.coord_root), snap)
        return emit(res, 0 if res["ok"] else 1)
    problems = markers.close_check(args.epic, st, reg, m, env.coord, markers.unknown_keys(st, unread))
    return emit({"ok": not problems, "epic": args.epic, "problems": problems, "unread_repos": unread},
                1 if problems else 0)


def _claims_remote(env: Env) -> str | None:
    return "origin" if gitio.remote_url(env.coord_root, "origin") else None


def _status(args, env: Env, reg, st, epic: int | None = None) -> dict:
    if epic is not None and not any(s.epic == epic for s in st.values()):
        epics = ", ".join(str(e) for e in sorted({s.epic for s in st.values()})) or "none"
        raise OrchError(f"epic {epic} has no stories in the epics on {env.coord_ref} (epics: {epics})")
    return status_mod.build(reg, st, env.coord_root, env.coord, env.cfg, env.cache_root, remote=_claims_remote(env),
                            offline=args.offline, host=not getattr(args, "no_host", False),
                            local_run=not gate.ci_source(), epic=epic)


def cmd_status(args, env: Env):
    reg = env.registry()
    st = env.stories(reg)
    res = _status(args, env, reg, st, args.epic)
    return emit({"ok": True, "coord_ref": env.coord_ref, "config": {"stale_claim_hours": env.cfg.stale_claim_hours,
                 "review_wait_hours": env.cfg.review_wait_hours}, "plan_issues": st.issues, **res})


def cmd_plan_check(args, env: Env):
    reg = env.registry()
    issues = registry.validate(reg, env.coord, env.cfg)
    res = plan.check(env.stories(reg), reg, issues)
    return emit({"ok": res["verdict"] != "FAIL", "registry_issues": issues, **res}, 1 if res["verdict"] == "FAIL" else 0)


def cmd_next(args, env: Env):
    reg = env.registry()
    st = env.stories(reg)
    snap = _status(args, env, reg, st)
    user = args.user or _git_user(env.coord_root)
    return emit({"ok": True, "coord_ref": env.coord_ref, **work.pick(snap, st, reg, user), "plan_issues": st.issues})


def cmd_worktree(args, env: Env):
    reg = env.registry()
    st = env.stories(reg)
    key = need_key(args.story)
    s, sub = work.story_subproject(reg, st, key)
    clone = work.local_clone(sub, env.repo, env.coord_root)
    if clone is None:
        raise OrchError(f"story {s.id} ({sub.name}) lives in {sub.repo}; run from a local clone of it "
                        "or pass --repo <local clone> with --coord <coordination repo>", "not-a-clone", repo=sub.repo)
    user = args.user or _git_user(env.coord_root)
    claim = next((c for c in claims.list_claims(env.coord_root, _claims_remote(env), fetch=not args.offline)
                  if c["story"] == key), None)
    if not claim or not work.same_user(claim["user"], user):
        # the worktree is for the claimant: claim first, or take over a stale claim
        return emit({"ok": False, "reason": "claimed-by-other" if claim else "not-claimed", "story": key,
                     "claim": claim, "user": user}, 1)
    path = Path(args.path) if args.path else work.default_path(
        env.coord_root, config.user_settings(env.coord_root, env.cfg)["worktrees_dir"], key)
    wt = work.prepare(clone, key, sub.branch, path)
    ctx = work.context(reg, st, key, env.coord, env.cfg, gitio.sha(env.coord_root, env.coord_ref))
    written = work.write(Path(wt["path"]), ctx, env.coord, snapshot=clone.resolve() != env.coord_root.resolve())
    return emit({"ok": True, "story": key, "subproject": sub.name, "repo": sub.repo, "clone": str(clone),
                 "claim": claim, "worktree": wt, "context": written, "plan_issues": ctx["plan_issues"]})


def cmd_context(args, env: Env):
    reg = env.registry()
    st = env.stories(reg)
    if args.story:
        key = need_key(args.story)
    else:
        branch = gitio.out(env.repo, "symbolic-ref", "--quiet", "--short", "HEAD", check=False)
        key = key_from_any(branch.removeprefix(status_mod.STORY_BRANCH)) if branch.startswith(status_mod.STORY_BRANCH) else None
        if not key:
            raise OrchError(f"not on a story/<N-M> branch ({branch or 'detached HEAD'}); pass --story", "not-story-branch")
    ctx = work.context(reg, st, key, env.coord, env.cfg, gitio.sha(env.coord_root, env.coord_ref))
    written = None
    if args.write:
        work.exclude_context(env.repo)
        written = work.write(env.repo, ctx, env.coord, snapshot=not env.same)
    return emit({"ok": True, "context": ctx, "written": written})


def cmd_report(args, env: Env):
    reg = env.registry()
    st = env.stories(reg)
    res = _status(args, env, reg, st, args.epic)
    text = report.render(args.epic, res, st, env.cfg.project_name)
    stamp = res["now"][:16].replace(":", "").replace("-", "").replace("T", "-")
    path = Path(args.output) if args.output else (
        env.coord_root / env.cfg.implementation_artifacts / "orch" / "reports" / f"epic-{args.epic}-{stamp}.html")
    pdf_path = None
    if path.suffix.lower() == ".pdf":  # -o names the PDF: the HTML goes beside it
        pdf_path, path, args.pdf = path, path.with_suffix(".html"), True
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    out = {"ok": True, "html": str(path)}
    if args.pdf:
        pdf, why = report.to_pdf(path, pdf_path)
        out.update({"pdf": str(pdf) if pdf else None, "pdf_error": why})
    return emit(out)


def _gate_base(args, env: Env, reg, repo_id: str) -> tuple[str, str]:
    """The base ref and where it came from. A code repo defaults to its registry `branch`, which merged() reads too."""
    if args.base:
        return args.base, "explicit"
    if not env.same:
        subs = registry.subprojects_in(reg, repo_id)
        branches = sorted({s.branch for s in subs})
        if len(branches) > 1:
            raise OrchError(f"subprojects in this repo name different branches ({', '.join(branches)}); pass --base")
        if branches:
            return gitio.default_base(env.repo, branches[0]), f"registry branch of {', '.join(s.name for s in subs)}"
    return gitio.default_base(env.repo, env.cfg.main_branch), "orch_main_branch"


def _portable(path: Path, repo: Path) -> str:
    """Path as a fix command should print it: relative to the repo root when inside or beside it, else absolute."""
    path = path.resolve()
    rel = os.path.relpath(path, repo.resolve())
    return rel if not rel.startswith(os.pardir + os.sep + os.pardir) else str(path)


def cmd_gate(args, env: Env):
    source = gate.ci_source()
    ci = args.ci or bool(source)
    if ci and args.offline:
        raise OrchError("--offline is not allowed in CI: the verdict would depend on which repos were skipped")
    repo_id = "." if env.same else gitio.normalize_repo(args.repo_id or gitio.remote_url(env.repo) or str(env.repo))
    coord = env.coord
    reg = registry.load(coord, env.cfg)
    base, base_source = _gate_base(args, env, reg, repo_id)
    flags = []
    if args.coord:
        flags += ["--coord", _portable(Path(args.coord), env.repo)]
    if args.coord_ref:
        flags += ["--coord-ref", args.coord_ref]
    ctx = gate.Context(
        repo=env.repo, base=base, head=args.head, repo_id=repo_id, coord=coord, cfg=env.cfg, reg=reg,
        stories=stories.load(coord, env.cfg, reg), cache_root=env.cache_root,
        ci=ci, ci_source="--ci" if args.ci else source, offline=args.offline, refs_fetched=env.refs_fetched,
        base_source=base_source, coord_ref_source=env.coord_ref_source,
        fix_prefix=["uv", "run", _portable(Path(__file__), env.repo)], fix_flags=flags, fix_base=args.base,
    )
    result = gate.run(ctx)
    code = 0 if result["ok"] else 1
    if args.format in ("text", "markdown"):
        if args.output:
            Path(args.output).write_text(json.dumps({"schema": SCHEMA_VERSION, **result}, indent=2) + "\n", encoding="utf-8")
        print(gate.render_text(result) if args.format == "text" else gate.render_markdown(result))
        return code
    return emit(result, code, args.output)


# ---- parser ----

class JsonParser(argparse.ArgumentParser):
    def error(self, message):
        print(json.dumps({"ok": False, "error": message}))
        sys.exit(2)


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--repo", help="repo to act on (default: cwd)")
    common.add_argument("--coord", help="coordination repo path (default: $ORCH_COORD, else orch_coordination_repo if a local path, else --repo)")
    common.add_argument("--coord-ref", help="ref of the coordination repo to read (default: origin/<main> or <main>)")
    common.add_argument("--offline", action="store_true", help="do not fetch other registry repos; their merge status is skipped with a warning")
    common.add_argument("--verbose", action="store_true")

    p = JsonParser(prog="orch.py", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True, parser_class=JsonParser)

    sub.add_parser("config", parents=[common], help="resolved orch config")
    sub.add_parser("registry", parents=[common], help="load + validate the subproject registry")
    sub.add_parser("stories", parents=[common], help="parse epics into stories (subproject, depends_on, contract change) + plan issues")
    sub.add_parser("merged", parents=[common], help="merged story markers across all registry repos (pull-based, cached under the git dir)")
    d = sub.add_parser("deps", parents=[common], help="contract detector availability and versions for types used in the registry")
    d.add_argument("--probe", action="store_true",
                   help="also run each installed detector on built-in compatible/breaking fixtures and check its verdicts")

    m = sub.add_parser("marker", parents=[common], help="write .orch/stories/<key>.yaml with current pins (commit story changes first)")
    m.add_argument("action", choices=["write"])
    m.add_argument("--story", required=True)
    m.add_argument("--base", help="base ref for contract stories' changed files (default: origin/main)")
    m.add_argument("--dry-run", action="store_true")

    c = sub.add_parser("claim", parents=[common], help="story claims as refs/heads/claim/<key> in the coordination repo")
    c.add_argument("action", choices=["list", "create", "take-over", "release"])
    c.add_argument("--story")
    c.add_argument("--user", help="'Name <email>' (default: git config)")
    c.add_argument("--expect", help="claim sha the caller saw (take-over / release lease)")
    c.add_argument("--remote", help="remote holding claims (default: origin if present)")
    c.add_argument("--local", action="store_true", help="use local refs only (no remote)")

    s = sub.add_parser("sprint-status", parents=[common], help="done-invariant check, derive, merge driver")
    s.add_argument("action", choices=["check", "derive", "merge", "install-driver"])
    s.add_argument("files", nargs="*", help="merge only: %%O %%A %%B")
    s.add_argument("--write", action="store_true", help="derive: write the file")
    s.add_argument("--ref", help="check/derive: read sprint-status at this ref instead of the working tree")

    e = sub.add_parser("epic", parents=[common], help="epic close check: all stories merged, markers archived, pins converged")
    e.add_argument("action", choices=["close-check", "close"])
    e.add_argument("--epic", type=int, required=True)
    e.add_argument("--push", action="store_true", help="close: build and push the pass's branches (default: plan only)")
    e.add_argument("--user", help="close: 'Name <email>' for the commits (default: git config)")
    e.add_argument("--expect-pass", choices=["archive", "record"],
                   help="close --push: the pass the user confirmed; push nothing if the plan now differs")

    st = sub.add_parser("status", parents=[common], help="epics and stories across subprojects, with anomalies and actions")
    st.add_argument("--epic", type=int)
    st.add_argument("--no-host", action="store_true", help="do not ask gh/glab for open reviews")

    sub.add_parser("plan-check", parents=[common], help="plan validation: PASS / CONCERNS / FAIL over epics and registry")

    nx = sub.add_parser("next", parents=[common], help="warnings, your open stories and the ready stories ranked by what they unblock")
    nx.add_argument("--user", help="'Name <email>' whose open claims to list (default: git config)")
    nx.add_argument("--no-host", action="store_true", help="do not ask gh/glab for open reviews")

    wt = sub.add_parser("worktree", parents=[common], help="story/<N-M> worktree in the story's repo, with its node context; needs your claim")
    wt.add_argument("--story", required=True)
    wt.add_argument("--user", help="'Name <email>' holding the claim (default: git config)")
    wt.add_argument("--path", help="worktree directory (default: <orch_worktrees_dir>/story-<N-M>)")

    cx = sub.add_parser("context", parents=[common], help="node context of a story: boundaries, contracts, pins, how to finish")
    cx.add_argument("--story", help="default: the story of the checked-out story/<N-M> branch")
    cx.add_argument("--write", action="store_true", help=f"write it to {work.CONTEXT_DIR}/ in --repo (kept out of commits)")

    rp = sub.add_parser("report", parents=[common], help="self-contained HTML epic report (optionally PDF)")
    rp.add_argument("--epic", type=int, required=True)
    rp.add_argument("--pdf", action="store_true", help="also print it to PDF with headless Chromium/Chrome")
    rp.add_argument("--no-host", action="store_true", help="do not ask gh/glab for open reviews")
    rp.add_argument("-o", "--output", help="HTML path, or a .pdf path to get the PDF there and the HTML beside it (default: {implementation_artifacts}/orch/reports/epic-N-<stamp>.html)")

    g = sub.add_parser("gate", parents=[common], help="pre-merge verdict for the current branch/PR")
    g.add_argument("--base", help="target branch ref (default: origin/main or main)")
    g.add_argument("--head", default="HEAD",
                   help="commit to gate; a CI merge ref (the PR merged into base) is resolved to the PR tip. Needs full history (fetch-depth 0)")
    g.add_argument("--repo-id", help="this repo's identity as written in the registry (default: origin URL)")
    g.add_argument("--ci", action="store_true",
                   help="CI mode (also implied by CI, GITHUB_ACTIONS, GITLAB_CI, BUILDKITE, JENKINS_URL, TF_BUILD or "
                        "TEAMCITY_VERSION); local runs fetch origin first, CI runs do not")
    g.add_argument("--format", choices=["json", "text", "markdown"], default="json",
                   help="markdown suits $GITHUB_STEP_SUMMARY or a PR comment; -o still writes the JSON")
    g.add_argument("-o", "--output", help="also write the JSON result to this file")
    return p


COMMANDS = {"config": cmd_config, "registry": cmd_registry, "stories": cmd_stories, "merged": cmd_merged,
            "deps": cmd_deps, "marker": cmd_marker, "claim": cmd_claim, "sprint-status": cmd_sprint_status,
            "epic": cmd_epic, "gate": cmd_gate, "status": cmd_status, "plan-check": cmd_plan_check,
            "report": cmd_report, "next": cmd_next, "worktree": cmd_worktree, "context": cmd_context}
# Commands that read what others pushed refresh origin first on local runs, as the gate does.
REFRESHING = {"gate", "status", "report", "epic", "next", "worktree", "context"}


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="backslashreplace")  # a non-UTF-8 path in a message must not crash the report
    try:
        if args.cmd == "sprint-status" and args.action == "merge":
            if len(args.files) != 3:
                raise OrchError("merge needs %O %A %B")
            args.base_file, args.ours, args.theirs = args.files
            return cmd_sprint_status(args, None)
        return COMMANDS[args.cmd](args, Env(args))
    except OrchError as exc:
        coded = {"code": exc.code, **exc.fields} if exc.code else {}
        return emit({"ok": False, "error": str(exc), **coded}, 2)
    except Exception as exc:  # never let a crash look like a failing verdict (exit 1)
        if args.verbose:
            traceback.print_exc()
        return emit({"ok": False, "error": f"internal error: {type(exc).__name__}: {exc}",
                     "hint": "re-run with --verbose for the traceback"}, 2)


if __name__ == "__main__":
    sys.exit(main())
