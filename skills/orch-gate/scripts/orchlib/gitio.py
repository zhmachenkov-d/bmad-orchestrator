"""Thin git plumbing. Every read goes through a ref so verdicts never depend on the working tree."""

from __future__ import annotations

import hashlib
import os
import re
import subprocess
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from . import OrchError

EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"
# Settings that change git's output format are pinned, so the same commit reads the same on every machine.
PINNED_CONFIG = ("-c", "core.quotePath=false")
# Set by git hooks (a pre-push hook runs with GIT_DIR) and they override -C, so orch would read or fetch into the
# hook's repo instead of the one it names. Every call addresses its repo explicitly, so they are dropped.
REPO_ENV = ("GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR", "GIT_INDEX_FILE", "GIT_OBJECT_DIRECTORY",
            "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_NAMESPACE", "GIT_PREFIX")
FETCH_TIMEOUT = 60


def git_env(extra: dict | None = None) -> dict:
    env = {k: v for k, v in os.environ.items() if k not in REPO_ENV}
    return {**env, **(extra or {})}


def decode(raw: bytes) -> str:
    """git output as str; bytes that are not UTF-8 (a path from another locale) round-trip instead of crashing."""
    return raw.decode("utf-8", "surrogateescape")


def git(repo: Path | str, *args: str, check: bool = True, input: bytes | None = None,
        env: dict | None = None, timeout: float | None = None) -> subprocess.CompletedProcess:
    proc = subprocess.run(
        ["git", *PINNED_CONFIG, "-C", str(repo), *args],
        input=input,
        capture_output=True,
        env=git_env(env),
        timeout=timeout,
    )
    if check and proc.returncode != 0:
        raise OrchError(f"git {' '.join(args)} failed in {repo}: {proc.stderr.decode(errors='replace').strip()}")
    return proc


def out(repo: Path | str, *args: str, **kw) -> str:
    return decode(git(repo, *args, **kw).stdout).strip()


def toplevel(path: Path | str) -> Path:
    return Path(out(path, "rev-parse", "--show-toplevel"))


def ref_exists(repo: Path, ref: str) -> bool:
    return git(repo, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}", check=False).returncode == 0


def default_base(repo: Path, branch: str = "main") -> str:
    for ref in (f"origin/{branch}", branch):
        if ref_exists(repo, ref):
            return ref
    raise OrchError(f"no base ref found in {repo} (tried origin/{branch}, {branch}); pass --base")


def remote_head(repo: Path, remote: str = "origin") -> str | None:
    """The remote's default branch as a local ref (e.g. origin/main), if the clone knows it."""
    proc = git(repo, "symbolic-ref", "-q", "--short", f"refs/remotes/{remote}/HEAD", check=False)
    ref = proc.stdout.decode().strip()
    return ref if proc.returncode == 0 and ref and ref_exists(repo, ref) else None


def merge_base(repo: Path, a: str, b: str) -> str:
    proc = git(repo, "merge-base", a, b, check=False)
    if proc.returncode == 0:
        return proc.stdout.decode().strip()
    if out(repo, "rev-parse", "--is-shallow-repository") == "true":
        raise OrchError(f"shallow clone: no common history between {a} and {b} in {repo}. Fetch full history "
                        "(actions/checkout fetch-depth: 0, GitLab GIT_DEPTH: 0, or git fetch --unshallow)")
    raise OrchError(f"{a} and {b} have no common history in {repo}")


def sha(repo: Path, ref: str) -> str:
    return out(repo, "rev-parse", "--verify", f"{ref}^{{commit}}")


def dirty_paths(repo: Path, ignore_prefix: str = "") -> list[str]:
    """Uncommitted or untracked (not ignored) paths in the working tree."""
    # Not out(): stripping would eat the leading space of the first " M path" status field.
    raw = decode(git(repo, "status", "--porcelain", "-z", "--untracked-files=all").stdout)
    paths, parts = [], raw.split("\0")
    i = 0
    while i < len(parts):
        entry = parts[i]
        i += 1
        if len(entry) < 4:
            continue
        if entry[0] in "RC":
            i += 1  # the next field is the rename source
        path = entry[3:]
        if not (ignore_prefix and path.startswith(ignore_prefix)):
            paths.append(path)
    return paths


def is_ancestor(repo: Path, a: str, b: str) -> bool:
    return git(repo, "merge-base", "--is-ancestor", a, b, check=False).returncode == 0


