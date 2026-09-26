"""The orch override templates: stock keys only, real orch.py calls, and they load with BMad.

Generic checks run over every template in `assets/custom/`; per-template tests pin what each one must say.
"""

import json
import re
import shlex
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

import orch
from conftest import R, run_cli
from orchlib import markers, stories, work

SKILL = Path(__file__).resolve().parents[2]  # skills/orch-gate
CUSTOM = SKILL / "assets" / "custom"
EPICS_SKILL, SPRINT_SKILL = "bmad-create-epics-and-stories", "bmad-sprint-planning"
PLANNING = (EPICS_SKILL, SPRINT_SKILL)
TEMPLATES = ("bmad-build", *PLANNING)
DEFAULT_CLI = "{project-root}/.claude/skills/orch-gate/scripts/orch.py"
DEFAULT_REGISTRY_DIR = "_bmad-output/orch/subprojects"
PLACEHOLDERS = {"@ORCH_CLI@", "@ORCH_REGISTRY_DIR@"}
ORCH_CALL = re.compile(r"uv run @ORCH_CLI@ ([^`\n]+)")
ABSOLUTE = re.compile(r"(?<![\w@}.-])/(?:home|Users|workspaces|tmp|usr|opt|root|var|etc)/|\b[A-Za-z]:\\")


def _find_up(*relatives: str) -> Path | None:
    """First existing `<ancestor>/<relative>` walking up from this skill (source repo or an installed copy)."""
    for base in SKILL.parents:
        for rel in relatives:
            if (base / rel).exists():
                return base / rel
    return None


def _stock_skill(name: str = "bmad-build") -> Path:
    found = _find_up(f".claude/skills/{name}/customize.toml", f".agents/skills/{name}/customize.toml")
    if found is None:
        pytest.skip(f"stock {name} is not installed")
    return found.parent


def _path(name: str = "bmad-build") -> Path:
    return CUSTOM / f"{name}.toml"


def _template(name: str = "bmad-build") -> dict:
    with _path(name).open("rb") as f:
        return tomllib.load(f)


def _text(name: str = "bmad-build") -> str:
    return _path(name).read_text(encoding="utf-8")


def _values(name: str = "bmad-build") -> list[str]:
    wf = _template(name)["workflow"]
    return [v for value in wf.values() for v in (value if isinstance(value, list) else [value])]


def _substituted(name: str, registry_dir: str = DEFAULT_REGISTRY_DIR) -> str:
    return _text(name).replace("@ORCH_CLI@", DEFAULT_CLI).replace("@ORCH_REGISTRY_DIR@", registry_dir)


def _orch_calls(name: str) -> list:
    parser = orch.build_parser()
    return [(argv, parser.parse_args(argv)) for argv in
            (shlex.split(m.group(1).strip().replace("<key>", "1-2")) for m in ORCH_CALL.finditer("\n".join(_values(name))))]


# ---- every template ----

def test_every_shipped_template_is_covered():
    assert sorted(p.stem for p in CUSTOM.glob("*.toml")) == sorted(TEMPLATES)


@pytest.mark.parametrize("name", TEMPLATES)
def test_template_parses_as_toml(name):
    data = _template(name)
    assert list(data) == ["workflow"]
    assert isinstance(data["workflow"], dict)


@pytest.mark.parametrize("name", TEMPLATES)
def test_template_keys_and_types_match_stock(name):
    with (_stock_skill(name) / "customize.toml").open("rb") as f:
        stock = tomllib.load(f)["workflow"]
    ours = _template(name)["workflow"]
    assert 0 < len(ours) <= 4
    for key, value in ours.items():
        assert key in stock, f"{key} is not a stock {name} key"
        assert type(value) is type(stock[key]), f"{key}: {type(value).__name__} != {type(stock[key]).__name__}"
        if isinstance(value, list):
            assert value and all(isinstance(i, str) and i.strip() for i in value), f"{key}: empty or non-string item"


@pytest.mark.parametrize("name", TEMPLATES)
def test_template_uses_placeholders_only(name):
    text, values = _text(name), "\n".join(_values(name))
    assert set(re.findall(r"@[A-Z_]+@", text)) <= PLACEHOLDERS
    assert "@ORCH_CLI@" in values
    assert "{skill-root}" not in values
    assert "orch.py" not in values  # only @ORCH_CLI@ stands for the CLI path
    assert not ABSOLUTE.search(values), ABSOLUTE.search(values)
    # no path to orch.py but the placeholder, and file: facts only under the registry placeholder
    assert all(m.group(0).startswith("uv run @ORCH_CLI@") for m in re.finditer(r"uv run \S+", values))
    for value in _values(name):
        if value.startswith("file:"):
            assert value.startswith("file:{project-root}/@ORCH_REGISTRY_DIR@/"), value


