"""setup.py: every step is idempotent and never clobbers a user file; the overrides merge keeps team entries and
upgrades orch's own in place; the registry draft validates with orch-gate; the hook blocks only a failing verdict."""

import importlib.util
import json
import os
import shutil
import stat
import subprocess
import tomllib
from pathlib import Path

import pytest
import tomlkit
import yaml

SKILL = Path(__file__).resolve().parents[2]  # skills/orch-setup
ORCH_GATE_SRC = SKILL.parent / "orch-gate"
CLI_DIR = ".claude/skills/orch-gate"
CLI = "{project-root}/" + CLI_DIR + "/scripts/orch.py"

spec = importlib.util.spec_from_file_location("setup_script", SKILL / "scripts" / "setup.py")
setup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(setup)


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def project(tmp_path):
    """A git project with orch-gate installed (and committed) where the BMad installer puts it."""
    root = tmp_path / "shop"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    git(root, "config", "user.email", "t@example.com")
    git(root, "config", "user.name", "T")
    shutil.copytree(ORCH_GATE_SRC, root / CLI_DIR,
                    ignore=shutil.ignore_patterns("tests", "__pycache__", ".pytest_cache", ".analysis", ".memlog.md"))
    (root / "_bmad").mkdir()
    (root / "_bmad" / "config.toml").write_text('[core]\nproject_name = "shop"\n', encoding="utf-8")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "init")
    return root


def run(capsys, project: Path, *args: str) -> tuple[int, dict]:
    argv = [args[0], "--orch-gate", str(project / CLI_DIR), *args[1:]]
    if args[0] != "hook":
        argv += ["--project-root", str(project)]
    code = setup.main(argv)
    return code, json.loads(capsys.readouterr().out)


def read_toml(path: Path) -> dict:
    return tomllib.loads(path.read_text(encoding="utf-8"))["workflow"]


def template(name: str) -> dict:
    text = (ORCH_GATE_SRC / "assets" / "custom" / f"{name}.toml").read_text(encoding="utf-8")
    text = text.replace("@ORCH_CLI@", CLI).replace("@ORCH_REGISTRY_DIR@", "_bmad-output/orch/subprojects")
    return tomllib.loads(text)["workflow"]


def installed(name: str) -> dict:
    """The template as installed: string keys hold the orch block between its markers."""
    return {k: setup._block(v) + "\n" if isinstance(v, str) else v for k, v in template(name).items()}


# ---- overrides ----

def test_overrides_install_substituted_templates_and_rerun_is_a_no_op(capsys, project):
    code, res = run(capsys, project, "overrides")
    assert code == 0 and [f["status"] for f in res["files"]] == ["created"] * 3, res
    assert res["cli"] == CLI and res["warnings"] == [], res
    for name in setup.OVERRIDES:
        text = (project / "_bmad" / "custom" / f"{name}.toml").read_text(encoding="utf-8")
        assert "@ORCH_" not in text and str(project) not in text
        assert read_toml(project / "_bmad" / "custom" / f"{name}.toml") == installed(name)
    code, res = run(capsys, project, "overrides")
    assert code == 0 and [f["status"] for f in res["files"]] == ["unchanged"] * 3, res


TEAM_BUILD = '''# Team bmad-build tweaks - keep this comment.
[workflow]
persistent_facts = ["Our house style: small commits."]
on_complete = """
Post a note in #builds.
"""
'''