def resolve_head(repo: Path, base: str, head: str) -> tuple[str, str | None]:
    """Commit to gate for `head`, plus a note when it differs.

    CI systems often check out a synthetic merge of the PR into its target (GitHub's
    refs/pull/N/merge, GitLab merged-results pipelines). Its first parent is already on base, so
    merge-base(base, head) would be the base tip and every "since this branch started" rule would pass
    vacuously. Gate the PR's own tip (the second parent) instead, so a branch-tip checkout and a
    merge-ref checkout get the same verdict.
    """
    commit = sha(repo, head)
    parents = out(repo, "rev-list", "--parents", "-n", "1", commit).split()[1:]
    if len(parents) == 2 and is_ancestor(repo, parents[0], base) and not is_ancestor(repo, parents[1], base):
        return parents[1], f"{head} is a merge of {parents[1][:10]} into {base}; gating the PR tip {parents[1][:10]}"
    return commit, None


class Tree:
    """Read-only view of a repository at one ref."""

    def __init__(self, repo: Path, ref: str):
        self.repo = Path(repo)
        self.ref = ref

    def __repr__(self) -> str:
        return f"Tree({self.repo}@{self.ref})"

    def read(self, path: str) -> bytes | None:
        """File contents; None if absent or a directory (never a tree listing)."""
        proc = git(self.repo, "cat-file", "blob", f"{self.ref}:{path}", check=False)
        return proc.stdout if proc.returncode == 0 else None

    def kind(self, path: str) -> str | None:
        """'blob', 'tree', or None if absent."""
        proc = git(self.repo, "cat-file", "-t", f"{self.ref}:{path}", check=False)
        if proc.returncode != 0:
            return None
        return proc.stdout.decode().strip() or None

    def files(self, path: str) -> dict[str, bytes]:
        """Every file under directory `path`, keyed relative to it; a single file is keyed by its name.

        A submodule (gitlink) reads as git diff prints it, so it compares by the commit it points at.
        """
        kind = self.kind(path)
        if kind == "blob":
            return {path.rstrip("/").rsplit("/", 1)[-1]: self.read(path)}
        if kind != "tree":
            return {}
        root = path.rstrip("/") + "/"
        return {p[len(root):]: self.read(p) if t == "blob" else f"Subproject commit {obj}\n".encode()
                for t, obj, p in self.entries(path)}

    def text(self, path: str) -> str | None:
        data = self.read(path)
        if data is None:
            return None
        try:
            return data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise OrchError(f"{path} at {self.ref} is not UTF-8: {exc}") from exc

    def blob_sha(self, path: str) -> str | None:
        """Blob sha for a file, tree sha for a directory, None if absent."""
        proc = git(self.repo, "rev-parse", "--verify", "--quiet", f"{self.ref}:{path}", check=False)
        if proc.returncode != 0:
            return None
        return proc.stdout.decode().strip() or None

    def exists(self, path: str) -> bool:
        return self.blob_sha(path) is not None

    def entries(self, prefix: str = "") -> list[tuple[str, str, str]]:
        """(type, object sha, path) for every file and submodule under `prefix`."""
        args = ["ls-tree", "-r", "-z", self.ref]
        if prefix:
            args += ["--", prefix.rstrip("/") + "/"]
        found = []
        for rec in decode(git(self.repo, *args).stdout).split("\0"):
            if rec:
                meta, path = rec.split("\t", 1)
                _, type_, obj = meta.split()
                found.append((type_, obj, path))
        return found

    def list(self, prefix: str = "") -> list[str]:
        return [p for _, _, p in self.entries(prefix)]


def changed_files(repo: Path, base: str, head: str) -> list[dict]:
    """Files changed on head since its merge-base with base: [{status, path}] (renames split into D + A)."""
    mb = merge_base(repo, base, head)
    raw = out(repo, "diff", "--name-status", "--no-renames", "-z", mb, head)
    parts = [p for p in raw.split("\0") if p]
    return [{"status": parts[i], "path": parts[i + 1]} for i in range(0, len(parts), 2)]


def normalize_repo(repo: str) -> str:
    """Canonical identity for a repo URL/path: host/org/name, no scheme, user or .git suffix."""
    r = repo.strip()
    if r in (".", ""):
        return "."
    r = re.sub(r"^[a-z+]+://", "", r)
    r = re.sub(r"^[^@/]+@", "", r)
    r = re.sub(r"^([^/:]+):(?!//)", r"\1/", r) if not os.path.isabs(repo) else r
    r = r.rstrip("/")
    if r.endswith(".git"):
        r = r[:-4]
    return r.lower()


def remote_url(repo: Path, remote: str = "origin") -> str | None:
    proc = git(repo, "remote", "get-url", remote, check=False)
    return proc.stdout.decode().strip() if proc.returncode == 0 else None


def cache_dir(repo: Path) -> Path:
    """orch's fetch cache, inside the git dir so it can never be committed; shared by all worktrees."""
    common = Path(out(repo, "rev-parse", "--git-common-dir"))
    return (common if common.is_absolute() else Path(repo) / common) / "orch-cache"