@pytest.mark.parametrize("name", TEMPLATES)
def test_template_orch_calls_parse_with_the_cli(name):
    calls = _orch_calls(name)
    assert calls, "a template with no orch call"
    for argv, args in calls:
        assert args.coord is None and args.coord_ref is None and args.repo is None


# ---- bmad-build ----

def test_bmad_build_shape_and_story_gating():
    wf = _template()["workflow"]
    assert set(wf) == {"activation_steps_prepend", "persistent_facts", "on_complete"}
    assert "file:" not in "\n".join(_values())
    # every orch instruction is gated on the story branch
    for value in _values():
        assert "git symbolic-ref --short HEAD" in value or "starts with `story/`" in value


def test_bmad_build_orch_calls():
    commands = set()
    for argv, args in _orch_calls("bmad-build"):
        commands.add(" ".join(argv[:2]) if args.cmd == "marker" else args.cmd)
        if args.cmd == "context":
            assert args.write and args.story is None  # key comes from the story branch
        if args.cmd == "marker":
            assert args.action == "write" and args.story == "1-2"
        if args.cmd == "gate":
            assert args.format == "text" and args.base is None
    assert commands == {"context", "marker write", "gate"}


def test_template_marker_commit_and_context_paths():
    wf = _template()["workflow"]
    oc, prepend = wf["on_complete"], wf["activation_steps_prepend"][0]
    marker = markers.marker_path("<key>")
    assert "story.key" in oc and "base_branch" in oc and work.CONTEXT_JSON in oc
    assert f"git add -- {marker}" in oc
    assert f"git diff --quiet HEAD -- {marker}" in oc
    assert f'git commit -m "chore(orch): add marker for story <key>" -- {marker}' in oc
    assert "gate --format text" in oc
    assert work.CONTEXT_MD in prepend and work.CONTEXT_JSON in prepend and "HALT" in prepend


def test_template_covers_failure_paths():
    wf = _template()["workflow"]
    oc, prepend, fact = wf["on_complete"], wf["activation_steps_prepend"][0], wf["persistent_facts"][0]
    # stale context from another story is never used: the key must match the branch
    assert "matches the branch key" in prepend and "does not match the branch key" in oc
    assert "`not-story-branch` or `story-not-found`" in prepend and "exits non-zero" in prepend
    # CLI that cannot run (no JSON): a path/uv problem, never a --coord request
    assert "orch CLI could not run" in prepend and "do not ask for `--coord`" in prepend
    # On Complete supersedes the context's own Finish section
    assert "supersede the context's own `## Finish` section" in fact and "`## Finish` section" in oc
    assert "stop without writing the marker" in oc and "within the context's `allowed_write`" in oc
    assert "if the commit fails, report it and stop before the gate" in oc
    assert "Any other exit" in oc and "Claim no verdict" in oc
    assert "Never invent node context" in prepend and "Never write or edit a marker by hand" in oc
    assert "Exit 1 (fail)" in oc and "do not loop" in oc
    assert "do not push or open a PR until the gate passes" in oc
    # override missing on a story branch: documented stock fallback
    assert "Without it, bmad-build runs stock" in _text()


def test_context_json_has_the_fields_on_complete_reads(mono):
    mono.branch("story/1-2")
    code, res = run_cli("context", "--write", "--repo", str(mono.path))
    assert code == 0, res
    ctx = json.loads((mono.path / work.CONTEXT_JSON).read_text(encoding="utf-8"))
    assert ctx["story"]["key"] == "1-2" and ctx["base_branch"] == "main"
    assert ctx["marker"] == markers.marker_path("1-2")
    assert (mono.path / work.CONTEXT_MD).is_file()


