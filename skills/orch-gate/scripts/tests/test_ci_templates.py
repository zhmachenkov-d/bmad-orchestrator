"""The orch-gate CI templates: they parse once substituted, their gate command parses with `build_parser()`,
and their scripts behave.

The GitHub gate step and the GitLab script run under bash in a tmp repo with a bare origin, mostly against a stub
`uv`; one test runs the GitHub step with the real orch.py to check the temp-dir strip on real fix commands.
"""

import json
import os
import re
import shlex
import stat
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

import orch
from conftest import ENV, EPICS, R, registry_yaml

SKILL = Path(__file__).resolve().parents[2]  # skills/orch-gate
CI = SKILL / "assets" / "ci"
GITHUB, GITLAB, OWNERS = "github-actions.yml", "gitlab-ci.yml", "CODEOWNERS"
DEFAULTS = {
    "@ORCH_CLI_DIR@": ".claude/skills/orch-gate",
    "@ORCH_CLI_PATH@": ".claude/skills/orch-gate/scripts/orch.py",
    "@ORCH_REGISTRY_DIR@": R,
    "@ORCH_CONTRACTS_DIR@": "_bmad-output/orch/contracts",
    "@PLANNING_ARTIFACTS@": "_bmad-output/planning-artifacts",
    "@ORCH_OWNERS@": "@acme/orch-owners",
}
CLI_DIR, CLI_PATH, OWNER = DEFAULTS["@ORCH_CLI_DIR@"], DEFAULTS["@ORCH_CLI_PATH@"], DEFAULTS["@ORCH_OWNERS@"]
PLACEHOLDER = re.compile(r"@[A-Z_]+@")
ABSOLUTE = re.compile(r"(?<![\w@}.-])/(?:home|Users|workspaces|tmp|usr|opt|root|var|etc)/|\b[A-Za-z]:\\")
LIMIT = 900000

STUB = r"""#!/usr/bin/env bash
set -e
if [ "$1" != run ] || [ "$2" != --script ]; then echo "stub: unexpected uv args: $*" >&2; exit 97; fi
cli="$3"
wt="${cli%/"$STUB_CLI_PATH"}"
if [ "$wt" = "$cli" ] || [ ! -f "$cli" ]; then echo "stub: $cli is not <WT>/$STUB_CLI_PATH" >&2; exit 98; fi
case "$wt" in "$STUB_WT_ROOT"/*) ;; *) echo "stub: WT $wt is not under $STUB_WT_ROOT" >&2; exit 98 ;; esac
printf '%s\n' "$wt" "$@" > "$STUB_LOG"
echo "WT=$wt/"
echo "ARGS: $*"
echo "CLI: $(cat "$cli")"
out="" prev=""
for a in "$@"; do if [ "$prev" = -o ]; then out="$a"; fi; prev="$a"; done
if [ -n "$out" ] && [ "${STUB_CODE:-0}" != 2 ]; then printf '{"fix": "uv run %s"}\n' "$cli" > "$out"; fi
if [ -n "$STUB_BIG" ]; then head -c "$STUB_BIG" /dev/zero | tr '\0' x; echo; fi
exit "${STUB_CODE:-0}"
"""


def _substituted(name: str) -> str:
    text = (CI / name).read_text(encoding="utf-8")
    for placeholder, value in DEFAULTS.items():
        text = text.replace(placeholder, value)
    return text


def _github() -> dict:
    return yaml.safe_load(_substituted(GITHUB))


def _gitlab() -> dict:
    return yaml.safe_load(_substituted(GITLAB))


def _triggers(doc: dict) -> dict:
    return doc.get("on", doc.get(True))  # YAML 1.1 reads a bare `on:` key as True


def _gate_step(doc: dict) -> dict:
    [step] = [s for job in doc["jobs"].values() for s in job["steps"] if s.get("id") == "gate"]
    return step


def _script(platform: str) -> str:
    return _gate_step(_github())["run"] if platform == "github" else "\n".join(_gitlab()["orch-gate"]["script"])


