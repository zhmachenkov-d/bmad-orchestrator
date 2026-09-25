import os

import yaml
from conftest import C, EPICS, R, SPRINT, Repo, check, fake_bin, registry_yaml, run_cli

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


def test_result_records_inputs_and_dirty_tree_never_changes_the_verdict(mono):
    mono.branch("orch/1-2").write(IMPL, "x\n")
    marker(mono, "1-2")
    mono.commit()
    mono.write("services/payment/wip.py", "not committed\n")
    code, res = gate(mono)
    assert code == 0, res
    assert res["base_sha"] == mono.git("rev-parse", "main") and res["coord_sha"] == res["base_sha"]
    assert res["head_sha"] == mono.git("rev-parse", "HEAD") and res["offline"] is False
    assert [n["code"] for n in res["notices"]] == ["dirty-worktree"] and "wip.py" in res["notices"][0]["message"]
    code, res = gate(mono, "--ci")
    assert code == 0 and res["notices"] == []
    mono.write("README.md", "edited, not committed\n")  # a tracked edit sorts first and starts with " M"
    code, res = gate(mono)
    assert ": README.md, services/payment/wip.py" in res["notices"][0]["message"]


def test_shallow_clone_names_the_fix(mono, tmp_path):
    mono.branch("orch/1-2").write(IMPL, "x\n").commit()
    shallow = tmp_path / "shallow"
    mono.git("clone", "-q", "--depth", "1", "--branch", "orch/1-2", f"file://{mono.path}", str(shallow))
    mono.git("-C", str(shallow), "fetch", "-q", "--depth", "1", "origin", "main:main")
    code, res = run_cli("gate", "--repo", str(shallow), "--base", "main")
    assert code == 2 and "shallow clone" in res["error"] and "fetch-depth: 0" in res["error"]


def test_offline_is_rejected_in_ci(mono):
    mono.branch("x").write("README.md", "y\n").commit()
    code, res = gate(mono, "--ci", "--offline")
    assert code == 2 and "--offline" in res["error"]


def unreadable_user_service(mono):
    mono.write(f"{R}/user-service.yaml", registry_yaml("user-service", "services/user", imports=["payment-service"],
                                                       repo="/nonexistent/user-service"))
    mono.commit("user-service moves to its own repo")


def test_unreadable_repo_is_unverifiable_not_unmerged(mono):
    unreadable_user_service(mono)
    mono.branch("status")
    mono.write(STATUS, SPRINT.replace("1-3-user-service-calls-payments: backlog", "1-3-user-service-calls-payments: done"))
    mono.commit()
    code, res = gate(mono)
    fails = [f for f in check(res, "sprint-status")["findings"] if f["level"] == "fail"]
    assert code == 1 and [f["code"] for f in fails] == ["done-unverifiable"]
    assert res["unread_repos"][0]["code"] == "repo-unreadable" and res["unread_repos"][0]["subprojects"] == ["user-service"]
    code, res = run_cli("epic", "close-check", "--epic", "1", "--repo", str(mono.path))
    assert code == 1 and any(p.get("code") == "unverifiable" and p["story"] == "1-3" for p in res["problems"])


def test_narrow_story_with_unreadable_consumer_says_so(mono, tmp_path):
    path = fake_bin(tmp_path, "oasdiff", ODIFF)
    unreadable_user_service(mono)
    mono.branch("orch/1-4").write(OAS, "openapi: 3.0.0\n# BREAK\n").commit()
    marker(mono, "1-4")
    mono.commit()
    code, res = gate(mono, env={"PATH": path})
    findings = check(res, "breaking")["findings"]
    assert code == 1 and findings[0]["code"] == "consumers-unverifiable" and "user-service" in findings[0]["message"]
    assert not any("not yet migrated" in f["message"] for f in findings)


