import os

import yaml
from conftest import C, R, SPRINT, Repo, check, fake_bin, registry_yaml, run_cli

IMPL = "services/payment/app.py"
OAS = f"{C}/payment-service/openapi.yaml"
STATUS = "_bmad-output/implementation-artifacts/sprint-status.yaml"


def gate(r, *extra, env=None):
    return run_cli("gate", "--repo", str(r.path), "--base", "main", *extra, env=env)


def test_registry_and_stories_are_valid(mono):
    code, res = run_cli("registry", "--repo", str(mono.path))
    assert code == 0, res["issues"]
    assert "contracts" in res["subprojects"]
    code, res = run_cli("stories", "--repo", str(mono.path))
    assert code == 0, res["issues"]


def test_registry_validation_finds_problems(mono):
    mono.write(f"{R}/bad.yaml", registry_yaml("bad", "services/payment", imports=["ghost"],
                                               exports=[("graphql", "elsewhere/x.graphql", None)]))
    mono.commit()
    code, res = run_cli("registry", "--repo", str(mono.path))
    codes = {i["code"] for i in res["issues"]}
    assert code == 1
    assert {"unknown-import", "unknown-contract-type", "canonical-location", "write-overlap"} <= codes


def test_story_pr_passes(mono):
    mono.branch("orch/1-2")
    mono.write(IMPL, "print('pay v2')\n")
    mono.commit()
    marker(mono, "1-2")
    mono.commit("marker")
    code, res = gate(mono)
    assert code == 0, res
    assert res["mode"] == "story" and res["story"] == "1-2" and res["subproject"] == "payment-service"


def marker(r, key):
    code, res = run_cli("marker", "write", "--story", key, "--repo", str(r.path))
    assert code == 0, res
    return res["marker"]


def test_scope_violation_fails(mono):
    mono.branch("orch/1-2")
    mono.write(IMPL, "x\n").write("services/user/app.py", "sneaky\n")
    marker(mono, "1-2")
    mono.commit()
    code, res = gate(mono)
    assert code == 1
    assert check(res, "scope")["status"] == "fail"
    assert check(res, "scope")["findings"][0]["path"] == "services/user/app.py"


def test_non_story_change_to_subproject_fails_but_docs_pass(mono):
    mono.branch("docs")
    mono.write("README.md", "more\n")
    mono.commit()
    assert gate(mono)[0] == 0
    mono.write(IMPL, "no story\n")
    mono.commit()
    code, res = gate(mono)
    assert code == 1 and res["mode"] == "no-story"
    assert "need a story marker" in check(res, "scope")["findings"][0]["message"]


def test_two_markers_fail(mono):
    mono.branch("x")
    marker(mono, "1-2")
    marker(mono, "1-3")
    mono.commit()
    code, res = gate(mono)
    assert code == 1 and check(res, "marker")["status"] == "fail"


def test_stale_pin_after_contract_moves_on_main(mono):
    mono.branch("orch/1-3")
    mono.write("services/user/app.py", "uses payments\n")
    marker(mono, "1-3")
    mono.commit()
    mono.checkout("main").write(OAS, "openapi: 3.0.0\npaths: {/pay: {}}\n").write(
        "services/payment/openapi.yaml", "openapi: 3.0.0\npaths: {/pay: {}}\n")
    mono.commit("contract moved")
    mono.checkout("orch/1-3")
    code, res = gate(mono)
    assert code == 1
    assert "pinned at" in check(res, "pins")["findings"][0]["message"]


def test_conformance_copy_drift_fails(mono):
    mono.branch("orch/1-2")
    mono.write("services/payment/openapi.yaml", "openapi: 3.0.0\npaths: {/edited: {}}\n")
    marker(mono, "1-2")
    mono.commit()
    code, res = gate(mono)
    assert code == 1 and check(res, "conformance")["status"] == "fail"


