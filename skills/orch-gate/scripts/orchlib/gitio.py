"""Thin git plumbing. Every read goes through a ref so verdicts never depend on the working tree."""

from __future__ import annotations

import hashlib
import os
import re
import subprocess
from pathlib import Path

from . import OrchError

EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"


def git(repo: Path | str, *args: str, check: bool = True, input: bytes | None = None,
        env: dict | None = None) -> subprocess.CompletedProcess:
    proc = subprocess.run(
        ["git", "-C", str(repo), *args],
        input=input,
        capture_output=True,
        env={**os.environ, **(env or {})},
    )
    if check and proc.returncode != 0:
        raise OrchError(f"git {' '.join(args)} failed in {repo}: {proc.stderr.decode(errors='replace').strip()}")
    return proc


def out(repo: Path | str, *args: str, **kw) -> str:
    return git(repo, *args, **kw).stdout.decode().strip()


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
    raw = out(repo, "status", "--porcelain", "-z", "--untracked-files=all")
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
        proc = git(self.repo, "show", f"{self.ref}:{path}", check=False)
        return proc.stdout if proc.returncode == 0 else None

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

    def list(self, prefix: str = "") -> list[str]:
        args = ["ls-tree", "-r", "--name-only", self.ref]
        if prefix:
            args += ["--", prefix.rstrip("/") + "/"]
        return [line for line in out(self.repo, *args).splitlines() if line]


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


def fetch_tree(url: str, branch: str, cache_dir: Path) -> Tree:
    """Shallow-fetch one branch of a remote repo into a bare cache repo and return its Tree."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    bare = cache_dir / (hashlib.sha1(normalize_repo(url).encode()).hexdigest()[:16] + ".git")
    if not bare.exists():
        subprocess.run(["git", "init", "--bare", "-q", str(bare)], check=True, capture_output=True)
    git(bare, "fetch", "--depth=1", "--quiet", url, f"+refs/heads/{branch}:refs/heads/{branch}")
    return Tree(bare, f"refs/heads/{branch}")