def _gate_argv(script: str) -> tuple[list, list]:
    [line] = [ln for ln in script.splitlines() if "uv run --script" in ln]
    tokens = shlex.split(line)
    return tokens[:4], tokens[4:tokens.index(">")]


# ---- static shape ----

def test_every_shipped_template_is_covered():
    assert sorted(p.name for p in CI.iterdir()) == sorted([GITHUB, GITLAB, OWNERS])


@pytest.mark.parametrize("name", [GITHUB, GITLAB, OWNERS])
def test_templates_start_with_a_header_and_use_known_placeholders(name):
    raw = (CI / name).read_text(encoding="utf-8")
    assert raw.startswith("# ") and "TEMPLATE shipped with orch-gate" in raw.splitlines()[0]
    assert set(PLACEHOLDER.findall(raw)) <= set(DEFAULTS)
    text = _substituted(name)
    assert not PLACEHOLDER.search(text), PLACEHOLDER.search(text)
    assert not ABSOLUTE.search(text), ABSOLUTE.search(text)
    assert "{project-root}" not in text and "{skill-root}" not in text


@pytest.mark.parametrize("name", [GITHUB, GITLAB])
def test_ci_templates_parse_and_never_use_secrets_or_worktrees(name):
    text = _substituted(name)
    assert isinstance(yaml.safe_load(text), dict)
    assert "secrets" not in text.lower() and "git worktree" not in text
    assert "CI_JOB_TOKEN" not in text and "pull_request_target" not in text


@pytest.mark.parametrize("name", [GITHUB, GITLAB])
def test_headers_cover_the_install_contract(name):
    header = "\n".join(ln for ln in (CI / name).read_text(encoding="utf-8").splitlines() if ln.startswith("#"))
    flat = " ".join(header.replace("#", " ").split()).lower()
    for phrase in ("never clobber", "require", "code-owned", "gated by the old version", "contract-moved-on-main",
                   "deps --probe", "polyrepo", "registry-empty", "same change as the registry"):
        assert phrase in flat, phrase
    if name == GITLAB:
        assert "premium" in flat and "merge trains" in flat and "include:" in flat
        assert "parent project's ci/cd variables" in flat and "maintainer" in flat


def test_github_workflow_shape():
    doc = _github()
    assert doc["name"] == "orch-gate"
    assert set(_triggers(doc)) == {"pull_request", "merge_group"}
    assert _triggers(doc)["pull_request"]["types"] == ["opened", "synchronize", "reopened", "edited"]
    assert doc["permissions"] == {"contents": "read"}
    assert "concurrency" in doc
    steps = doc["jobs"]["orch-gate"]["steps"]
    checkout = next(s for s in steps if s.get("uses", "").startswith("actions/checkout@"))
    assert checkout["with"]["fetch-depth"] == 0
    assert any(s.get("uses", "").startswith("astral-sh/setup-uv@") for s in steps)
    gate = _gate_step(doc)
    assert gate["env"] == {"BASE_REF": "${{ github.base_ref }}", "MG_BASE": "${{ github.event.merge_group.base_sha }}"}
    assert "${{" not in gate["run"]  # no expression interpolation inside run
    upload = next(s for s in steps if s.get("uses", "").startswith("actions/upload-artifact@"))
    assert upload["if"] == "always()"
    assert upload["with"]["if-no-files-found"] == "warn"
    assert set(upload["with"]["path"].split()) == {"orch-gate.json", "orch-gate.md"}


def test_gitlab_job_shape():
    job = _gitlab()["orch-gate"]
    assert job["variables"]["GIT_DEPTH"] == 0
    assert job["rules"] == [{"if": '$CI_PIPELINE_SOURCE == "merge_request_event"'}]
    assert job["image"].startswith("ghcr.io/astral-sh/uv:") and "slim" not in job["image"]
    assert job["artifacts"]["when"] == "always" and job["artifacts"]["expose_as"]
    assert set(job["artifacts"]["paths"]) == {"orch-gate.json", "orch-gate.md"}
    assert '"+refs/heads/$T:refs/remotes/origin/$T"' in _script("gitlab")
    assert "$CI_MERGE_REQUEST_TARGET_BRANCH_NAME" in _script("gitlab")


