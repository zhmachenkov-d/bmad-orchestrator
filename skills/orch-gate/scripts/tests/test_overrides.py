"""The bmad-build override template: stock keys only, real orch.py calls, and it renders with BMad."""

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
from conftest import run_cli
from orchlib import markers, work

SKILL = Path(__file__).resolve().parents[2]  # skills/orch-gate
TEMPLATE = SKILL / "assets" / "custom" / "bmad-build.toml"
DEFAULT_CLI = "{project-root}/.claude/skills/orch-gate/scripts/orch.py"
ORCH_CALL = re.compile(r"uv run @ORCH_CLI@ ([^`\n]+)")


def _find_up(*relatives: str) -> Path | None:
    """First existing `<ancestor>/<relative>` walking up from this skill (source repo or an installed copy)."""
    for base in SKILL.parents:
        for rel in relatives:
            if (base / rel).exists():
                return base / rel
    return None


def _stock_skill() -> Path:
    found = _find_up(".claude/skills/bmad-build/customize.toml", ".agents/skills/bmad-build/customize.toml")
    if found is None:
        pytest.skip("stock bmad-build is not installed")
    return found.parent


def _template() -> dict:
    with TEMPLATE.open("rb") as f:
        return tomllib.load(f)


def _text() -> str:
    return TEMPLATE.read_text(encoding="utf-8")


def _values() -> list[str]:
    wf = _template()["workflow"]
    return [v for value in wf.values() for v in (value if isinstance(value, list) else [value])]


def test_template_parses_as_toml():
    data = _template()
    assert list(data) == ["workflow"]
    assert isinstance(data["workflow"], dict)


def test_template_keys_and_types_match_stock_bmad_build():
    with (_stock_skill() / "customize.toml").open("rb") as f:
        stock = tomllib.load(f)["workflow"]
    ours = _template()["workflow"]
    for key, value in ours.items():
        assert key in stock, f"{key} is not a stock bmad-build key"
        assert type(value) is type(stock[key]), f"{key}: {type(value).__name__} != {type(stock[key]).__name__}"
        if isinstance(value, list):
            assert value and all(isinstance(i, str) and i.strip() for i in value), f"{key}: empty or non-string item"


def test_template_shape_and_forbidden_tokens():
    wf = _template()["workflow"]
    assert set(wf) == {"activation_steps_prepend", "persistent_facts", "on_complete"}
    assert "@ORCH_CLI@" in _text()
    values = "\n".join(_values())
    assert "{skill-root}" not in values
    assert "file:" not in values
    assert "orch.py" not in values  # only @ORCH_CLI@ stands for the CLI path
    # every orch instruction is gated on the story branch
    for value in _values():
        assert "git symbolic-ref --short HEAD" in value or "starts with `story/`" in value
    # no absolute path to orch.py: only the placeholder
    assert all(m.group(0).startswith("uv run @ORCH_CLI@") for m in re.finditer(r"uv run \S+", values))


def test_template_orch_calls_parse_with_the_cli():
    calls = [m.group(1).strip() for m in ORCH_CALL.finditer("\n".join(_values()))]
    commands = set()
    parser = orch.build_parser()
    for call in calls:
        argv = shlex.split(call.replace("<key>", "1-2"))
        args = parser.parse_args(argv)
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