def test_prd_change_only_warns(mono):
    mono.branch("orch/1-2")
    mono.write(IMPL, "x\n")
    marker(mono, "1-2")
    mono.commit()
    mono.checkout("main").write("_bmad-output/planning-artifacts/prd.md", "# PRD v2\n").commit()
    mono.checkout("orch/1-2")
    code, res = gate(mono)
    assert code == 0 and check(res, "pins")["status"] == "warn"


ODIFF = "import sys\nsys.exit(1 if 'BREAK' in open(sys.argv[3]).read() else 0)\n"


def test_contract_story_non_breaking_passes_and_breaking_fails(mono, tmp_path):
    path = fake_bin(tmp_path, "oasdiff", ODIFF)
    mono.branch("orch/1-1")
    mono.write(OAS, "openapi: 3.0.0\npaths: {/new: {}}\n")
    mono.commit()
    marker(mono, "1-1")
    mono.commit()
    code, res = gate(mono, env={"PATH": path})
    assert code == 0, res
    mono.write(OAS, "openapi: 3.0.0\n# BREAK\n")
    mono.commit()
    marker(mono, "1-1")
    mono.commit()
    code, res = gate(mono, env={"PATH": path})
    assert code == 1
    assert "expand -> migrate -> contract" in check(res, "breaking")["findings"][0]["hint"]


def test_missing_detector_fails_closed(mono):
    mono.branch("orch/1-1")
    mono.write(OAS, "openapi: 3.0.0\npaths: {/new: {}}\n")
    mono.commit()
    marker(mono, "1-1")
    mono.commit()
    code, res = gate(mono, env={"PATH": "/usr/bin:/bin"})
    assert code == 1 and "not found" in check(res, "breaking")["findings"][0]["message"]


def test_narrow_story_needs_migrated_consumers(mono, tmp_path):
    path = fake_bin(tmp_path, "oasdiff", ODIFF)
    mono.branch("orch/1-4")
    mono.write(OAS, "openapi: 3.0.0\n# BREAK\n")
    mono.commit()
    marker(mono, "1-4")
    mono.commit()
    code, res = gate(mono, env={"PATH": path})
    assert code == 1 and "not yet migrated: user-service" in check(res, "breaking")["findings"][0]["message"]
    # user-service migrates (story 1.3 merged on main), then the narrow story passes.
    mono.checkout("main").branch("orch/1-3").write("services/user/app.py", "migrated\n")
    marker(mono, "1-3")
    mono.commit()
    mono.checkout("main").git("merge", "-q", "--no-ff", "orch/1-3", "-m", "merge 1-3")
    mono.checkout("orch/1-4").git("rebase", "-q", "main")
    code, res = gate(mono, env={"PATH": path})
    assert code == 0, res
    assert check(res, "breaking")["status"] == "warn"


def test_contract_changed_on_main_since_branch_point(mono, tmp_path):
    path = fake_bin(tmp_path, "oasdiff", ODIFF)
    mono.branch("orch/1-1")
    mono.write(OAS, "openapi: 3.0.0\npaths: {/a: {}}\n")
    mono.commit()
    marker(mono, "1-1")
    mono.commit()
    mono.checkout("main").write(OAS, "openapi: 3.0.0\npaths: {/b: {}}\n").commit("concurrent")
    mono.checkout("orch/1-1")
    code, res = gate(mono, env={"PATH": path})
    assert code == 1 and "changed on main since this branch started" in check(res, "pins")["findings"][0]["message"]


def test_ci_merge_ref_gets_the_same_verdict_as_the_branch_tip(mono, tmp_path):
    path = fake_bin(tmp_path, "oasdiff", ODIFF)
    mono.write(OAS, "openapi: 3.0.0\ninfo: {}\nservers: []\npaths: {}\n").commit("longer contract")
    mono.branch("orch/1-1")
    mono.write(OAS, "openapi: 3.0.0\ninfo: {}\nservers: []\npaths: {/a: {}}\n")
    mono.commit()
    marker(mono, "1-1")
    tip = mono.commit()
    mono.checkout("main").write(OAS, "openapi: 3.0.1\ninfo: {}\nservers: []\npaths: {}\n").commit("concurrent")
    mono.checkout("orch/1-1")
    tip_code, tip_res = gate(mono, env={"PATH": path})
    # What GitHub's refs/pull/N/merge looks like: base tip first, PR tip second.
    mono.git("checkout", "-q", "--detach", "main")
    mono.git("merge", "-q", "--no-ff", "orch/1-1", "-m", "Merge PR")
    code, res = gate(mono, env={"PATH": path})
    assert tip_code == code == 1, res
    assert check(res, "pins")["findings"] == check(tip_res, "pins")["findings"]
    assert res["head_sha"] == tip and "gating the PR tip" in res["head_resolved"]