@pytest.mark.parametrize("platform", ["github", "gitlab"])
def test_gate_command_parses_with_the_cli(platform):
    prefix, argv = _gate_argv(_script(platform))
    assert prefix == ["uv", "run", "--script", f"$WT/{CLI_PATH}"]
    assert argv.count("$BASE") == 1
    args = orch.build_parser().parse_args([a.replace("$BASE", "origin/main") for a in argv])
    assert args.cmd == "gate" and args.ci and args.format == "markdown" and args.output == "orch-gate.json"
    assert args.base == "origin/main" and args.head == "HEAD"
    assert args.coord is None and args.coord_ref is None and args.repo is None


def test_codeowners_lines():
    lines = [ln for ln in _substituted(OWNERS).splitlines() if ln.strip() and not ln.startswith("#")]
    rules = dict(ln.split(maxsplit=1) for ln in lines)
    assert len(rules) == len(lines)
    assert set(rules.values()) == {OWNER}
    assert set(rules) == {
        f"/{R}/", "/_bmad-output/orch/contracts/", "/_bmad-output/planning-artifacts/", f"/{CLI_DIR}/",
        "/.github/workflows/orch-gate.yml", "/.gitlab/orch-gate.gitlab-ci.yml", "/.gitlab-ci.yml",
        "/_bmad/config.toml", "/_bmad/custom/config.toml",
        "/.github/CODEOWNERS", "/CODEOWNERS", "/docs/CODEOWNERS", "/.gitlab/CODEOWNERS",
    }


# ---- running the scripts ----

def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True,
                          env={**os.environ, **ENV}).stdout.strip()


def _commit(repo: Path, files: dict, msg: str) -> str:
    for rel, content in files.items():
        path = repo / rel
        if content is None:
            _git(repo, "rm", "-q", rel)
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        _git(repo, "add", rel)
    _git(repo, "commit", "-q", "-m", msg)
    return _git(repo, "rev-parse", "HEAD")


class Rig:
    def __init__(self, tmp: Path, base_files: dict, pr_files: dict | None = None, repo_rel: str = "work",
                 skill_dir: bool = False):
        self.tmp = tmp
        origin = tmp / "origin.git"
        _git(tmp, "init", "-q", "--bare", "-b", "main", str(origin))
        self.repo = tmp / repo_rel
        self.repo.mkdir(parents=True)
        _git(tmp, "init", "-q", "-b", "main", str(self.repo))
        if skill_dir:  # the real orch-gate skill at CLI_DIR
            shutil.copytree(SKILL, self.repo / CLI_DIR, ignore=shutil.ignore_patterns("__pycache__", "tests"))
            _git(self.repo, "add", CLI_DIR)
        _git(self.repo, "remote", "add", "origin", str(origin))
        self.base = _commit(self.repo, {"README.md": "shop\n", **base_files}, "base")
        _git(self.repo, "push", "-q", "origin", "main")
        _git(self.repo, "fetch", "-q", "origin")
        _git(self.repo, "checkout", "-q", "-b", "pr")
        # the PR edits the CLI: the job must still run the base copy
        self.head = _commit(self.repo, pr_files or {CLI_PATH: "pr-cli\n", "src/app.py": "x = 1\n"}, "pr")
        self.bin = tmp / "bin"
        self.bin.mkdir()
        uv = self.bin / "uv"
        uv.write_text(STUB, encoding="utf-8")
        uv.chmod(uv.stat().st_mode | stat.S_IXUSR)
        for d in ("runner", "tmpdir"):
            (tmp / d).mkdir()
        self.summary = tmp / "summary.md"
        self.summary.write_text("", encoding="utf-8")
        self.log = tmp / "stub.log"

    def run(self, platform: str, code: int = 0, event: str = "pull_request", mg_base: str = "", big: int = 0,
            **extra: str):
        wt_root = self.tmp / ("runner" if platform == "github" else "tmpdir")
        env = {**os.environ, **ENV,
               "PATH": f"{self.bin}{os.pathsep}{os.environ['PATH']}",
               "GITHUB_EVENT_NAME": event, "BASE_REF": "main" if event == "pull_request" else "", "MG_BASE": mg_base,
               "RUNNER_TEMP": str(self.tmp / "runner"), "TMPDIR": str(self.tmp / "tmpdir"),
               "GITHUB_STEP_SUMMARY": str(self.summary), "CI_MERGE_REQUEST_TARGET_BRANCH_NAME": "main",
               "STUB_CODE": str(code), "STUB_LOG": str(self.log), "STUB_CLI_PATH": CLI_PATH,
               "STUB_WT_ROOT": str(wt_root), "STUB_BIG": str(big) if big else "",
               "CI_PROJECT_ID": "7", "CI_MERGE_REQUEST_PROJECT_ID": "7", **extra}
        return subprocess.run(["bash", "-eo", "pipefail", "-c", _script(platform)], cwd=self.repo, env=env,
                              capture_output=True, text=True)

    def stub_argv(self) -> tuple[str, list]:
        wt, *argv = self.log.read_text(encoding="utf-8").splitlines()
        return wt, argv

    def md(self) -> str:
        return (self.repo / "orch-gate.md").read_text(encoding="utf-8")