def test_template_renders_with_stock_bmad_build(tmp_path):
    renderer = _find_up("_bmad/scripts/render_skill.py")
    config = _find_up("_bmad/config.toml")
    if renderer is None or config is None:
        pytest.skip("BMad renderer is not installed")
    stock = _stock_skill()
    project = tmp_path / "project"
    (project / "_bmad" / "custom").mkdir(parents=True)
    shutil.copy(config, project / "_bmad" / "config.toml")
    # communication_language is a personal install answer: take it from the user layer, or a stand-in
    user_config = config.with_name("config.user.toml")
    if user_config.is_file():
        shutil.copy(user_config, project / "_bmad" / "config.user.toml")
    else:
        (project / "_bmad" / "config.user.toml").write_text('[core]\ncommunication_language = "English"\n',
                                                            encoding="utf-8")
    skill = project / ".claude" / "skills" / "bmad-build"
    shutil.copytree(stock, skill)
    (project / "_bmad" / "custom" / "bmad-build.toml").write_text(
        _text().replace("@ORCH_CLI@", DEFAULT_CLI), encoding="utf-8")

    res = subprocess.run([sys.executable, str(renderer), "--project-root", str(project), "--skill", str(skill)],
                         capture_output=True, text=True, timeout=60)
    assert res.returncode == 0, res.stdout + res.stderr
    assert res.stdout.startswith("read and follow "), res.stdout
    out = Path(res.stdout.removeprefix("read and follow ").strip()).parent
    assert out.is_relative_to(project / "_bmad" / "render")

    call = f"uv run {DEFAULT_CLI}"
    expected = {
        "workflow.md": [f"{call} context --write", ".orch/context/node-context.md", "orch run rule"],
        "step-05-present.md": [f"{call} marker write --story <key>", f"{call} gate --format text"],
        "step-oneshot.md": [f"{call} marker write --story <key>", f"{call} gate --format text"],
    }
    for name, needles in expected.items():
        assert (out / name).is_file(), f"rendered {name} is missing: stock bmad-build no longer ships it"
        text = (out / name).read_text(encoding="utf-8")
        for needle in needles:
            assert needle in text, f"{name} lacks {needle!r}"
    for path in out.rglob("*.md"):
        assert "@ORCH_CLI@" not in path.read_text(encoding="utf-8"), path


# ---- planning templates ----

def _facts(name: str) -> list[str]:
    return _template(name)["workflow"]["persistent_facts"]


@pytest.mark.parametrize("name", PLANNING)
def test_planning_template_sets_only_persistent_facts(name):
    assert set(_template(name)["workflow"]) == {"persistent_facts"}


@pytest.mark.parametrize("name", PLANNING)
def test_planning_template_runs_plan_check_on_the_working_tree(name):
    calls = _orch_calls(name)
    assert {args.cmd for _, args in calls} == {"plan-check"}
    assert all(args.working_tree for _, args in calls)


def test_epics_template_loads_the_registry():
    facts = _facts(EPICS_SKILL)
    assert facts[:2] == ["file:{project-root}/@ORCH_REGISTRY_DIR@/*.yaml", "file:{project-root}/@ORCH_REGISTRY_DIR@/*.yml"]
    assert len(facts) == 4


def test_epics_template_covers_the_orch_story_rules():
    rules, check = _facts(EPICS_SKILL)[2:]
    for label in ("**Subproject:**", "**Depends on:**", "**Contract change:**"):
        assert label in rules
    # the label lines it shows parse as the gate parses them, in order
    shown = [stories.LABEL_RE.match(line) for line in rules.splitlines()]
    assert [m.group(1).lower() for m in shown if m] == ["subproject", "depends on", "contract change"]
    for value in stories.CONTRACT_CHANGES:
        assert f"`{value}`" in rules
    for phrase in ("overrides the stock story format", "\"user value only\"", "Every story gets all three label lines",
                   "right after the \"So that\" line", "before `**Acceptance Criteria:**`", "outside any code fence",
                   "before any other heading", "exact registry file stem", "`contracts` is an implicit pseudo-subproject",
                   "letter suffix", "registry `imports`", "expand → migrate → narrow", "one migration story per consumer",
                   "comes after the `expand` and depends on every migration story", "even without direct user value",
                   "`epic*.md`", "`epics-v1.md`", "`orch-setup`"):
        assert phrase in rules, phrase
    for phrase in ("Step 4", "before offering [C]", "plan-check --working-tree", "offer to fix the epics first",
                   "`no-epics`", "`orch-setup`", "never a verdict", "never re-implement the checks",
                   "`snapshot_skipped`", "`snapshot-incomplete`"):
        assert phrase in check, phrase