def test_config_is_read_at_base_not_from_the_pr_or_working_tree(mono):
    mono.branch("sneaky")
    # A no-marker PR repoints the registry and touches a subproject.
    mono.write("_bmad/custom/config.toml", '[modules.orch]\norch_registry_dir = "nowhere"\n')
    mono.write(IMPL, "no story\n")
    mono.commit()
    # A gitignored personal layer on this machine must not change the verdict either.
    mono.write("_bmad/custom/config.user.toml", '[modules.orch]\norch_registry_dir = "nowhere"\n')
    code, res = gate(mono)
    assert code == 1 and "need a story marker" in check(res, "scope")["findings"][0]["message"]
    code, res = run_cli("config", "--repo", str(mono.path))
    assert res["config"]["registry_dir"] == R and res["coord_ref"] == "main"


def test_config_changes_take_effect_once_merged(mono):
    mono.git("mv", R, "orch-registry")
    mono.write("_bmad/custom/config.toml", '[modules.orch]\norch_registry_dir = "orch-registry"\n').commit()
    code, res = run_cli("registry", "--repo", str(mono.path))
    assert code == 0 and "payment-service" in res["subprojects"], res


def test_sprint_status_done_requires_marker(mono):
    mono.branch("status")
    mono.write(STATUS, SPRINT.replace("1-2-implement-payment-api: backlog", "1-2-implement-payment-api: done"))
    mono.commit()
    code, res = gate(mono)
    assert code == 1 and check(res, "sprint-status")["findings"][0]["code"] == "done-without-marker"


def test_story_pr_may_mark_itself_done(mono):
    mono.branch("orch/1-2")
    mono.write(IMPL, "x\n").write(STATUS, SPRINT.replace("1-2-implement-payment-api: backlog", "1-2-implement-payment-api: done"))
    marker(mono, "1-2")
    mono.commit()
    code, res = gate(mono)
    assert code == 0, res


def test_epic_close_record_requires_close_check(mono):
    mono.branch("close")
    mono.write("_bmad-output/orch/closed/epic-1.yaml", "epic: 1\n")
    mono.write(STATUS, SPRINT.replace("epic-1: in-progress", "epic-1: done"))
    mono.commit()
    code, res = gate(mono)
    msgs = [f["message"] for f in check(res, "sprint-status")["findings"]]
    assert code == 1 and any("close check fails" in m for m in msgs)


def test_archive_move_is_allowed_and_tampering_is_not(mono):
    mono.branch("orch/1-2")
    mono.write(IMPL, "x\n")
    marker(mono, "1-2")
    mono.commit()
    mono.checkout("main").git("merge", "-q", "--no-ff", "orch/1-2", "-m", "m")
    mono.branch("archive")
    mono.git("mv", ".orch/stories/1-2.yaml", "_tmp.yaml")
    (mono.path / ".orch/archive/epic-1").mkdir(parents=True)
    mono.git("mv", "_tmp.yaml", ".orch/archive/epic-1/1-2.yaml")
    mono.commit()
    assert gate(mono)[0] == 0
    mono.write(".orch/archive/epic-1/1-2.yaml", "story: '1-2'\nepic: 1\ncontract_pins: {}\n")
    mono.commit()
    code, res = gate(mono)
    assert code == 1 and "unchanged move" in check(res, "scope")["findings"][0]["message"]