def test_coordination_repo_found_from_committed_config(tmp_path):
    coord = Repo(tmp_path / "coord")
    code_repo = Repo(tmp_path / "payment")
    url = str(code_repo.path)
    coord.write(f"{R}/payment-service.yaml", registry_yaml("payment-service", "src", repo=url))
    coord.write("_bmad-output/planning-artifacts/epics.md", "## Epic 1: P\n### Story 1.2: Impl\n**Subproject:** payment-service\n")
    coord.commit()
    code_repo.write("src/app.py", "x\n").write("_bmad/custom/config.toml", '[modules.orch]\ncoordination_repo = "../coord"\n').commit()
    code_repo.branch("orch/1-2").write("src/app.py", "y\n")
    assert run_cli("marker", "write", "--story", "1.2", "--repo", url)[0] == 0
    code_repo.commit()
    code, res = run_cli("gate", "--repo", url, "--base", "main", "--repo-id", url)
    assert code == 0, res
    code_repo.checkout("main").write("_bmad/custom/config.toml", '[modules.orch]\ncoordination_repo = "git@example.com:org/coord.git"\n').commit()
    code, res = run_cli("config", "--repo", url)
    assert code == 2 and "--coord" in res["error"]


def test_findings_carry_codes_and_mechanical_fixes(mono):
    mono.write(".gitattributes", f"{STATUS} merge=orch-sprint-status\n").commit()
    mono.branch("orch/1-3")
    mono.write("services/user/app.py", "uses payments\n")
    marker(mono, "1-3")
    mono.commit()
    mono.checkout("main").write(OAS, "openapi: 3.0.0\npaths: {/pay: {}}\n").write(
        "services/payment/openapi.yaml", "openapi: 3.0.0\npaths: {/pay: {}}\n").commit("contract moved")
    mono.checkout("orch/1-3")
    code, res = gate(mono)
    assert code == 1
    assert all("code" in f for c in res["checks"] for f in c["findings"])
    stale = check(res, "pins")["findings"][0]
    assert stale["code"] == "pin-stale"
    cmd = stale["fix"]["command"]
    assert cmd[:2] == ["uv", "run"] and cmd[2].endswith("orch-gate/scripts/orch.py")
    assert cmd[3:] == ["marker", "write", "--story", "1-3", "--base", "main"] and "rebase" in stale["fix"]["precondition"]
    driver = check(res, "merge-driver")["findings"][0]
    assert driver["code"] == "driver-missing" and driver["fix"]["command"][3:] == ["sprint-status", "install-driver"]
    code, out = gate(mono, "--format", "text")
    assert "[pin-stale]" in out and f"then run: uv run {cmd[2]} marker write --story 1-3 --base main" in out


def test_conformance_failure_shows_the_diff(mono):
    mono.branch("orch/1-2")
    mono.write("services/payment/openapi.yaml", "openapi: 3.0.0\npaths: {/edited: {}}\n")
    marker(mono, "1-2")
    mono.commit()
    code, res = gate(mono)
    detail = check(res, "conformance")["findings"][0]["detail"]
    assert code == 1 and "-paths: {}" in detail and "+paths: {/edited: {}}" in detail


def test_markdown_format_for_ci_summaries(mono):
    mono.branch("x").write(IMPL, "no story\n").commit()
    code, out = gate(mono, "--format", "markdown", "--ci")
    assert code == 1 and out.startswith("### orch-gate: ❌ FAIL") and "| scope | ❌ fail |" in out and "`needs-story`" in out


def test_internal_error_is_exit_2_not_a_failing_verdict(mono, monkeypatch):
    from orchlib import gate as gate_mod

    def boom(ctx):
        raise KeyError("registry field")
    monkeypatch.setattr(gate_mod, "run", boom)
    code, res = gate(mono)
    assert code == 2 and res["error"].startswith("internal error: KeyError")


ATLAS = """import os, sys
d = sys.argv[sys.argv.index('--dir') + 1][len('file://'):]
files = sorted(os.listdir(d))
print(files)
sys.exit(1 if 'wip.sql' in files else 0)
"""


def test_db_schema_detector_lints_the_committed_head_not_the_working_tree(mono, tmp_path):
    path = fake_bin(tmp_path, "atlas", ATLAS)
    mig = f"{C}/orders-db/migrations"
    mono.write(f"{R}/orders-db.yaml", "name: orders-db\nrepo: .\npath: db/orders\nallowed_read: ['db/orders/**']\n"
               f"allowed_write: ['db/orders/**']\ncontracts:\n  exports:\n    - {{type: db-schema, canonical: {mig}, dev_url: 'docker://postgres'}}\n"
               "  imports: []\n")
    mono.write(f"{mig}/001.sql", "create table a (id int);\n").commit()
    mono.branch("orch/1-1").write(f"{mig}/002.sql", "alter table a add b int;\n").commit()
    marker(mono, "1-1")
    mono.commit()
    mono.write(f"{mig}/wip.sql", "drop table a;\n")  # uncommitted, must not reach the detector
    code, res = gate(mono, env={"PATH": path})
    assert code == 0, res
    used = res["detectors_used"][0]
    assert used["type"] == "db-schema" and used["inputs"] == {"dev_url_source": "registry"}
    assert not (mono.path / ".git" / "worktrees").exists() or not any((mono.path / ".git" / "worktrees").iterdir())


