"""Story claims as git refs `refs/heads/claim/<key>` in the coordination repo.

A claim ref points at an empty-tree commit whose message is JSON. Creation is atomic: locally via
`update-ref <ref> <new> ""` (ref must not exist), remotely via `push --force-with-lease=<ref>:` (same
guarantee on the server). Take-over rewrites the ref with a lease on the old sha, so two people taking
over the same claim cannot both win.
"""

from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

from . import OrchError
from .gitio import EMPTY_TREE, FETCH_TIMEOUT, git, out

PREFIX = "refs/heads/claim/"
MIRROR = "refs/orch/claims/"


def ref_for(key: str) -> str:
    return f"{PREFIX}{key}"


def _identity_env(user: str) -> dict:
    name, _, email = user.partition("<")
    name, email = name.strip() or "orch", email.rstrip(">").strip() or "orch@localhost"
    return {"GIT_AUTHOR_NAME": name, "GIT_AUTHOR_EMAIL": email, "GIT_COMMITTER_NAME": name, "GIT_COMMITTER_EMAIL": email}


def _claim_commit(repo: Path, key: str, user: str, action: str, parent: str | None = None) -> str:
    msg = json.dumps({"orch": "claim", "action": action, "story": key, "user": user}, sort_keys=True)
    args = ["commit-tree", EMPTY_TREE, "-m", msg] + (["-p", parent] if parent else [])
    return out(repo, *args, env=_identity_env(user))


def _sync(repo: Path, remote: str | None, fetch: bool = True) -> str:
    """Refresh the local mirror of remote claims (unless `fetch` is off); return the ref namespace to read."""
    if not remote:
        return PREFIX
    if fetch:
        try:
            git(repo, "fetch", "--quiet", "--prune", remote, f"+{PREFIX}*:{MIRROR}*",
                env={"GIT_TERMINAL_PROMPT": "0"}, timeout=FETCH_TIMEOUT)
        except subprocess.TimeoutExpired:
            raise OrchError(f"no answer from {remote} within {FETCH_TIMEOUT}s while fetching claims") from None
    return MIRROR


def list_claims(repo: Path, remote: str | None = None, now: float | None = None, fetch: bool = True) -> list[dict]:
    """Claims on `remote` (local refs without one); with `fetch` off, as of the last fetch."""
    ns = _sync(repo, remote, fetch)
    now = now if now is not None else time.time()
    fmt = "%(refname)%00%(objectname)%00%(committerdate:unix)%00%(contents)"
    raw = out(repo, "for-each-ref", f"--format={fmt}%01", ns)
    claims = []
    for rec in [r.strip("\n") for r in raw.split("\x01") if r.strip()]:
        ref, sha, ts, body = rec.split("\0", 3)
        key = ref[len(ns):]
        try:
            meta = json.loads(body.strip())
        except json.JSONDecodeError:
            meta = {}
        claims.append({
            "story": key, "sha": sha, "user": meta.get("user"), "action": meta.get("action", "claim"),
            "claimed_at": int(ts), "age_hours": round((now - int(ts)) / 3600, 2),
        })
    return sorted(claims, key=lambda c: c["story"])


def _current(repo: Path, key: str, remote: str | None) -> str | None:
    ns = _sync(repo, remote)
    proc = git(repo, "rev-parse", "--verify", "--quiet", f"{ns}{key}", check=False)
    return proc.stdout.decode().strip() if proc.returncode == 0 else None


def _publish(repo: Path, key: str, new: str | None, expect: str | None, remote: str | None) -> bool:
    ref = ref_for(key)
    if not remote:
        args = ["update-ref", "-d", ref, expect] if new is None else ["update-ref", ref, new, expect or ""]
        return git(repo, *args, check=False).returncode == 0
    lease = f"--force-with-lease={ref}:{expect or ''}"
    refspec = f":{ref}" if new is None else f"{new}:{ref}"
    proc = git(repo, "push", "--quiet", "--porcelain", lease, remote, refspec, check=False)
    return proc.returncode == 0


def create(repo: Path, key: str, user: str, remote: str | None = None) -> dict:
    holder = _current(repo, key, remote)
    if holder:
        return {"ok": False, "reason": "already-claimed", "story": key, "claim": _describe(repo, key, remote)}
    sha = _claim_commit(repo, key, user, "claim")
    if not _publish(repo, key, sha, None, remote):
        return {"ok": False, "reason": "lost-race", "story": key, "claim": _describe(repo, key, remote)}
    _sync(repo, remote)
    return {"ok": True, "story": key, "sha": sha, "user": user}


def take_over(repo: Path, key: str, user: str, expect_sha: str, remote: str | None = None) -> dict:
    """Reassign a claim; `expect_sha` is the claim the caller saw (the stale one) — a lease, not a hint."""
    current = _current(repo, key, remote)
    if current is None:
        return {"ok": False, "reason": "not-claimed", "story": key}
    if current != expect_sha:
        return {"ok": False, "reason": "claim-changed", "story": key, "claim": _describe(repo, key, remote)}
    sha = _claim_commit(repo, key, user, "take-over", parent=current)
    if not _publish(repo, key, sha, current, remote):
        return {"ok": False, "reason": "lost-race", "story": key, "claim": _describe(repo, key, remote)}
    _sync(repo, remote)
    return {"ok": True, "story": key, "sha": sha, "user": user, "previous": current}


def release(repo: Path, key: str, expect_sha: str, remote: str | None = None) -> dict:
    current = _current(repo, key, remote)
    if current is None:
        return {"ok": True, "story": key, "released": False}
    if current != expect_sha:
        return {"ok": False, "reason": "claim-changed", "story": key, "claim": _describe(repo, key, remote)}
    if not _publish(repo, key, None, current, remote):
        return {"ok": False, "reason": "lost-race", "story": key}
    _sync(repo, remote)
    return {"ok": True, "story": key, "released": True}


def _describe(repo: Path, key: str, remote: str | None) -> dict | None:
    for c in list_claims(repo, remote):
        if c["story"] == key:
            return c
    return None


def require_repo(path: Path) -> Path:
    if not (Path(path) / ".git").exists() and git(path, "rev-parse", "--git-dir", check=False).returncode != 0:
        raise OrchError(f"{path} is not a git repository")
    return Path(path)