def test_merge_driver_missing_in_local_clone(mono):
    mono.write(".gitattributes", f"{STATUS} merge=orch-sprint-status\n").commit()
    mono.branch("x").write("README.md", "y\n").commit()
    code, res = gate(mono)
    assert code == 1 and check(res, "merge-driver")["status"] == "fail"
    assert gate(mono, "--ci")[0] == 0
    code, res = run_cli("sprint-status", "install-driver", "--repo", str(mono.path))
    assert code == 0
    assert gate(mono)[0] == 0


def test_text_format(mono):
    mono.branch("x").write(IMPL, "no story\n").commit()
    code, out = gate(mono, "--format", "text")
    assert code == 1 and "[FAIL] scope" in out and "fix:" in out


def test_polyrepo_code_repo(tmp_path):
    coord = Repo(tmp_path / "coord")
    code_repo = Repo(tmp_path / "payment")
    url = str(code_repo.path)
    coord.write(f"{R}/payment-service.yaml", registry_yaml(
        "payment-service", "src", repo=url, exports=[("openapi", f"{C}/payment-service/openapi.yaml", "docs/openapi.yaml")]))
    coord.write(f"{C}/payment-service/openapi.yaml", "openapi: 3.0.0\n")
    coord.write("_bmad-output/planning-artifacts/epics.md", "## Epic 1: P\n### Story 1.2: Impl\n**Subproject:** payment-service\n")
    coord.commit()
    code_repo.write("src/app.py", "x\n").write("docs/openapi.yaml", "openapi: 3.0.0\n").commit()
    code_repo.branch("orch/1-2").write("src/app.py", "y\n")
    code, res = run_cli("marker", "write", "--story", "1.2", "--repo", str(code_repo.path), "--coord", str(coord.path))
    assert code == 0, res
    assert list(res["marker"]["contract_pins"]) == [f"{C}/payment-service/openapi.yaml"]
    code_repo.commit()
    code, res = run_cli("gate", "--repo", str(code_repo.path), "--coord", str(coord.path), "--base", "main", "--repo-id", url)
    assert code == 0, res
    code_repo.write("README.md", "outside\n").commit()
    code, res = run_cli("gate", "--repo", str(code_repo.path), "--coord", str(coord.path), "--base", "main", "--repo-id", url)
    assert code == 1
    # merged status is pulled from the code repo's main through the fetch cache
    code_repo.checkout("main").git("merge", "-q", "orch/1-2")
    code, res = run_cli("merged", "--repo", str(coord.path))
    assert "1-2" in res["merged"], res


def test_wrong_repo_for_story(tmp_path, mono):
    other = Repo(tmp_path / "other")
    other.write("x.txt", "1").commit()
    other.branch("orch/1-2")
    other.write(".orch/stories/1-2.yaml", yaml.safe_dump({"story": "1-2", "epic": 1, "contract_pins": {}}))
    other.commit()
    code, res = run_cli("gate", "--repo", str(other.path), "--coord", str(mono.path), "--base", "main", "--repo-id", "github.com/x/other")
    assert code == 1 and "not this repo" in check(res, "marker")["findings"][0]["message"]


def test_merge_driver_resolves_real_git_conflict(mono):
    assert run_cli("sprint-status", "install-driver", "--repo", str(mono.path))[0] == 0
    mono.commit("driver")
    mono.branch("a").write(STATUS, SPRINT.replace("1-2-implement-payment-api: backlog", "1-2-implement-payment-api: review")).commit()
    mono.checkout("main").branch("b").write(STATUS, SPRINT.replace("1-2-implement-payment-api: backlog", "1-2-implement-payment-api: in-progress")
                                            .replace("1-3-user-service-calls-payments: backlog", "1-3-user-service-calls-payments: in-progress")).commit()
    mono.git("merge", "-q", "a", "-m", "m")
    text = (mono.path / STATUS).read_text()
    assert "1-2-implement-payment-api: review" in text and "1-3-user-service-calls-payments: in-progress" in text
    assert "<<<<<<<" not in text