def test_deps_probe_checks_detector_verdicts(mono, tmp_path):
    good = fake_bin(tmp_path, "oasdiff", "import sys\nif '--version' in sys.argv: print('oasdiff v1.2.3'); sys.exit(0)\n"
                    "sys.exit(0 if '/b:' in open(sys.argv[3]).read() else 1)\n")
    code, res = run_cli("deps", "--probe", "--repo", str(mono.path), env={"PATH": good})
    oas = res["detectors"][0]
    assert code == 0 and oas["version"] == "oasdiff v1.2.3" and oas["probe"]["status"] == "pass", res
    (tmp_path / "lax").mkdir()
    lax = fake_bin(tmp_path / "lax", "oasdiff", "import sys\nsys.exit(0)\n")
    code, res = run_cli("deps", "--probe", "--repo", str(mono.path), env={"PATH": lax})
    probe = res["detectors"][0]["probe"]
    assert code == 1 and probe["status"] == "fail" and probe["breaking_change"] == "ok"


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


# ---- setup fails closed ----

def test_repo_without_orch_setup_fails_instead_of_passing(tmp_path):
    r = Repo(tmp_path / "fresh")
    r.write("README.md", "x\n").commit()
    r.branch("x").write("services/pay/a.py", "x\n").commit()
    code, res = gate(r)
    codes = {f["code"] for f in check(res, "setup")["findings"]}
    assert code == 1 and {"registry-empty", "no-epics"} <= codes, res


def test_broken_registry_fails_every_pr_until_a_repair_pr(mono):
    good = (mono.path / f"{R}/user-service.yaml").read_text()
    mono.write(f"{R}/user-service.yaml", "name: user-service\npath: services/user\nallowed_write: [services/user/**\n").commit("broken")
    # The broken entry drops out of the registry, so without the setup check this markerless PR would pass.
    mono.branch("sneaky").write("services/user/app.py", "no story\n").commit()
    code, res = gate(mono)
    assert code == 1 and "registry-invalid" in {f["code"] for f in check(res, "setup")["findings"]}
    mono.checkout("main").branch("repair").write(f"{R}/user-service.yaml", good).commit()
    code, res = gate(mono)
    assert code == 0, res
    assert check(res, "setup")["findings"][0]["code"] == "setup-repair"
    # A "repair" that also changes anything outside registry and planning files is judged normally.
    mono.write("services/user/app.py", "sneak along\n").commit()
    assert gate(mono)[0] == 1


def test_registry_entry_without_repo_is_not_defaulted_to_this_repo(mono):
    mono.write(f"{R}/user-service.yaml", "name: user-service\npath: services/user\nallowed_write: ['services/user/**']\n").commit()
    code, res = run_cli("registry", "--repo", str(mono.path))
    assert code == 1 and "user-service" not in res["subprojects"]
    assert any(i["code"] == "missing-field" and "'repo'" in i["message"] for i in res["issues"])


def test_wildcard_free_allowed_write_protects_the_directory(mono):
    text = (mono.path / f"{R}/user-service.yaml").read_text().replace('allowed_write: ["services/user/**"]',
                                                                      'allowed_write: ["services/user"]')
    mono.write(f"{R}/user-service.yaml", text).commit()
    mono.branch("x").write("services/user/app.py", "no story\n").commit()
    code, res = gate(mono)
    assert code == 1 and check(res, "scope")["findings"][0]["code"] == "needs-story"


# ---- directory contracts ----

EVENTS = f"{C}/events/proto"
BUF = """import os, sys
h, b = sys.argv[2], sys.argv[4]
assert sorted(os.listdir(h)) == sorted(os.listdir(b)) == ['a.proto', 'b.proto'], (os.listdir(h), os.listdir(b))
sys.exit(1 if any('BREAK' in open(os.path.join(h, f)).read() for f in os.listdir(h)) else 0)
"""