def test_overrides_merge_keeps_team_entries_and_appends_orch_last(capsys, project):
    target = project / "_bmad" / "custom" / "bmad-build.toml"
    target.parent.mkdir(parents=True)
    target.write_text(TEAM_BUILD, encoding="utf-8")
    code, res = run(capsys, project, "overrides")
    assert code == 0, res
    (build,) = [f for f in res["files"] if f["file"].endswith("bmad-build.toml")]
    assert build["status"] == "updated"
    assert any("review that they do not push" in c for c in build["changes"]), build
    text = target.read_text(encoding="utf-8")
    assert text.startswith("# Team bmad-build tweaks - keep this comment.")
    got, ours = read_toml(target), installed("bmad-build")
    assert got["persistent_facts"] == ["Our house style: small commits.", *ours["persistent_facts"]]
    assert got["activation_steps_prepend"] == ours["activation_steps_prepend"]
    assert got["on_complete"] == "Post a note in #builds.\n\n" + ours["on_complete"]
    code, res = run(capsys, project, "overrides")
    assert code == 0 and {f["status"] for f in res["files"]} == {"unchanged"}, res


def _older_orch_revision(target: Path) -> str:
    """The installed file as an older orch revision left it, with team steps before and after the orch block."""
    old = target.read_text(encoding="utf-8")
    old = old.replace("orch run rule — applies only", "orch run rule — (old wording) applies only")
    old = old.replace('on_complete = """<!-- orch:begin', 'on_complete = """Team step first.\n\n<!-- orch:begin')
    assert "Team step first." in old
    old = old.replace("<!-- orch:end -->\n", "<!-- orch:end -->\n\nTeam step last: post to #builds.\n")
    old = old.replace("5. Run `uv run", "5. (old) Run `uv run")
    target.write_text(old, encoding="utf-8")
    return old


def test_overrides_report_drift_and_leave_it_without_update(capsys, project):
    run(capsys, project, "overrides")
    target = project / "_bmad" / "custom" / "bmad-build.toml"
    old = _older_orch_revision(target)
    code, res = run(capsys, project, "overrides")
    assert code == 1 and not res["ok"], res
    (build,) = [f for f in res["files"] if f["file"].endswith("bmad-build.toml")]
    assert build["status"] == "unchanged"
    assert {d["key"] for d in build["drift"]} == {"persistent_facts", "on_complete"}
    assert all("(old" in d["diff"] for d in build["drift"]), build["drift"]
    assert target.read_text(encoding="utf-8") == old


def test_overrides_update_replaces_only_the_orch_block(capsys, project):
    run(capsys, project, "overrides")
    target = project / "_bmad" / "custom" / "bmad-build.toml"
    _older_orch_revision(target)
    code, res = run(capsys, project, "overrides", "--update")
    assert code == 0, res
    got, ours = read_toml(target), template("bmad-build")
    assert got["persistent_facts"] == ours["persistent_facts"]
    assert got["on_complete"] == ("Team step first.\n\n" + setup._block(ours["on_complete"])
                                  + "\n\nTeam step last: post to #builds.\n")


def test_overrides_never_cut_team_text_that_mentions_the_orch_phrase(capsys, project):
    target = project / "_bmad" / "custom" / "bmad-build.toml"
    target.parent.mkdir(parents=True)
    team = "Before the orch completion steps, run lint.\nThen deploy docs.\n"
    target.write_text(f'[workflow]\non_complete = """\n{team}"""\n', encoding="utf-8")
    code, res = run(capsys, project, "overrides")
    assert code == 0, res
    got = read_toml(target)["on_complete"]
    assert got == team.rstrip() + "\n\n" + setup._block(template("bmad-build")["on_complete"]) + "\n"


def test_overrides_mark_a_hand_installed_block_in_place(capsys, project):
    target = project / "_bmad" / "custom" / "bmad-build.toml"
    target.parent.mkdir(parents=True)
    ours = template("bmad-build")["on_complete"]
    target.write_text(tomlkit.dumps({"workflow": {"on_complete": tomlkit.string(
        "Team first.\n\n" + ours + "\nTeam after.\n", multiline=True)}}), encoding="utf-8")
    code, res = run(capsys, project, "overrides")
    assert code == 0, res
    assert read_toml(target)["on_complete"] == "Team first.\n\n" + setup._block(ours) + "\n\nTeam after.\n"


