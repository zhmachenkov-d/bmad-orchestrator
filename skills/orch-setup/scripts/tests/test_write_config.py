"""write-config.py: orch answers land in the BMad custom TOML layers, never the installer's files, and orch's own
config loader reads them back. module.yaml stays in step with the library's defaults."""

import importlib.util
import json
import sys
from pathlib import Path

import pytest
import tomllib
import yaml

SKILL = Path(__file__).resolve().parents[2]  # skills/orch-setup
MODULE_YAML = SKILL / "assets" / "module.yaml"
sys.path.insert(0, str(SKILL.parent / "orch-gate" / "scripts"))

from orchlib import config as orch_config  # noqa: E402

spec = importlib.util.spec_from_file_location("write_config", SKILL / "scripts" / "write-config.py")
wc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(wc)

INSTALLED = """# Installer-managed.
[core]
project_name = "shop"

[modules.orch]
orch_coordination_repo = "."
orch_registry_dir = "{project-root}/_bmad-output/orch/subprojects"
orch_contracts_dir = "{project-root}/_bmad-output/orch/contracts"
orch_stale_claim_hours = 48
orch_review_wait_hours = 24
orch_main_branch = "main"
"""
INSTALLED_USER = """[core]
user_name = "T"

[modules.orch]
orch_worktrees_dir = "../{project_name}-worktrees"
"""
CUSTOM = """# Team overrides - keep this comment.

[agents.bmad-agent-pm]
description = "Short PRDs."
"""
DEFAULT_ANSWERS = {
    "orch_coordination_repo": ".",
    "orch_registry_dir": "_bmad-output/orch/subprojects",
    "orch_contracts_dir": "_bmad-output/orch/contracts",
    "orch_worktrees_dir": "../{project_name}-worktrees",
    "orch_stale_claim_hours": "48",
    "orch_review_wait_hours": "24",
    "orch_main_branch": "main",
}


@pytest.fixture
def project(tmp_path):
    bmad = tmp_path / "_bmad"
    (bmad / "custom").mkdir(parents=True)
    (bmad / "config.toml").write_text(INSTALLED)
    (bmad / "config.user.toml").write_text(INSTALLED_USER)
    (bmad / "custom" / "config.toml").write_text(CUSTOM)
    return tmp_path


def run(root, answers, *extra):
    path = root / "answers.json"
    path.write_text(json.dumps(answers))
    return wc.main(["--project-root", str(root), "--module-yaml", str(MODULE_YAML), "--answers", str(path), *extra])


def output(capsys):
    return json.loads(capsys.readouterr().out)


def orch_keys(path):
    return tomllib.loads(path.read_text()).get("modules", {}).get("orch", {}) if path.is_file() else {}


def snapshot(root):
    return {p: p.read_bytes() for p in sorted((root / "_bmad").rglob("*.toml"))}


def test_installer_answers_leave_every_file_untouched(project, capsys):
    before = snapshot(project)
    assert run(project, DEFAULT_ANSWERS) == 0
    result = output(capsys)
    assert result["written"] == {} and result["installer_config"] is True
    assert snapshot(project) == before


def test_changed_answers_go_to_custom_layers_and_keep_comments(project, capsys):
    installer_files = {p: p.read_bytes() for p in (project / "_bmad" / "config.toml", project / "_bmad" / "config.user.toml")}
    answers = {**DEFAULT_ANSWERS, "orch_stale_claim_hours": "12", "orch_registry_dir": "orch/registry",
               "orch_worktrees_dir": "~/wt"}
    assert run(project, answers) == 0
    written = output(capsys)["written"]
    assert written["_bmad/custom/config.toml"]["set"] == {"orch_stale_claim_hours": 12,
                                                          "orch_registry_dir": "{project-root}/orch/registry"}
    assert written["_bmad/custom/config.user.toml"]["set"] == {"orch_worktrees_dir": "~/wt"}
    team = project / "_bmad" / "custom" / "config.toml"
    assert "# Team overrides - keep this comment." in team.read_text()
    assert tomllib.loads(team.read_text())["agents"]["bmad-agent-pm"]["description"] == "Short PRDs."
    assert orch_keys(team) == {"orch_stale_claim_hours": 12, "orch_registry_dir": "{project-root}/orch/registry"}
    assert "keep this file out of git" in (project / "_bmad" / "custom" / "config.user.toml").read_text()
    assert all(p.read_bytes() == b for p, b in installer_files.items())