def events_repo(mono):
    mono.write(f"{R}/events.yaml", registry_yaml("events", "services/events",
                                                 exports=[("protobuf", EVENTS, "services/events/proto")]))
    for root in (EVENTS, "services/events/proto"):
        mono.write(f"{root}/a.proto", 'syntax = "proto3";\nmessage A {}\n').write(f"{root}/b.proto", 'syntax = "proto3";\nmessage B {}\n')
    mono.write("services/events/app.py", "x\n")
    epics = mono.path / "_bmad-output/planning-artifacts/epics.md"
    mono.write("_bmad-output/planning-artifacts/epics.md", epics.read_text() + (
        "\n### Story 1.5: Events impl\n**Subproject:** events\n**Depends on:** 1.1\n"
        "\n### Story 1.6: Events contract\n**Subproject:** contracts\n**Depends on:** none\n**Contract change:** expand\n"))
    mono.commit("events")


def test_directory_contract_copies_compare_file_by_file(mono):
    events_repo(mono)
    mono.branch("orch/1-5").write("services/events/app.py", "y\n")
    marker(mono, "1-5")
    mono.commit()
    code, res = gate(mono)
    assert code == 0 and check(res, "conformance")["status"] == "pass", res
    mono.write("services/events/proto/b.proto", 'syntax = "proto3";\nmessage B { string x = 1; }\n').commit()
    code, res = gate(mono)
    drift = check(res, "conformance")["findings"][0]
    assert code == 1 and drift["code"] == "copy-drift" and "1 changed" in drift["message"]
    assert "+message B { string x = 1; }" in drift["detail"]


def test_protobuf_directory_contract_runs_the_detector_on_both_trees(mono, tmp_path):
    path = fake_bin(tmp_path, "buf", BUF)
    events_repo(mono)
    mono.branch("orch/1-6").write(f"{EVENTS}/b.proto", 'syntax = "proto3";\nmessage B { string x = 1; }\n').commit()
    marker(mono, "1-6")
    mono.commit()
    code, res = gate(mono, env={"PATH": path})
    assert code == 0, res
    mono.write(f"{EVENTS}/a.proto", 'syntax = "proto3";\n// BREAK\n').commit()
    marker(mono, "1-6")
    mono.commit()
    code, res = gate(mono, env={"PATH": path})
    assert code == 1 and check(res, "breaking")["findings"][0]["code"] == "breaking-change"


def test_openapi_canonical_must_be_a_file(mono):
    mono.write(f"{C}/payment-service/api/openapi.yaml", "openapi: 3.0.0\n")
    mono.write(f"{R}/payment-service.yaml", registry_yaml(
        "payment-service", "services/payment", exports=[("openapi", f"{C}/payment-service/api", None)])).commit()
    code, res = run_cli("registry", "--repo", str(mono.path))
    assert code == 1 and any(i["code"] == "canonical-kind" for i in res["issues"])


# ---- narrow stories, archives ----

def test_narrow_verdict_does_not_depend_on_depends_on_order(mono, tmp_path):
    path = fake_bin(tmp_path, "oasdiff", ODIFF)
    mono.branch("orch/1-3").write("services/user/app.py", "migrated\n")
    marker(mono, "1-3")
    mono.commit()
    mono.checkout("main").git("merge", "-q", "--no-ff", "orch/1-3", "-m", "merge 1-3")
    for order in ("1.3, 1.5", "1.5, 1.3"):
        # user-service has two stories in Depends on; only 1.3 is merged, so it has not fully migrated either way.
        mono.checkout("main").write("_bmad-output/planning-artifacts/epics.md", EPICS + (
            "\n### Story 1.5: More user work\n**Subproject:** user-service\n**Depends on:** 1.1\n"
            f"\n### Story 1.6: Narrow again\n**Subproject:** contracts\n**Depends on:** {order}\n**Contract change:** narrow\n"))
        mono.commit(f"plan {order}")
        mono.git("checkout", "-q", "-B", "orch/1-6")
        mono.write(OAS, "openapi: 3.0.0\n# BREAK\n").commit()
        marker(mono, "1-6")
        mono.commit()
        code, res = gate(mono, env={"PATH": path})
        msgs = [f["message"] for f in check(res, "breaking")["findings"]]
        assert code == 1 and any("not yet migrated: user-service" in m for m in msgs), (order, res)