def test_overrides_leave_a_broken_block_marker_to_the_user(capsys, project):
    target = project / "_bmad" / "custom" / "bmad-build.toml"
    target.parent.mkdir(parents=True)
    text = f'[workflow]\non_complete = """\n{setup.BLOCK_BEGIN}\nhalf a block\n"""\n'
    target.write_text(text, encoding="utf-8")
    code, res = run(capsys, project, "overrides")
    assert code == 1, res
    (build,) = [f for f in res["files"] if f["file"].endswith("bmad-build.toml")]
    assert build["status"] == "manual" and target.read_text(encoding="utf-8") == text


def test_overrides_flag_registry_facts_left_from_an_earlier_registry_dir(capsys, project):
    target = project / "_bmad" / "custom" / "bmad-create-epics-and-stories.toml"
    target.parent.mkdir(parents=True)
    target.write_text('[workflow]\npersistent_facts = ["file:{project-root}/old/registry/*.yaml"]\n', encoding="utf-8")
    code, res = run(capsys, project, "overrides")
    assert code == 0, res
    (epics,) = [f for f in res["files"] if f["file"].endswith("bmad-create-epics-and-stories.toml")]
    assert any("old/registry/*.yaml" in c and "earlier orch_registry_dir" in c for c in epics["changes"]), epics


def test_overrides_warn_when_a_personal_on_complete_drops_the_orch_steps(capsys, project):
    personal = project / "_bmad" / "custom" / "bmad-build.user.toml"
    personal.parent.mkdir(parents=True)
    personal.write_text('[workflow]\non_complete = "Just push."\n', encoding="utf-8")
    code, res = run(capsys, project, "overrides")
    assert code == 0 and len(res["warnings"]) == 1, res
    assert "bmad-build.user.toml sets on_complete" in res["warnings"][0] and "orch completion" in res["warnings"][0]
    assert personal.read_text(encoding="utf-8") == '[workflow]\non_complete = "Just push."\n'


def test_overrides_leave_an_unparsable_file_alone(capsys, project):
    target = project / "_bmad" / "custom" / "bmad-sprint-planning.toml"
    target.parent.mkdir(parents=True)
    target.write_text("[workflow\nbroken", encoding="utf-8")
    code, res = run(capsys, project, "overrides")
    assert code == 1 and [f["status"] for f in res["files"]][2] == "unparsable", res
    assert target.read_text(encoding="utf-8") == "[workflow\nbroken"


def test_overrides_refuse_an_orch_gate_outside_the_project(capsys, project, tmp_path):
    elsewhere = tmp_path / "global" / "orch-gate"
    shutil.copytree(project / CLI_DIR, elsewhere)
    code = setup.main(["overrides", "--orch-gate", str(elsewhere), "--project-root", str(project)])
    res = json.loads(capsys.readouterr().out)
    assert code == 2 and res["code"] == "orch-gate-outside", res


# ---- scan / write-registry ----

def _layout(root: Path):
    files = {
        "package.json": json.dumps({"workspaces": ["packages/*"]}),
        "packages/web/package.json": "{}",
        "packages/web/node_modules/x/openapi.yaml": "openapi: 3.0.0\n",  # never scanned
        "services/pay/go.mod": "module pay\n",
        "services/pay/api/openapi.yaml": "openapi: 3.0.0\npaths: {}\n",
        "services/pay/proto/pay/v1/pay.proto": 'syntax = "proto3";\n',
        "services/pay/proto/pay/v1/refund.proto": 'syntax = "proto3";\n',
        "services/ledger/pyproject.toml": "[project]\nname = 'ledger'\n",
        "services/ledger/db/migrations/001_init.sql": "create table t (id int);\n",
        "services/ledger/events.yaml": "asyncapi: 2.6.0\n",
    }
    for rel, text in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text, encoding="utf-8")