def test_orch_config_loader_reads_the_written_values(project, capsys):
    assert run(project, {**DEFAULT_ANSWERS, "orch_stale_claim_hours": "12", "orch_main_branch": "trunk",
                         "orch_worktrees_dir": "~/wt"}) == 0
    layers = [(p, (project / p).read_bytes()) for p in orch_config.LAYERS]
    merged = orch_config._layer_values(layers, "test")
    assert merged["orch_stale_claim_hours"] == 12 and merged["orch_main_branch"] == "trunk"
    cfg = orch_config.Config(**{**{f: "" for f in orch_config.Config.__dataclass_fields__}, "worktrees_dir": "x"})
    assert orch_config.user_settings(project, cfg)["worktrees_dir"] == "~/wt"


def test_answer_equal_to_installer_value_removes_the_override(project, capsys):
    run(project, {**DEFAULT_ANSWERS, "orch_stale_claim_hours": "12", "orch_worktrees_dir": "~/wt"})
    capsys.readouterr()
    assert run(project, DEFAULT_ANSWERS) == 0
    written = output(capsys)["written"]
    assert written["_bmad/custom/config.toml"]["removed"] == ["orch_stale_claim_hours"]
    assert written["_bmad/custom/config.user.toml"]["removed"] == ["orch_worktrees_dir"]
    team = project / "_bmad" / "custom" / "config.toml"
    assert "modules" not in tomllib.loads(team.read_text())
    assert "agents" in tomllib.loads(team.read_text())


def test_unprefixed_custom_key_is_the_same_key(project, capsys):
    team = project / "_bmad" / "custom" / "config.toml"
    team.write_text(CUSTOM + "\n[modules.orch]\nstale_claim_hours = 6\n")
    assert run(project, {"orch_stale_claim_hours": "12"}) == 0
    assert orch_keys(team) == {"orch_stale_claim_hours": 12}
    assert run(project, {"orch_stale_claim_hours": "48"}) == 0
    assert orch_keys(team) == {}


def test_without_installer_config_every_answer_is_written(tmp_path, capsys):
    (tmp_path / "_bmad").mkdir()
    assert run(tmp_path, DEFAULT_ANSWERS) == 0
    result = output(capsys)
    assert result["installer_config"] is False
    assert set(orch_keys(tmp_path / "_bmad" / "custom" / "config.toml")) == set(DEFAULT_ANSWERS) - {"orch_worktrees_dir"}
    assert set(orch_keys(tmp_path / "_bmad" / "custom" / "config.user.toml")) == {"orch_worktrees_dir"}


def test_dry_run_reports_without_writing(project, capsys):
    before = snapshot(project)
    assert run(project, {"orch_main_branch": "trunk"}, "--dry-run") == 0
    result = output(capsys)
    assert result["dry_run"] is True
    assert result["written"]["_bmad/custom/config.toml"]["set"] == {"orch_main_branch": "trunk"}
    assert snapshot(project) == before


@pytest.mark.parametrize("answers, key", [
    ({"orch_unknown": "x"}, "orch_unknown"),
    ({"orch_stale_claim_hours": "two days"}, "orch_stale_claim_hours"),
    ({"orch_main_branch": " "}, "orch_main_branch"),
])
def test_invalid_answers_fail_and_write_nothing(project, capsys, answers, key):
    before = snapshot(project)
    assert run(project, {**answers, "orch_review_wait_hours": "6"}) == 1
    assert key in output(capsys)["errors"]
    assert snapshot(project) == before


