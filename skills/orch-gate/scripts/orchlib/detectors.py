"""Breaking-change detector adapters, keyed by registry `contracts.exports[].type`.

Each adapter compares the canonical contract at the PR's merge-base with the PR head. Any non-zero exit
counts as breaking (fail closed): a detector that crashes must not let a change through.
Adding a contract type = one entry in ADAPTERS.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Callable

from .gitio import Tree


@dataclass
class Adapter:
    type: str
    binary: str
    install: str
    kind: str  # "file" compares two files, "dir" runs in the repo against a git base


ADAPTERS = {
    "openapi": Adapter("openapi", "oasdiff", "go install github.com/oasdiff/oasdiff@latest  (or: brew install oasdiff)", "file"),
    "protobuf": Adapter("protobuf", "buf", "https://buf.build/docs/installation  (or: brew install bufbuild/buf/buf)", "file"),
    "asyncapi": Adapter("asyncapi", "asyncapi", "npm install -g @asyncapi/cli", "file"),
    "db-schema": Adapter("db-schema", "atlas", "curl -sSf https://atlasgo.sh | sh", "dir"),
}

Runner = Callable[[list[str], Path | None], subprocess.CompletedProcess]


def default_runner(cmd: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)


def available(type_: str, which: Callable[[str], str | None] = shutil.which) -> bool:
    adapter = ADAPTERS.get(type_)
    return bool(adapter and which(adapter.binary))


def deps(types: set[str], which: Callable[[str], str | None] = shutil.which) -> list[dict]:
    return [{"type": t, "binary": ADAPTERS[t].binary if t in ADAPTERS else None,
             "available": available(t, which), "install": ADAPTERS[t].install if t in ADAPTERS else None}
            for t in sorted(types)]


def run(type_: str, canonical: str, base: Tree, head: Tree, repo: Path, base_ref: str, extra: dict,
        runner: Runner = default_runner, which: Callable[[str], str | None] = shutil.which) -> dict:
    """Return {status: ok|breaking|missing|added|removed, output}."""
    adapter = ADAPTERS.get(type_)
    if adapter is None:
        return {"status": "missing", "output": f"no detector adapter for contract type '{type_}'"}
    if not which(adapter.binary):
        return {"status": "missing", "output": f"'{adapter.binary}' not found; install: {adapter.install}"}
    if adapter.kind == "dir":
        return _db_schema(canonical, repo, base_ref, extra, runner)
    old, new = base.read(canonical), head.read(canonical)
    if new is None:
        return {"status": "removed", "output": f"{canonical} deleted"}
    if old is None:
        return {"status": "added", "output": f"{canonical} is new"}
    name = PurePosixPath(canonical).name
    with tempfile.TemporaryDirectory(prefix="orch-detect-") as tmp:
        b, h = Path(tmp, "base"), Path(tmp, "head")
        b.mkdir()
        h.mkdir()
        (b / name).write_bytes(old)
        (h / name).write_bytes(new)
        cmd = {
            "openapi": ["oasdiff", "breaking", str(b / name), str(h / name), "--fail-on", "ERR"],
            "protobuf": ["buf", "breaking", str(h), "--against", str(b)],
            "asyncapi": ["asyncapi", "diff", str(b / name), str(h / name), "--type", "breaking", "--format", "json"],
        }[type_]
        proc = runner(cmd, Path(tmp))
    return _verdict(proc)


def _db_schema(canonical: str, repo: Path, base_ref: str, extra: dict, runner: Runner) -> dict:
    dev_url = extra.get("dev_url") or os.environ.get("ORCH_ATLAS_DEV_URL")
    if not dev_url:
        return {"status": "breaking", "output": "db-schema contracts need 'dev_url' in the registry export or ORCH_ATLAS_DEV_URL"}
    cmd = ["atlas", "migrate", "lint", "--dir", f"file://{canonical}", "--dev-url", dev_url, "--git-base", base_ref]
    return _verdict(runner(cmd, repo))


def _verdict(proc: subprocess.CompletedProcess) -> dict:
    output = ((proc.stdout or "") + (proc.stderr or "")).strip()
    return {"status": "ok" if proc.returncode == 0 else "breaking", "output": output[-4000:]}