def test_scan_proposes_workspaces_and_services_with_their_contracts(capsys, project):
    _layout(project)
    code, res = run(capsys, project, "scan")
    assert code == 0, res
    by = {p["name"]: p for p in res["proposals"]}
    assert set(by) == {"web", "pay", "ledger"}, res
    assert by["web"]["path"] == "packages/web" and by["web"]["allowed_write"] == ["packages/web/**"]
    assert by["web"]["contracts"]["exports"] == []
    c = "_bmad-output/orch/contracts"
    assert by["pay"]["contracts"]["exports"] == [
        {"type": "openapi", "canonical": f"{c}/pay/openapi.yaml", "copy": "services/pay/api/openapi.yaml"},
        {"type": "protobuf", "canonical": f"{c}/pay/proto", "copy": "services/pay/proto/pay/v1"},
    ]
    assert {(e["type"], e["copy"]) for e in by["ledger"]["contracts"]["exports"]} == {
        ("asyncapi", "services/ledger/events.yaml"), ("db-schema", "services/ledger/db/migrations")}
    assert all(p["repo"] == "." for p in res["proposals"])


def test_write_registry_validates_with_orch_gate_and_never_overwrites(capsys, project):
    _layout(project)
    _, res = run(capsys, project, "scan")
    plan = project.parent / "plan.json"
    plan.write_text(json.dumps({"subprojects": res["proposals"]}), encoding="utf-8")
    code, res = run(capsys, project, "write-registry", "--plan", str(plan))
    assert code == 0 and len(res["written"]) == 3, res
    reg = subprocess.run(["uv", "run", str(project / CLI_DIR / "scripts" / "orch.py"), "registry", "--working-tree",
                          "--offline", "--repo", str(project)], capture_output=True, text=True)
    out = json.loads(reg.stdout)
    assert {i["code"] for i in out["issues"]} == {"canonical-missing"}, out  # canonicals land with contract stories
    assert set(out["subprojects"]) == {"web", "pay", "ledger", "contracts"}
    # a re-scan skips what is registered, and a re-write keeps an edited file
    edited = project / "_bmad-output/orch/subprojects/pay.yaml"
    edited.write_text(edited.read_text(encoding="utf-8") + "# edited\n", encoding="utf-8")
    code, res = run(capsys, project, "scan")
    assert res["proposals"] == [] and res["existing"] == ["ledger", "pay", "web"], res
    code, res = run(capsys, project, "write-registry", "--plan", str(plan))
    assert code == 1 and len(res["kept_existing"]) == 3 and res["written"] == [], res
    assert edited.read_text(encoding="utf-8").endswith("# edited\n")


def test_scan_expands_a_double_star_workspace_by_manifest_and_skips_members_outside(capsys, project):
    files = {
        "pnpm-workspace.yaml": "packages:\n  - 'components/**'\n  - '../shared'\n  - '/opt/libs/*'\n",
        "components/README.md": "",
        "components/ui/button/package.json": "{}",
        "components/ui/button/node_modules/dep/package.json": "{}",  # never a member
        "components/forms/package.json": "{}",
        "components/forms/src/index.ts": "",
        "go.work": "go 1.22\nuse (\n  ./tools/gen\n  ./../outside\n)\n",
        "tools/gen/go.mod": "module gen\n",
    }
    for rel, text in files.items():
        (project / rel).parent.mkdir(parents=True, exist_ok=True)
        (project / rel).write_text(text, encoding="utf-8")
    code, res = run(capsys, project, "scan")
    assert code == 0, res
    assert sorted(p["path"] for p in res["proposals"]) == ["components/forms", "components/ui/button", "tools/gen"], res
    outside = [n for n in res["notes"] if "outside this checkout" in n]
    assert len(outside) == 3 and any("../shared" in n for n in outside), res