INSTALLED = {CLI_PATH: "base-cli\n", f"{CLI_DIR}/scripts/orchlib/__init__.py": "", f"{R}/shop.yaml": "name: shop\n"}


@pytest.fixture
def rig(tmp_path):
    return Rig(tmp_path, INSTALLED)


@pytest.mark.parametrize("platform", ["github", "gitlab"])
@pytest.mark.parametrize("code", [0, 1, 2])
def test_script_passes_the_gate_exit_code_and_strips_the_temp_dir(rig, platform, code):
    if platform == "gitlab":
        _git(rig.repo, "update-ref", "-d", "refs/remotes/origin/main")  # the job must fetch the target itself
    res = rig.run(platform, code)
    assert res.returncode == code, res.stderr
    wt, argv = rig.stub_argv()
    assert argv[:3] == ["run", "--script", f"{wt}/{CLI_PATH}"]
    args = orch.build_parser().parse_args(argv[3:])
    assert args.base == "origin/main" and args.ci and args.format == "markdown" and args.output == "orch-gate.json"
    md = rig.md()
    assert wt not in md
    assert f"ARGS: run --script {CLI_PATH} gate --ci --base origin/main" in md
    assert "CLI: base-cli" in md  # the base copy ran, not the PR's edit
    assert not (rig.repo / ".git" / "worktrees").exists()
    if code != 2:
        data = (rig.repo / "orch-gate.json").read_text(encoding="utf-8")
        assert wt not in data and f"uv run {CLI_PATH}" in data
    if platform == "github":
        summary = rig.summary.read_text(encoding="utf-8")
        assert md.strip() in summary
        assert summary.startswith("````text\n") == (code == 2)
        if code == 2:
            assert summary.rstrip().endswith("\n````")
    else:
        assert "CLI: base-cli" in res.stdout  # printed to the job log


@pytest.mark.parametrize("platform", ["github", "gitlab"])
def test_orch_not_installed_at_base_passes_without_running(tmp_path, platform):
    rig = Rig(tmp_path, {})
    res = rig.run(platform, code=1)
    assert res.returncode == 0, res.stderr
    assert "not installed" in res.stdout
    assert not rig.log.exists() and not (rig.repo / "orch-gate.md").exists()


