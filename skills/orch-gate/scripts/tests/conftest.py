import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))

import orch  # noqa: E402

ENV = {"GIT_AUTHOR_NAME": "T", "GIT_AUTHOR_EMAIL": "t@x", "GIT_COMMITTER_NAME": "T", "GIT_COMMITTER_EMAIL": "t@x"}

EPICS = """# Shop - Epic Breakdown

## Epic 1: Payments

### Story 1.1: Payment API contract
**Subproject:** contracts
**Depends on:** none
**Contract change:** expand

As a dev, I want a contract.

### Story 1.2: Implement payment API
**Subproject:** payment-service
**Depends on:** 1.1

As a user, I want to pay.

### Story 1.3: User service calls payments
**Subproject:** user-service
**Depends on:** 1.1

### Story 1.4: Remove legacy field
**Subproject:** contracts
**Depends on:** 1.3
**Contract change:** narrow
"""

SPRINT = """# generated
generated: 09-25-2026 10:00
last_updated: 09-25-2026 10:00
project: shop
development_status:
  epic-1: in-progress
  1-1-payment-api-contract: backlog
  1-2-implement-payment-api: backlog
  1-3-user-service-calls-payments: backlog
  1-4-remove-legacy-field: backlog
  epic-1-retrospective: optional

action_items: []
"""

C = "_bmad-output/orch/contracts"
R = "_bmad-output/orch/subprojects"


class Repo:
    def __init__(self, path: Path):
        self.path = path
        path.mkdir(parents=True, exist_ok=True)
        self.git("init", "-q", "-b", "main")

    def git(self, *args, check=True):
        p = subprocess.run(["git", "-C", str(self.path), *args], capture_output=True, text=True, env={**os.environ, **ENV})
        if check and p.returncode:
            raise RuntimeError(p.stderr)
        return p.stdout.strip()

    def write(self, rel, text):
        f = self.path / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(text)
        return self

    def rm(self, rel):
        self.git("rm", "-q", rel)
        return self

    def commit(self, msg="c"):
        self.git("add", "-A")
        self.git("commit", "-q", "--allow-empty", "-m", msg)
        return self.git("rev-parse", "HEAD")

    def branch(self, name):
        self.git("checkout", "-q", "-b", name)
        return self

    def checkout(self, name):
        self.git("checkout", "-q", name)
        return self

    def blob(self, rel, ref="HEAD"):
        return self.git("rev-parse", f"{ref}:{rel}")


def registry_yaml(name, path, imports=(), exports=(), repo="."):
    ex = "".join(f"\n    - {{type: {t}, canonical: {c}" + (f", copy: {cp}" if cp else "") + "}" for t, c, cp in exports)
    return (f"name: {name}\nrepo: {repo}\npath: {path}\nallowed_read: [\"{path}/**\"]\nallowed_write: [\"{path}/**\"]\n"
            f"contracts:\n  exports:{ex or ' []'}\n  imports: [{', '.join(imports)}]\n")


@pytest.fixture
def mono(tmp_path):
    """Monorepo: payment-service exports openapi (with a copy), user-service imports it."""
    r = Repo(tmp_path / "mono")
    r.write(f"{R}/payment-service.yaml", registry_yaml(
        "payment-service", "services/payment", exports=[("openapi", f"{C}/payment-service/openapi.yaml", "services/payment/openapi.yaml")]))
    r.write(f"{R}/user-service.yaml", registry_yaml("user-service", "services/user", imports=["payment-service"]))
    r.write(f"{C}/payment-service/openapi.yaml", "openapi: 3.0.0\npaths: {}\n")
    r.write("services/payment/openapi.yaml", "openapi: 3.0.0\npaths: {}\n")
    r.write("services/payment/app.py", "print('pay')\n")
    r.write("services/user/app.py", "print('user')\n")
    r.write("_bmad-output/planning-artifacts/epics.md", EPICS)
    r.write("_bmad-output/planning-artifacts/prd.md", "# PRD\n")
    r.write("_bmad-output/implementation-artifacts/sprint-status.yaml", SPRINT)
    r.write("README.md", "hi\n")
    r.commit("init")
    return r


def run_cli(*argv, env=None):
    """Run orch.main in-process, capturing JSON (or text) stdout."""
    import io
    from contextlib import redirect_stdout

    old = dict(os.environ)
    os.environ.update(env or {})
    os.environ.pop("CI", None)
    buf = io.StringIO()
    try:
        with redirect_stdout(buf):
            code = orch.main(list(argv))
    finally:
        os.environ.clear()
        os.environ.update(old)
    out = buf.getvalue()
    try:
        return code, json.loads(out)
    except json.JSONDecodeError:
        return code, out


def marker(r: Repo, key, epic=1, **pins_override):
    """Write a correct marker via the CLI, then apply overrides to its pins."""
    code, res = run_cli("marker", "write", "--story", key, "--repo", str(r.path))
    assert code == 0, res
    return res


def fake_bin(tmp_path, name, script):
    d = tmp_path / "bin"
    d.mkdir(exist_ok=True)
    f = d / name
    f.write_text("#!/usr/bin/env python3\n" + script)
    f.chmod(0o755)
    return str(d) + os.pathsep + os.environ["PATH"]


def check(result, cid):
    return next(c for c in result["checks"] if c["id"] == cid)