def test_registry_draft_survives_until_written_and_resumes(capsys, project):
    _layout(project)
    code, res = run(capsys, project, "scan")
    draft = Path(res["draft"]["path"])
    assert res["draft"]["status"] == "written" and draft.is_relative_to(project / ".git"), res  # never committed
    plan = json.loads(draft.read_text(encoding="utf-8"))
    assert [p["name"] for p in plan["subprojects"]] == [p["name"] for p in res["proposals"]]
    # the user's decisions go into the draft; a plain re-scan keeps them, --fresh replaces them
    plan["subprojects"] = [dict(p, name="payments") if p["name"] == "pay" else p for p in plan["subprojects"]]
    draft.write_text(json.dumps(plan), encoding="utf-8")
    _, res = run(capsys, project, "scan")
    assert res["draft"]["status"] == "kept" and "payments" in draft.read_text(encoding="utf-8")
    _, res = run(capsys, project, "state")
    assert res["draft"] == str(draft) and res["in_place"] is False, res
    code, res = run(capsys, project, "write-registry")
    assert code == 0 and res["draft_removed"] and not draft.exists(), res
    assert (project / "_bmad-output/orch/subprojects/payments.yaml").is_file()
    code, res = run(capsys, project, "write-registry")
    assert code == 2 and res["code"] == "no-plan", res


def test_write_registry_rejects_a_name_used_twice_in_the_plan(capsys, project):
    plan = project.parent / "plan.json"
    plan.write_text(json.dumps([{"name": "pay", "repo": ".", "path": "a", "allowed_write": ["a/**"]},
                                {"name": "pay", "repo": ".", "path": "b", "allowed_write": ["b/**"]}]))
    code, res = run(capsys, project, "write-registry", "--plan", str(plan))
    assert code == 1 and res["errors"] == ["entry 1: name 'pay' is used by an earlier entry of the plan"], res
    assert not (project / "_bmad-output").exists()


def test_write_registry_rejects_bad_names(capsys, project):
    plan = project.parent / "plan.json"
    plan.write_text(json.dumps([{"name": "contracts", "repo": ".", "path": "x", "allowed_write": ["x/**"]},
                                {"name": "Pay Svc", "repo": ".", "path": "y", "allowed_write": ["y/**"]}]))
    code, res = run(capsys, project, "write-registry", "--plan", str(plan))
    assert code == 1 and len(res["errors"]) == 2, res
    assert not (project / "_bmad-output").exists()


def test_scan_polyrepo_checkout_uses_its_origin_url(capsys, project, tmp_path):
    code_repo = tmp_path / "pay-service"
    code_repo.mkdir()
    git(code_repo, "init", "-q")
    git(code_repo, "remote", "add", "origin", "https://git.example.com/acme/pay-service.git")
    (code_repo / "go.mod").write_text("module pay\n", encoding="utf-8")
    code, res = run(capsys, project, "scan", "--checkout", str(code_repo))
    assert code == 0, res
    (p,) = res["proposals"]
    assert (p["name"], p["repo"], p["path"], p["allowed_write"]) == (
        "pay-service", "https://git.example.com/acme/pay-service.git", ".", ["**"])


# ---- ci ----

def _register(root: Path, name: str, repo: str = "."):
    reg = root / "_bmad-output/orch/subprojects"
    reg.mkdir(parents=True, exist_ok=True)
    (reg / f"{name}.yaml").write_text(yaml.safe_dump({"name": name, "repo": repo, "path": f"services/{name}",
                                                      "allowed_write": [f"services/{name}/**"]}), encoding="utf-8")