def test_values_in_effect_fed_back_are_not_templated_twice(project, capsys):
    """Headless passes the values in effect; a stored `{project-root}/...` path must not gain a second prefix."""
    before = snapshot(project)
    stored = {**DEFAULT_ANSWERS, "orch_registry_dir": "{project-root}/_bmad-output/orch/subprojects",
              "orch_contracts_dir": "{project-root}/_bmad-output/orch/contracts"}
    assert run(project, stored) == 0
    assert output(capsys)["written"] == {} and snapshot(project) == before
    assert run(project, {"orch_registry_dir": "{project-root}/orch/registry"}) == 0
    assert orch_keys(project / "_bmad" / "custom" / "config.toml") == {"orch_registry_dir": "{project-root}/orch/registry"}


def show(root, capsys):
    assert wc.main(["--project-root", str(root), "--module-yaml", str(MODULE_YAML), "--show"]) == 0
    return output(capsys)


def test_show_reports_raw_values_in_effect_and_their_layer(project, capsys):
    (project / "_bmad" / "custom" / "config.toml").write_text(
        CUSTOM + '\n[modules.orch]\norch_registry_dir = "{project-root}/orch/registry"\norch_worktrees_dir = "/team/wt"\n')
    (project / "_bmad" / "custom" / "config.user.toml").write_text('[modules.orch]\norch_stale_claim_hours = 1\n')
    result = show(project, capsys)
    v = result["variables"]
    assert result["installer_config"] is True
    assert v["orch_registry_dir"] == {**v["orch_registry_dir"], "value": "orch/registry", "source": "_bmad/custom/config.toml"}
    assert v["orch_contracts_dir"]["value"] == "_bmad-output/orch/contracts" and v["orch_contracts_dir"]["source"] == "_bmad/config.toml"
    # a user setting sees every layer; a team key ignores the personal ones
    assert v["orch_worktrees_dir"]["value"] == "/team/wt" and v["orch_worktrees_dir"]["source"] == "_bmad/custom/config.toml"
    assert v["orch_stale_claim_hours"]["value"] == 48 and v["orch_stale_claim_hours"]["source"] == "_bmad/config.toml"
    # the shown values, fed back as answers, change nothing
    capsys.readouterr()
    before = snapshot(project)
    assert run(project, {k: x["value"] for k, x in v.items()}) == 0
    assert output(capsys)["written"] == {} and snapshot(project) == before


def test_show_without_installer_config_gives_defaults(tmp_path, capsys):
    (tmp_path / "_bmad").mkdir()
    result = show(tmp_path, capsys)
    assert result["installer_config"] is False
    assert all(x["source"] == "default" and x["value"] == x["default"] for x in result["variables"].values())


def test_unresolved_project_root_token_is_refused(capsys):
    code = wc.main(["--project-root", "{project-root}", "--module-yaml", str(MODULE_YAML), "--answers", "a.json"])
    assert code == 2 and "{project-root}" in output(capsys)["error"]


def test_project_without_bmad_is_refused(tmp_path, capsys):
    assert run(tmp_path, DEFAULT_ANSWERS) == 2
    assert "_bmad" in output(capsys)["error"]


def test_module_yaml_matches_the_library_defaults():
    data = yaml.safe_load(MODULE_YAML.read_text())
    variables = {k: v for k, v in data.items() if isinstance(v, dict) and "prompt" in v}
    orch_defaults = {k: v for k, v in orch_config.DEFAULTS.items() if k.startswith("orch_")}
    assert set(variables) == set(orch_defaults)
    for key, default in orch_defaults.items():
        assert variables[key]["default"] == default, key
        assert bool(variables[key].get("user_setting")) == (key in orch_config.USER_KEYS), key