def refresh(repo: Path, remote: str = "origin") -> dict | None:
    """Fetch `remote` so origin/* refs are current; None when there is no such remote.

    Returns {repo, remote, ok} plus, on failure, the error and when the refs were last fetched.
    """
    if remote_url(repo, remote) is None:
        return None
    try:
        proc = git(repo, "fetch", "--quiet", "--no-tags", remote, check=False, env={"GIT_TERMINAL_PROMPT": "0"},
                   timeout=FETCH_TIMEOUT)
        if proc.returncode == 0:
            return {"repo": str(repo), "remote": remote, "ok": True}
        lines = proc.stderr.decode(errors="replace").strip().splitlines()
        error = lines[-1] if lines else ""
    except subprocess.TimeoutExpired:
        error = f"no answer from {remote} within {FETCH_TIMEOUT}s"
    # FETCH_HEAD is per worktree while the refs are shared, so the refs are as fresh as the newest one.
    common = cache_dir(repo).parent
    stamps = [f.stat().st_mtime for f in (common / "FETCH_HEAD", *common.glob("worktrees/*/FETCH_HEAD")) if f.exists()]
    last = datetime.fromtimestamp(max(stamps), timezone.utc).isoformat(timespec="seconds") if stamps else "never"
    return {"repo": str(repo), "remote": remote, "ok": False, "last_fetched": last, "error": error}


def repo_name(repo: Path) -> str:
    """Stable project name: the origin URL's last segment, else the checkout directory's name."""
    url = remote_url(repo)
    return normalize_repo(url).rsplit("/", 1)[-1] if url else Path(repo).resolve().name


def fetch_tree(url: str, branch: str, cache_dir: Path) -> Tree:
    """Shallow-fetch one branch of a remote repo into a bare cache repo and return its Tree."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    bare = cache_dir / (hashlib.sha1(normalize_repo(url).encode()).hexdigest()[:16] + ".git")
    # The cache is shared by every worktree of the clone, so gates running side by side take turns per repo.
    with _locked(bare.with_suffix(".lock")):
        if not bare.exists():
            subprocess.run(["git", "init", "--bare", "-q", str(bare)], check=True, capture_output=True, env=git_env())
        # Holding the lock, no other orch process is inside: git lock files left by an interrupted run are stale.
        for stale in (bare / "shallow.lock", bare / "packed-refs.lock", bare / "config.lock", *bare.glob("refs/**/*.lock")):
            stale.unlink(missing_ok=True)
        git(bare, "fetch", "--depth=1", "--quiet", url, f"+refs/heads/{branch}:refs/heads/{branch}")
    return Tree(bare, f"refs/heads/{branch}")


def branch_tips(repo: Path, prefixes: tuple[str, ...]) -> dict[str, tuple[str, int]]:
    """Branch name (without prefix) -> (sha, committer unix time) for refs under `prefixes`; the newest tip wins."""
    fmt = "%(refname)%00%(objectname)%00%(committerdate:unix)"
    tips: dict[str, tuple[str, int]] = {}
    for prefix in prefixes:
        for line in out(repo, "for-each-ref", f"--format={fmt}", prefix).splitlines():
            ref, obj, ts = line.split("\0")
            name, when = ref[len(prefix):], int(ts or 0)
            if name not in tips or when > tips[name][1]:
                tips[name] = (obj, when)
    return tips


def fetch_branches(url: str, prefix: str, cache_dir: Path) -> dict[str, tuple[str, int]]:
    """Tips of the remote's `refs/heads/<prefix>*` branches, shallow-fetched into the shared bare cache."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    bare = cache_dir / (hashlib.sha1(normalize_repo(url).encode()).hexdigest()[:16] + ".git")
    mirror = f"refs/orch-branches/{prefix}"
    with _locked(bare.with_suffix(".lock")):
        if not bare.exists():
            subprocess.run(["git", "init", "--bare", "-q", str(bare)], check=True, capture_output=True, env=git_env())
        for stale in (bare / "shallow.lock", bare / "packed-refs.lock", bare / "config.lock", *bare.glob("refs/**/*.lock")):
            stale.unlink(missing_ok=True)
        git(bare, "fetch", "--depth=1", "--quiet", "--prune", url, f"+refs/heads/{prefix}*:{mirror}*",
            env={"GIT_TERMINAL_PROMPT": "0"}, timeout=FETCH_TIMEOUT)
        return branch_tips(bare, (mirror,))


@contextmanager
def _locked(path: Path):
    try:
        import fcntl
    except ImportError:  # Windows: no advisory locks; concurrent gates may still collide there
        yield
        return
    with open(path, "a") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)