def test_ci_installs_once_then_reports_a_changed_file_instead_of_clobbering(capsys, project):
    _register(project, "pay")
    code, res = run(capsys, project, "ci", "--platform", "github", "--owners", "@acme/orch")
    assert code == 0 and res["files"] == [{"file": ".github/workflows/orch-gate.yml", "status": "created"}], res
    text = (project / ".github/workflows/orch-gate.yml").read_text(encoding="utf-8")
    assert "@ORCH_" not in text and CLI_DIR in text and str(project) not in text
    yaml.safe_load(text)
    assert "/_bmad-output/orch/subprojects/ @acme/orch" in res["codeowners_lines"], res
    assert res["codeowners_file"] == {"github": None}
    code, res = run(capsys, project, "ci", "--platform", "github")
    assert code == 0 and res["files"][0]["status"] == "unchanged", res
    (project / ".github/workflows/orch-gate.yml").write_text(text.replace("ubuntu-latest", "self-hosted"))
    code, res = run(capsys, project, "ci", "--platform", "github")
    assert code == 1 and res["files"][0]["status"] == "differs" and "+    runs-on: ubuntu-latest" in res["files"][0]["diff"]
    assert "self-hosted" in (project / ".github/workflows/orch-gate.yml").read_text(encoding="utf-8")


@pytest.mark.parametrize("existing, status", [
    (None, "created"),
    ("stages: [test]\n", "updated"),
    ("include:\n  - local: other.yml\n", "manual"),
    ("include:\n  - local: .gitlab/orch-gate.gitlab-ci.yml\n", "unchanged"),
])
def test_ci_gitlab_adds_the_include_only_where_it_is_safe(capsys, project, existing, status):
    _register(project, "pay")
    root_ci = project / ".gitlab-ci.yml"
    if existing is not None:
        root_ci.write_text(existing, encoding="utf-8")
    code, res = run(capsys, project, "ci", "--platform", "gitlab")
    assert res["files"][0] == {"file": ".gitlab/orch-gate.gitlab-ci.yml", "status": "created"}, res
    assert res["files"][1]["status"] == status and code == (1 if status == "manual" else 0), res
    loaded = yaml.safe_load(root_ci.read_text(encoding="utf-8"))
    if status == "manual":
        assert root_ci.read_text(encoding="utf-8") == existing
    else:
        assert {"local": ".gitlab/orch-gate.gitlab-ci.yml"} in loaded["include"]


def test_ci_refuses_a_polyrepo_registry(capsys, project):
    _register(project, "pay", repo="https://git.example.com/acme/pay.git")
    code, res = run(capsys, project, "ci", "--platform", "github")
    assert code == 1 and res["code"] == "polyrepo", res
    assert not (project / ".github").exists()


# ---- hook ----

FAKE_ORCH = """import os, sys
open(os.environ["HOOK_LOG"], "a").write(" ".join(sys.argv[1:]) + "\\n")
sys.exit(int(os.environ.get("GATE_EXIT", "0")))
"""


def _fake_gate(tmp_path: Path) -> Path:
    fake = tmp_path / "fake-orch-gate"
    (fake / "scripts").mkdir(parents=True)
    (fake / "scripts" / "orch.py").write_text(FAKE_ORCH, encoding="utf-8")
    return fake


def _push(hook: Path, lines: str, tmp_path: Path, gate_exit: int) -> tuple[int, str]:
    log = tmp_path / "hook.log"
    log.write_text("")
    env = {**os.environ, "HOOK_LOG": str(log), "GATE_EXIT": str(gate_exit)}
    res = subprocess.run([str(hook), "origin", "url"], input=lines, capture_output=True, text=True, env=env)
    return res.returncode, log.read_text()


def test_hook_gates_only_story_branches_and_blocks_only_a_failing_verdict(capsys, project, tmp_path):
    fake = _fake_gate(tmp_path)
    code = setup.main(["hook", "--orch-gate", str(fake), "--repo", str(project)])
    res = json.loads(capsys.readouterr().out)
    hook = Path(res["hook"])
    assert code == 0 and res["status"] == "created" and hook.stat().st_mode & stat.S_IXUSR, res
    sha, zero = "a" * 40, "0" * 40
    story = f"refs/heads/story/1-2 {sha} refs/heads/story/1-2 {zero}\n"
    other = f"refs/heads/claim/1-2 {sha} refs/heads/claim/1-2 {zero}\nrefs/heads/story/1-3 {zero} refs/heads/story/1-3 {sha}\n"
    assert _push(hook, story, tmp_path, 1) == (1, f"gate --format text --head {sha}\n")
    assert _push(hook, story, tmp_path, 2)[0] == 0
    assert _push(hook, story, tmp_path, 0)[0] == 0
    assert _push(hook, other, tmp_path, 1) == (0, "")  # claim refs and deletions are never gated
    setup.main(["hook", "--orch-gate", str(fake), "--repo", str(project)])
    assert json.loads(capsys.readouterr().out)["status"] == "unchanged"


