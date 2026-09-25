"""Breaking-change detector adapters, keyed by registry `contracts.exports[].type`.

Each adapter compares the canonical contract at the PR's merge-base with the PR head. Any non-zero exit
counts as breaking (fail closed): a detector that crashes must not let a change through.
Adding a contract type = one entry in ADAPTERS (plus a PROBES pair so `deps --probe` can verify it).
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Callable

from .gitio import Tree, git


@dataclass
class Adapter:
    type: str
    binary: str
    install: str
    kind: str  # "file" compares two files, "dir" runs in a checkout of the head against a git base
    version_cmd: tuple[str, ...]


ADAPTERS = {
    "openapi": Adapter("openapi", "oasdiff", "go install github.com/oasdiff/oasdiff@<version>  (or: brew install oasdiff)",
                       "file", ("oasdiff", "--version")),
    "protobuf": Adapter("protobuf", "buf", "https://buf.build/docs/installation  (or: brew install bufbuild/buf/buf)",
                        "file", ("buf", "--version")),
    "asyncapi": Adapter("asyncapi", "asyncapi", "npm install -g @asyncapi/cli@<version>", "file", ("asyncapi", "--version")),
    "db-schema": Adapter("db-schema", "atlas", "curl -sSf https://atlasgo.sh | ATLAS_VERSION=<version> sh", "dir",
                         ("atlas", "version")),
}
PIN_NOTE = "install the same <version> locally and in the CI image; detector rule sets change between releases"

Runner = Callable[[list[str], Path | None], subprocess.CompletedProcess]


def default_runner(cmd: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)


def available(type_: str, which: Callable[[str], str | None] = shutil.which) -> bool:
    adapter = ADAPTERS.get(type_)
    return bool(adapter and which(adapter.binary))


def version(type_: str, runner: Runner = default_runner, which: Callable[[str], str | None] = shutil.which) -> dict:
    """{binary, version} of the detector for a type, so two runs can be compared; version None if unknown."""
    adapter = ADAPTERS.get(type_)
    if adapter is None or not which(adapter.binary):
        return {"binary": adapter.binary if adapter else None, "version": None}
    try:
        proc = runner(list(adapter.version_cmd), None)
        text = ((proc.stdout or "") + (proc.stderr or "")).strip().splitlines()
        return {"binary": adapter.binary, "version": text[0].strip() if proc.returncode == 0 and text else None}
    except OSError:
        return {"binary": adapter.binary, "version": None}


def deps(types: set[str], runner: Runner = default_runner, which: Callable[[str], str | None] = shutil.which,
         probe: bool = False) -> list[dict]:
    report = []
    for t in sorted(types):
        adapter = ADAPTERS.get(t)
        entry = {"type": t, "binary": adapter.binary if adapter else None, "available": available(t, which),
                 "install": adapter.install if adapter else None, "pin": PIN_NOTE if adapter else None}
        if entry["available"]:
            entry["version"] = version(t, runner, which)["version"]
            if probe:
                entry["probe"] = run_probe(t, runner, which)
        report.append(entry)
    return report


def run(type_: str, canonical: str, base: Tree, head: Tree, repo: Path, base_ref: str, extra: dict,
        runner: Runner = default_runner, which: Callable[[str], str | None] = shutil.which) -> dict:
    """Return {status: ok|breaking|missing|added|removed, output, inputs?}."""
    adapter = ADAPTERS.get(type_)
    if adapter is None:
        return {"status": "missing", "output": f"no detector adapter for contract type '{type_}'"}
    if not which(adapter.binary):
        return {"status": "missing", "output": f"'{adapter.binary}' not found; install: {adapter.install}; {PIN_NOTE}"}
    if adapter.kind == "dir":
        return _db_schema(canonical, repo, head.ref, base_ref, extra, runner)
    old, new = base.read(canonical), head.read(canonical)
    if new is None:
        return {"status": "removed", "output": f"{canonical} deleted"}
    if old is None:
        return {"status": "added", "output": f"{canonical} is new"}
    return compare_files(type_, PurePosixPath(canonical).name, old, new, runner)


def compare_files(type_: str, name: str, old: bytes, new: bytes, runner: Runner = default_runner) -> dict:
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


def _db_schema(canonical: str, repo: Path, head_ref: str, base_ref: str, extra: dict, runner: Runner) -> dict:
    if extra.get("dev_url"):
        dev_url, source = extra["dev_url"], "registry"
    elif os.environ.get("ORCH_ATLAS_DEV_URL"):
        dev_url, source = os.environ["ORCH_ATLAS_DEV_URL"], "env ORCH_ATLAS_DEV_URL"
    else:
        return {"status": "breaking", "output": "db-schema contracts need 'dev_url' in the registry export or ORCH_ATLAS_DEV_URL"}
    # Lint the committed head, not the working tree: a detached worktree shares refs, so --git-base still resolves.
    with tempfile.TemporaryDirectory(prefix="orch-atlas-") as tmp:
        wt = Path(tmp, "head")
        git(repo, "worktree", "add", "--detach", "--quiet", str(wt), head_ref)
        try:
            cmd = ["atlas", "migrate", "lint", "--dir", f"file://{canonical}", "--dev-url", dev_url, "--git-base", base_ref]
            res = _verdict(runner(cmd, wt))
        finally:
            git(repo, "worktree", "remove", "--force", str(wt), check=False)
    return {**res, "inputs": {"dev_url_source": source}}


def _verdict(proc: subprocess.CompletedProcess) -> dict:
    output = ((proc.stdout or "") + (proc.stderr or "")).strip()
    return {"status": "ok" if proc.returncode == 0 else "breaking", "output": output[-4000:]}


# Tiny contract pairs: (file name, base, compatible head, breaking head). `deps --probe` runs each adapter on
# both heads and reports whether it classified them correctly, so adapter command lines can be verified once
# per detector version instead of on the first real contract PR.
_OAS = "openapi: 3.0.0\ninfo: {title: probe, version: '1'}\npaths:\n  /a:\n    get:\n      responses:\n        '200': {description: ok}\n"
_PROTO = 'syntax = "proto3";\npackage probe;\nmessage A {\n  string id = 1;\n}\n'
_AAPI = ("asyncapi: 2.6.0\ninfo: {title: probe, version: '1'}\nchannels:\n  a:\n    subscribe:\n      message:\n"
         "        payload: {type: object, properties: {id: {type: string}}}\n")
PROBES = {
    "openapi": ("openapi.yaml", _OAS,
                _OAS + "  /b:\n    get:\n      responses:\n        '200': {description: ok}\n",
                _OAS.replace("/a:", "/renamed:")),
    "protobuf": ("probe.proto", _PROTO, _PROTO.replace("}\n", "  string name = 2;\n}\n"), _PROTO.replace("string id = 1;", "int64 id = 1;")),
    "asyncapi": ("asyncapi.yaml", _AAPI, _AAPI + "  b:\n    subscribe:\n      message:\n        payload: {type: string}\n",
                 _AAPI.replace("  a:\n", "  renamed:\n")),
}


def run_probe(type_: str, runner: Runner = default_runner, which: Callable[[str], str | None] = shutil.which) -> dict:
    if type_ not in PROBES:
        return {"status": "unprobed", "reason": "db-schema needs a dev database; verify with a real migration PR"
                if type_ == "db-schema" else f"no probe fixtures for '{type_}'"}
    if not available(type_, which):
        return {"status": "unprobed", "reason": "detector not installed"}
    name, base, compatible, breaking = PROBES[type_]
    got_ok = compare_files(type_, name, base.encode(), compatible.encode(), runner)
    got_break = compare_files(type_, name, base.encode(), breaking.encode(), runner)
    good = got_ok["status"] == "ok" and got_break["status"] == "breaking"
    out = {"status": "pass" if good else "fail", "compatible_change": got_ok["status"], "breaking_change": got_break["status"]}
    if not good:
        out["output"] = {"compatible_change": got_ok["output"][-800:], "breaking_change": got_break["output"][-800:]}
    return out