@pytest.mark.parametrize("platform", ["github", "gitlab"])
def test_registry_without_cli_at_base_exits_2(tmp_path, platform):
    rig = Rig(tmp_path, {f"{R}/shop.yaml": "name: shop\n"})
    res = rig.run(platform)
    assert res.returncode == 2
    assert CLI_PATH in res.stdout + res.stderr
    assert not rig.log.exists()


def test_merge_group_gates_against_the_queue_base(rig):
    _git(rig.repo, "checkout", "-q", "-b", "gh-readonly-queue/main/pr-1", rig.base)
    _commit(rig.repo, {"src/a.py": "a = 1\n"}, "queued 1")
    _commit(rig.repo, {"src/b.py": "b = 1\n"}, "queued 2")
    _git(rig.repo, "update-ref", "-d", "refs/remotes/origin/main")
    res = rig.run("github", event="merge_group", mg_base=rig.base)
    assert res.returncode == 0, res.stderr
    _, argv = rig.stub_argv()
    assert argv[argv.index("--base") + 1] == rig.base


@pytest.mark.parametrize("code", [1, 2])
def test_huge_output_is_truncated_with_a_closed_fence(rig, code):
    res = rig.run("github", code=code, big=LIMIT + 100000)
    assert res.returncode == code, res.stderr
    summary = rig.summary.read_text(encoding="utf-8")
    assert len(summary.encode()) <= LIMIT + 1000
    lines = summary.splitlines()
    assert lines[0] == "````text"
    fence = max(i for i, ln in enumerate(lines) if ln == "````")
    assert fence > 0
    assert "truncated" in "\n".join(lines[fence + 1:]) and "artifact" in "\n".join(lines[fence + 1:])
    assert (rig.repo / "orch-gate.md").stat().st_size > LIMIT  # the artifact keeps it whole


def test_unresolvable_base_exits_2(rig):
    res = rig.run("github", event="merge_group", mg_base="")
    assert res.returncode == 2
    assert "does not resolve" in res.stdout
    assert not rig.log.exists()


def test_gitlab_fork_pipeline_exits_2(rig):
    res = rig.run("gitlab", CI_PROJECT_ID="8", CI_MERGE_REQUEST_PROJECT_ID="7")
    assert res.returncode == 2
    assert "fork" in res.stderr and "parent project" in res.stderr
    assert not rig.log.exists()


def test_real_cli_fix_commands_name_the_committed_cli(tmp_path):
    base = {f"{R}/user-service.yaml": registry_yaml("user-service", "services/user"),
            "_bmad-output/planning-artifacts/epics.md": EPICS, "services/user/app.py": "x = 0\n"}
    # an invalid marker for story 1-3 fails `marker-invalid`, which carries a mechanical fix
    pr = {".orch/stories/1-3.yaml": "garbage: 1\n", "services/user/app.py": "x = 1\n"}
    # checkout two levels below the temp root's parent, as $GITHUB_WORKSPACE is below $RUNNER_TEMP's
    rig = Rig(tmp_path, base, pr, repo_rel="work/shop/shop", skill_dir=True)
    (rig.bin / "uv").write_text('#!/usr/bin/env bash\n[ "$1" = run ] && [ "$2" = --script ] || exit 97\n'
                                'shift 2\nexec "$ORCH_TEST_PYTHON" "$@"\n', encoding="utf-8")
    res = rig.run("github", ORCH_TEST_PYTHON=sys.executable)
    assert res.returncode == 1, res.stdout + res.stderr
    data = (rig.repo / "orch-gate.json").read_text(encoding="utf-8")
    md = rig.md()
    runner = str(tmp_path / "runner")
    assert runner not in data and runner not in md
    result = json.loads(data)
    fixes = [f["fix"] for c in result["checks"] for f in c["findings"] if f.get("fix")]
    assert fixes, result
    for fx in fixes:
        assert fx["command"][:3] == ["uv", "run", CLI_PATH]
    assert f"uv run {CLI_PATH} marker write --story 1-3" in md