def test_hook_honours_core_hooks_path_and_leaves_a_foreign_hook_alone(capsys, project, tmp_path):
    git(project, "config", "core.hooksPath", ".husky")
    foreign = project / ".husky" / "pre-push"
    foreign.parent.mkdir()
    foreign.write_text("#!/bin/sh\nnpm test\n", encoding="utf-8")
    code = setup.main(["hook", "--orch-gate", str(_fake_gate(tmp_path)), "--repo", str(project)])
    res = json.loads(capsys.readouterr().out)
    assert code == 1 and res["status"] == "foreign-hook" and res["hook"] == str(foreign), res
    assert setup.HOOK_TAG in res["snippet"]
    assert foreign.read_text(encoding="utf-8") == "#!/bin/sh\nnpm test\n"


# ---- check ----

def test_check_flags_a_missing_stock_anchor_and_an_ignored_orch_dir(capsys, project):
    stock = project / ".claude" / "skills" / "bmad-create-epics-and-stories" / "steps"
    stock.mkdir(parents=True)
    (stock / "step-03-create-stories.md").write_text("STORY FORMAT ... So that ...", encoding="utf-8")
    (stock / "step-04-final-validation.md").write_text("[F] Finish", encoding="utf-8")
    (project / ".gitignore").write_text("_bmad-output/\n", encoding="utf-8")
    code, res = run(capsys, project, "check")
    assert code == 1, res
    codes = [p["code"] for p in res["problems"]]
    assert codes.count("orch-dir-ignored") == 3 and "stock-anchor-missing" in codes, res
    anchors = res["stock_anchors"][".claude/skills/bmad-create-epics-and-stories"]
    assert anchors == {"status": "missing", "missing": ["steps/step-04-final-validation.md: [C] Complete"]}
    assert res["stock_anchors"]["bmad-sprint-planning"] == {"status": "not-installed"}


# ---- state ----

def test_state_tells_a_first_install_from_a_rerun_and_finds_drift(capsys, project):
    code, res = run(capsys, project, "state")
    assert code == 0 and res["in_place"] is False, res
    assert set(res["overrides"].values()) == {"missing"} and res["hook"] == "absent" and not res["merge_driver"]
    run(capsys, project, "overrides")
    _register(project, "pay")
    subprocess.run(["uv", "run", str(project / CLI_DIR / "scripts" / "orch.py"), "sprint-status", "install-driver",
                    "--repo", str(project)], check=True, capture_output=True)
    target = project / "_bmad" / "custom" / "bmad-build.toml"
    target.write_text(target.read_text(encoding="utf-8").replace("orch completion", "orch completion (team)", 1),
                      encoding="utf-8")
    code, res = run(capsys, project, "state")
    assert code == 0 and res["in_place"] and res["merge_driver"] and res["registry"] == ["pay"], res
    assert res["overrides"] == {"bmad-build": "drift", "bmad-create-epics-and-stories": "current",
                                "bmad-sprint-planning": "current"}, res


def test_stock_anchors_hold_in_this_repos_installed_skills(capsys):
    """The anchors the check looks for are the ones the installed stock skills have today."""
    root = SKILL.parents[1]
    for skill, needed in setup.STOCK_ANCHORS.items():
        dirs = setup._stock_dirs(root, skill)
        if not dirs:
            pytest.skip(f"stock {skill} is not installed")
        for d in dirs:
            assert all(a in (d / f).read_text(encoding="utf-8") for f, a in needed), d