def test_archive_under_the_wrong_epic_is_rejected(mono):
    mono.branch("orch/1-2").write(IMPL, "x\n")
    marker(mono, "1-2")
    mono.commit()
    mono.checkout("main").git("merge", "-q", "--no-ff", "orch/1-2", "-m", "m")
    mono.branch("archive")
    (mono.path / ".orch/archive/epic-2").mkdir(parents=True)
    mono.git("mv", ".orch/stories/1-2.yaml", ".orch/archive/epic-2/1-2.yaml")
    mono.commit()
    code, res = gate(mono)
    assert code == 1 and "belongs to epic 1" in check(res, "scope")["findings"][0]["message"]
    mono.checkout("main").git("merge", "-q", "archive")
    code, res = run_cli("epic", "close-check", "--epic", "1", "--repo", str(mono.path))
    assert any(p["story"] == "1-2" and "not archived under .orch/archive/epic-1/" in p["message"] for p in res["problems"])


# ---- verdict inputs, runnable fixes, config ----

def test_polyrepo_verdict_inputs_and_runnable_fixes(tmp_path):
    coord = Repo(tmp_path / "coord")
    code_repo = Repo(tmp_path / "payment")
    url = str(code_repo.path)
    coord.write(f"{R}/payment-service.yaml", registry_yaml("payment-service", "src", repo=url) + "branch: develop\n")
    coord.write("_bmad-output/planning-artifacts/epics.md", "## Epic 1: P\n### Story 1.2: Impl\n**Subproject:** payment-service\n")
    coord.write(STATUS, "development_status:\n  epic-1: in-progress\n  1-2-impl: backlog\n")
    coord.commit()
    code_repo.write("src/app.py", "x\n").commit()
    code_repo.git("branch", "develop")
    code_repo.branch("orch/1-2").write("src/app.py", "y\n")
    code_repo.write(".orch/stories/1-2.yaml", yaml.safe_dump({"story": "1-2", "epic": 9, "contract_pins": {}})).commit()
    # No --base: a code repo is gated against its registry branch, the one merged() reads.
    code, res = run_cli("gate", "--repo", url, "--coord", str(coord.path), "--repo-id", url)
    assert res["base"] == "develop" and res["base_source"] == "registry branch of payment-service", res
    assert res["coord_ref_source"] == "default branch name"
    mismatch = next(f for f in check(res, "marker")["findings"] if f["code"] == "marker-epic-mismatch")
    cmd = mismatch["fix"]["command"]
    assert cmd[cmd.index("--coord") + 1] == "../coord" and "--base" not in cmd
    code, res = run_cli("gate", "--repo", url, "--coord", str(coord.path), "--repo-id", "github.com/x/other")
    assert code == 1 and any(f["code"] == "repo-unregistered" for f in check(res, "setup")["findings"])
    # A coordination PR that reads the code repo records which commit it read.
    code_repo.checkout("develop").git("merge", "-q", "orch/1-2")
    coord.branch("status").write(STATUS, "development_status:\n  epic-1: in-progress\n  1-2-impl: done\n").commit()
    code, res = run_cli("gate", "--repo", str(coord.path), "--base", "main")
    assert code == 0, res
    assert res["repos_read"] == [{"repo": url, "branch": "develop", "sha": code_repo.git("rev-parse", "develop")}]


def test_personal_layers_never_reach_the_verdict_but_user_settings_resolve_locally(mono):
    mono.write("_bmad/config.user.toml", '[modules.orch]\norch_registry_dir = "nowhere"\n').commit("tracked personal layer")
    mono.write("_bmad/custom/config.user.toml", '[modules.orch]\norch_worktrees_dir = "../my-trees"\n')
    code, res = run_cli("config", "--repo", str(mono.path))
    assert res["config"]["registry_dir"] == R and res["config"]["worktrees_dir"] == "../my-trees", res
    (mono.path / "_bmad/custom/config.user.toml").unlink()
    mono.git("remote", "add", "origin", "git@example.com:org/shop.git")
    code, res = run_cli("config", "--repo", str(mono.path))
    assert res["config"]["project_name"] == "shop" and res["config"]["worktrees_dir"] == "../shop-worktrees"


def test_missing_coordination_main_names_coord_ref(tmp_path):
    r = Repo(tmp_path / "trunk")
    r.write("a.txt", "1").commit()
    r.git("branch", "-m", "trunk")
    code, res = run_cli("config", "--repo", str(r.path))
    assert code == 2 and "--coord-ref" in res["error"]