def test_sprint_template_covers_the_readiness_rule():
    (fact,) = _facts(SPRINT_SKILL)
    for phrase in ("only to the **readiness** and **sprint-planning** intents",
                   "for the status, validate and fix intents ignore this fact and run no orch command",
                   "before stating the gate verdict", "plan-check --working-tree",
                   "FAIL → FAIL", "CONCERNS → at least CONCERNS", "PASS → no change",
                   "`code`, story and message", "`bmad-create-epics-and-stories`", "`bmad-correct-course`",
                   "Headless runs put these findings in `findings`",
                   "`orch-setup`", "`no-epics`", "`epic*.md`",
                   "not independently completable", "orphan", "plan-check owns",
                   "never a verdict", "orch check as not run", "at least CONCERNS", "never re-implement the checks",
                   "`snapshot_skipped`", "`snapshot-incomplete`"):
        assert phrase in fact, phrase


@pytest.mark.parametrize("name", PLANNING)
def test_planning_template_resolves_with_the_bmad_resolver(name, tmp_path):
    resolver = _find_up("_bmad/scripts/resolve_customization.py")
    if resolver is None:
        pytest.skip("BMad customization resolver is not installed")
    project = tmp_path / "project"
    (project / "_bmad" / "custom").mkdir(parents=True)
    skill = project / ".claude" / "skills" / name
    shutil.copytree(_stock_skill(name), skill)
    (project / "_bmad" / "custom" / f"{name}.toml").write_text(_substituted(name), encoding="utf-8")

    res = subprocess.run([sys.executable, str(resolver), "--skill", str(skill), "--project-root", str(project),
                          "--key", "workflow"], capture_output=True, text=True, timeout=60)
    assert res.returncode == 0, res.stdout + res.stderr
    wf = json.loads(res.stdout)["workflow"]
    facts = wf["persistent_facts"]
    text = "\n".join(facts)
    assert f"uv run {DEFAULT_CLI} plan-check --working-tree" in text
    assert not re.search(r"@[A-Z_]+@", json.dumps(wf))
    assert wf.get("on_complete", "") == ""  # stock value: the template never sets it
    if name == EPICS_SKILL:
        assert f"file:{{project-root}}/{DEFAULT_REGISTRY_DIR}/*.yaml" in facts
        assert f"file:{{project-root}}/{DEFAULT_REGISTRY_DIR}/*.yml" in facts
        assert "orch story rules" in text and "orch plan check" in text
    else:
        assert "orch readiness rule" in text and f"{{project-root}}/{DEFAULT_REGISTRY_DIR}" in text


def test_registry_dir_placeholder_matches_the_normalized_config(mono):
    """A `{project-root}/...` orch_registry_dir with {project_name} normalizes to what the template needs."""
    custom = "_bmad-output/shop/subprojects"
    mono.write("_bmad/custom/config.toml", '[core]\nproject_name = "shop"\n\n[modules.orch]\n'
                                           'registry_dir = "{project-root}/_bmad-output/{project_name}/subprojects"\n')
    (mono.path / custom).parent.mkdir(parents=True)
    mono.git("mv", R, custom)
    mono.commit("move registry")
    code, res = run_cli("config", "--repo", str(mono.path))
    assert code == 0, res
    registry_dir = res["config"]["registry_dir"]
    assert registry_dir == custom
    facts = tomllib.loads(_substituted(EPICS_SKILL, registry_dir))["workflow"]["persistent_facts"]
    globs = [f.removeprefix("file:{project-root}/") for f in facts if f.startswith("file:")]
    assert globs == [f"{custom}/*.yaml", f"{custom}/*.yml"]
    loaded = sorted(p.name for g in globs for p in mono.path.glob(g))
    assert loaded == ["payment-service.yaml", "user-service.yaml"]
    # plan-check reads the same registry the facts load
    code, res = run_cli("plan-check", "--working-tree", "--repo", str(mono.path))
    assert code == 0 and res["verdict"] == "PASS", res


def test_stock_anchors_named_by_the_planning_facts_still_exist():
    epics, sprint = _stock_skill(EPICS_SKILL), _stock_skill(SPRINT_SKILL)
    assert "[C] Complete" in (epics / "steps" / "step-04-final-validation.md").read_text(encoding="utf-8")
    step3 = (epics / "steps" / "step-03-create-stories.md").read_text(encoding="utf-8")
    assert "STORY FORMAT" in step3 and "So that" in step3
    skill = (sprint / "SKILL.md").read_text(encoding="utf-8")
    for intent in ("readiness", "sprint-planning", "status", "validate", "fix"):
        assert f"**{intent}**" in skill, intent
