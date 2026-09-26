"""status, plan-check, epic close and report: the commands orch-status (and orch-next's warnings) run on."""

import json
import os
from types import SimpleNamespace

import yaml
from conftest import C, EPICS, R, check, fake_bin, registry_yaml, run_cli

from orchlib import status as status_mod

OAS = f"{C}/payment-service/openapi.yaml"
COPY = "services/payment/openapi.yaml"
STATUS = "_bmad-output/implementation-artifacts/sprint-status.yaml"
OLD = "2020-01-01T00:00:00Z"

# EPICS plus the exporter adopting the narrowed contract, so every pin can converge.
EPICS_ADOPT = EPICS + """
### Story 1.5: Payment service drops the legacy field
**Subproject:** payment-service
**Depends on:** 1.4
"""


def cli(r, *argv, env=None):
    return run_cli(*argv, "--repo", str(r.path), env=env)


def land(r, key, files):
    """Build a story on story/<key> with its marker and merge it into main, as a merged PR would."""
    r.branch(f"story/{key}")
    for path, text in files.items():
        r.write(path, text)
    r.commit(f"story {key}")
    code, res = cli(r, "marker", "write", "--story", key)
    assert code == 0, res
    r.commit(f"marker {key}")
    r.checkout("main").git("merge", "-q", "--no-ff", f"story/{key}")
    r.git("branch", "-D", f"story/{key}")


def by_key(res):
    return {s["key"]: s for s in res["stories"]}


def codes(res, story=None):
    return {a["code"] for a in res["anomalies"] if story is None or a["story"] == story}


def test_status_states_critical_path_and_bottleneck(mono):
    assert cli(mono, "claim", "create", "--story", "1-1", "--user", "Ann <a@x>")[0] == 0
    code, res = cli(mono, "status", "--no-host")
    assert code == 0, res
    s = by_key(res)
    assert s["1-1"]["state"] == "in-progress" and s["1-1"]["claimant"] == "Ann <a@x>"
    assert s["1-2"]["state"] == "blocked" and s["1-2"]["blocked_by"] == ["1-1"]
    assert s["1-1"]["downstream"] == 3
    epic = res["epics"][0]
    assert epic["critical_path"] == ["1-1", "1-3", "1-4"] and epic["state"] == "open"
    bottleneck = next(a for a in res["anomalies"] if a["code"] == "contract-bottleneck")
    assert bottleneck["story"] == "1-1" and set(bottleneck["waiting"]) == {"1-2", "1-3"}


def test_stale_claim_offers_take_over_that_works(mono):
    cli(mono, "claim", "create", "--story", "1-1", "--user", "Ann <a@x>", env={"GIT_COMMITTER_DATE": OLD})
    code, res = cli(mono, "status", "--no-host")
    stale = next(a for a in res["anomalies"] if a["code"] == "stale-claim")
    take = next(a["args"] for a in stale["actions"] if a["label"] == "take over")
    code, res = cli(mono, *take, "--user", "Bob <b@x>")
    assert code == 0 and res["ok"], res
    _, res = cli(mono, "status", "--no-host")
    assert by_key(res)["1-1"]["claimant"] == "Bob <b@x>" and "stale-claim" not in codes(res)


def test_story_branch_activity_keeps_an_old_claim_fresh(mono):
    cli(mono, "claim", "create", "--story", "1-1", "--user", "Ann <a@x>", env={"GIT_COMMITTER_DATE": OLD})
    mono.branch("story/1-1").write(OAS, "openapi: 3.0.0\npaths: {/pay: {}}\n").commit("wip")
    mono.checkout("main")
    _, res = cli(mono, "status", "--no-host")
    assert by_key(res)["1-1"]["branch"] == "story/1-1" and "stale-claim" not in codes(res)


def test_claim_left_on_a_merged_story_is_offered_for_release(mono):
    cli(mono, "claim", "create", "--story", "1-1", "--user", "Ann <a@x>")
    land(mono, "1-1", {OAS: "openapi: 3.0.0\npaths: {/pay: {}}\n"})
    _, res = cli(mono, "status", "--no-host")
    assert by_key(res)["1-1"]["state"] == "done" and by_key(res)["1-2"]["state"] == "ready"
    assert "claim-on-merged" in codes(res, "1-1")


def _run_epic(mono, adopt: bool):
    mono.write("_bmad-output/planning-artifacts/epics.md", EPICS_ADOPT if adopt else EPICS).commit("plan")
    v1, v2 = "openapi: 3.0.0\npaths: {/pay: {}, /legacy: {}}\n", "openapi: 3.0.0\npaths: {/pay: {}}\n"
    land(mono, "1-1", {OAS: v1})                                   # expand
    land(mono, "1-2", {COPY: v1, "services/payment/app.py": "v1\n"})  # exporter builds against v1
    land(mono, "1-3", {"services/user/app.py": "migrated\n"})      # consumer migrates
    land(mono, "1-4", {OAS: v2})                                   # narrow, depends on 1.3
    if adopt:
        land(mono, "1-5", {COPY: v2})                              # exporter adopts v2


def test_pin_drift_names_the_unmigrated_exporter_and_drafts_its_story(mono):
    _run_epic(mono, adopt=False)
    _, res = cli(mono, "status", "--no-host")
    drift = [a for a in res["anomalies"] if a["code"] == "pin-drift"]
    assert [a["story"] for a in drift] == ["1-2"], drift       # 1-3 migrated: 1.4 depends on it
    assert drift[0]["migration_draft"]["subproject"] == "payment-service"
    assert drift[0]["migration_draft"]["depends_on"] == ["1.4"]
    assert res["epics"][0]["state"] == "drift"


def test_close_epic_in_two_gated_passes_after_expand_migrate_narrow(mono):
    _run_epic(mono, adopt=True)
    code, res = cli(mono, "epic", "close", "--epic", "1")
    assert code == 0 and res["pass"] == "archive" and "published" not in res, res
    assert len(res["repos"][0]["moves"]) == 5
    assert not mono.git("branch", "--list", "orch/close-epic-1")

    code, res = cli(mono, "epic", "close", "--epic", "1", "--push", "--user", "Ann <a@x>")
    assert res["published"][0]["status"] == "created-local", res
    mono.checkout("orch/close-epic-1")
    code, gate = cli(mono, "gate", "--base", "main")
    assert code == 0, gate
    mono.checkout("main").git("merge", "-q", "--no-ff", "orch/close-epic-1")

    code, res = cli(mono, "epic", "close", "--epic", "1", "--push", "--user", "Ann <a@x>")
    assert code == 0 and res["pass"] == "record", res
    mono.checkout("orch/close-epic-1-record")
    code, gate = cli(mono, "gate", "--base", "main")
    assert code == 0, gate
    mono.checkout("main").git("merge", "-q", "--no-ff", "orch/close-epic-1-record")

    status = yaml.safe_load((mono.path / STATUS).read_text())["development_status"]
    assert status["epic-1"] == "done" and status["1-4-remove-legacy-field"] == "done"
    retro = json.loads((mono.path / "_bmad-output/implementation-artifacts/orch/reports/orch-epic-1.json").read_text())
    assert retro["epic"] == 1 and retro["planned_critical_path"][0] == "1-1"
    code, res = cli(mono, "epic", "close", "--epic", "1")
    assert res["pass"] == "closed"
    _, res = cli(mono, "status", "--no-host")
    assert res["epics"][0]["state"] == "closed"


def test_close_is_blocked_until_every_story_is_merged(mono):
    code, res = cli(mono, "epic", "close", "--epic", "1", "--push")
    assert code == 1 and res["pass"] == "blocked" and "published" not in res
    assert {p["story"] for p in res["problems"]} == {"1-1", "1-2", "1-3", "1-4"}


def test_plan_check_verdicts(mono):
    code, res = cli(mono, "plan-check")
    assert code == 0 and res["verdict"] == "PASS", res
    bad = EPICS.replace("**Depends on:** 1.3\n**Contract change:** narrow", "**Depends on:** 1.2\n**Contract change:** narrow")
    mono.write("_bmad-output/planning-artifacts/epics.md", bad).commit()
    code, res = cli(mono, "plan-check")
    assert code == 1 and res["verdict"] == "FAIL"
    assert {f["code"] for f in res["findings"]} == {"narrow-without-migration"}


def test_plan_check_concerns(mono):
    text = EPICS.replace("**Contract change:** expand", "**Contract change:** none")
    text += "\n### Story 1.5: Payment waits on users\n**Subproject:** payment-service\n**Depends on:** 1.3\n"
    mono.write("_bmad-output/planning-artifacts/epics.md", text).commit()
    code, res = cli(mono, "plan-check")
    assert code == 0 and res["verdict"] == "CONCERNS", res
    assert {f["code"] for f in res["findings"]} == {"narrow-without-expand", "dependency-not-imported"}


def test_report_writes_html_and_pdf_through_chromium(mono, tmp_path):
    out = tmp_path / "r.html"
    path = fake_bin(tmp_path, "fakechrome", "import sys\n"
                    "arg = next(a for a in sys.argv if a.startswith('--print-to-pdf='))\n"
                    "open(arg.split('=', 1)[1], 'wb').write(b'%PDF-1.4')\n")
    code, res = cli(mono, "report", "--epic", "1", "--no-host", "--pdf", "-o", str(out),
                    env={"PATH": path, "ORCH_CHROME": "fakechrome"})
    assert code == 0 and res["pdf"] and res["pdf_error"] is None, res
    page = out.read_text()
    assert "<svg" in page and "epic 1" in page and "1.4" in page
    code, res = cli(mono, "report", "--epic", "1", "--no-host", "--pdf", "-o", str(out),
                    env={"PATH": "/usr/bin:/bin", "ORCH_CHROME": ""})
    assert code == 0 and res["pdf"] is None and "print it to PDF" in res["pdf_error"]


def test_open_reviews_come_from_gh(tmp_path, monkeypatch):
    rows = [{"headRefName": "story/1-2", "createdAt": "2020-01-01T00:00:00Z", "url": "https://github.com/acme/pay/pull/7"},
            {"headRefName": "feature/x", "createdAt": "2020-01-01T00:00:00Z", "url": "u"}]
    monkeypatch.setenv("PATH", fake_bin(tmp_path, "gh", f"print({json.dumps(json.dumps(rows))})\n"))
    reg = {"pay": SimpleNamespace(repo="git@github.com:acme/pay.git")}
    found, sources, notices = status_mod.reviews(reg, tmp_path)
    assert sources == {"github.com/acme/pay": "gh"} and not notices
    assert list(found) == [("github.com/acme/pay", "1-2")]


def test_missing_review_host_degrades_with_a_notice(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", str(tmp_path))
    reg = {"pay": SimpleNamespace(repo="https://gitlab.example.com/acme/pay.git")}
    found, sources, notices = status_mod.reviews(reg, tmp_path)
    assert not found and sources == {"gitlab.example.com/acme/pay": "none"}
    assert notices[0]["code"] == "review-host-unavailable" and "glab" in notices[0]["message"]
    assert os.environ["PATH"] == str(tmp_path)


def test_polyrepo_close_pushes_the_archive_branch_to_the_code_repo(tmp_path):
    from conftest import R, Repo, registry_yaml
    coord, code_repo = Repo(tmp_path / "coord"), Repo(tmp_path / "payment")
    url = str(code_repo.path)
    coord.write(f"{R}/payment-service.yaml", registry_yaml(
        "payment-service", "src", repo=url, exports=[("openapi", OAS, "docs/openapi.yaml")]))
    coord.write(OAS, "openapi: 3.0.0\n")
    coord.write("_bmad-output/planning-artifacts/epics.md", "## Epic 1: P\n### Story 1.2: Impl\n**Subproject:** payment-service\n")
    coord.commit()
    code_repo.write("src/app.py", "x\n").write("docs/openapi.yaml", "openapi: 3.0.0\n").commit()
    flags = ("--coord", str(coord.path))
    code_repo.branch("story/1-2").write("src/app.py", "y\n")
    assert run_cli("marker", "write", "--story", "1.2", "--repo", url, *flags)[0] == 0
    code_repo.commit()
    _, res = run_cli("status", "--no-host", "--repo", str(coord.path))
    assert by_key(res)["1-2"]["branch"] == "story/1-2", res     # branch read from the code repo through the cache
    code_repo.checkout("main").git("merge", "-q", "--no-ff", "story/1-2")

    code, res = run_cli("epic", "close", "--epic", "1", "--push", "--user", "Ann <a@x>", "--repo", str(coord.path))
    assert code == 0 and res["published"] == [{"repo": url, "base": "main", "branch": "orch/close-epic-1", "status": "pushed"}], res
    code_repo.checkout("orch/close-epic-1")
    code, gate = run_cli("gate", "--repo", url, *flags, "--base", "main", "--repo-id", url)
    assert code == 0, gate
    code_repo.checkout("main").git("merge", "-q", "--no-ff", "orch/close-epic-1")
    code, res = run_cli("epic", "close", "--epic", "1", "--push", "--user", "Ann <a@x>", "--repo", str(coord.path))
    assert code == 0 and res["pass"] == "record" and res["published"][0]["status"] == "created-local", res
    code, res = run_cli("epic", "close", "--epic", "1", "--push", "--user", "Ann <a@x>", "--repo", str(coord.path))
    assert res["published"][0]["status"] == "exists"


# --- regressions from the analyze report ---

REVERSED = """## Epic 1: Payments
### Story 1.1: First contract change
**Subproject:** contracts
**Depends on:** none
**Contract change:** expand

### Story 1.2: Second contract change
**Subproject:** contracts
**Depends on:** none
**Contract change:** expand

### Story 1.3: Payment adopts the contract
**Subproject:** payment-service
**Depends on:** 1.1, 1.2
"""


def test_contract_chain_follows_merge_order_not_plan_order(mono):
    mono.write("_bmad-output/planning-artifacts/epics.md", REVERSED).commit("plan")
    v1, v2 = "openapi: 3.0.0\npaths: {/a: {}}\n", "openapi: 3.0.0\npaths: {/a: {}, /b: {}}\n"
    land(mono, "1-2", {OAS: v1})       # the later story in the plan merges first
    land(mono, "1-1", {OAS: v2})       # the earlier one rebases on it and merges second
    land(mono, "1-3", {COPY: v2})
    _, res = cli(mono, "status", "--no-host")
    assert "pin-drift" not in codes(res), res["anomalies"]
    assert res["epics"][0]["state"] == "archive-needed"


def _poly(tmp_path, extra_repo=None):
    from conftest import R, Repo, registry_yaml
    coord, code_repo = Repo(tmp_path / "coord"), Repo(tmp_path / "payment")
    url = str(code_repo.path)
    coord.write(f"{R}/payment-service.yaml", registry_yaml(
        "payment-service", "src", repo=url, exports=[("openapi", OAS, "docs/openapi.yaml")]))
    if extra_repo:
        coord.write(f"{R}/user-service.yaml", registry_yaml("user-service", "src", repo=extra_repo, imports=["payment-service"]))
    coord.write(OAS, "openapi: 3.0.0\n")
    coord.write("_bmad-output/planning-artifacts/epics.md", "## Epic 1: P\n### Story 1.2: Impl\n**Subproject:** payment-service\n")
    coord.write(STATUS, "development_status:\n  epic-1: in-progress\n  1-2-impl: backlog\n")
    coord.commit()
    code_repo.write("src/app.py", "x\n").write("docs/openapi.yaml", "openapi: 3.0.0\n").commit()
    code_repo.branch("story/1-2").write("src/app.py", "y\n")
    assert run_cli("marker", "write", "--story", "1.2", "--repo", url, "--coord", str(coord.path))[0] == 0
    code_repo.commit()
    return coord, code_repo, url


def test_close_record_pass_waits_while_a_repo_is_unread(tmp_path):
    coord, code_repo, url = _poly(tmp_path, extra_repo=str(tmp_path / "missing"))
    code_repo.checkout("main").git("merge", "-q", "--no-ff", "story/1-2")
    flags = ("--user", "Ann <a@x>", "--repo", str(coord.path))
    code, res = run_cli("epic", "close", "--epic", "1", "--push", *flags)
    assert res["pass"] == "archive", res
    code_repo.git("merge", "-q", "--no-ff", "orch/close-epic-1")
    code, res = run_cli("epic", "close", "--epic", "1", "--push", *flags)
    assert code == 1 and res["pass"] == "blocked" and "published" not in res, res
    assert [p["code"] for p in res["problems"]] == ["repos-unread"]
    assert not coord.git("branch", "--list", "orch/close-epic-1-record")


def test_unread_story_branches_never_offer_take_over(tmp_path, monkeypatch):
    from orchlib import OrchError
    coord, _, _ = _poly(tmp_path)
    run_cli("claim", "create", "--story", "1-2", "--user", "Ann <a@x>", "--repo", str(coord.path), env={"GIT_COMMITTER_DATE": OLD})

    def unreadable(*a, **k):
        raise OrchError("no access")
    monkeypatch.setattr(status_mod, "fetch_branches", unreadable)
    _, res = run_cli("status", "--no-host", "--repo", str(coord.path))
    assert by_key(res)["1-2"]["activity_source"] == "claim-only"
    stale = next(a for a in res["anomalies"] if a["code"] == "stale-claim")
    assert stale["actions"] == [] and "could not be read" in stale["message"]


def test_story_branches_come_from_origin_not_this_clone(mono, tmp_path):
    bare = tmp_path / "origin.git"
    mono.git("init", "-q", "--bare", str(bare))
    mono.git("remote", "add", "origin", str(bare))
    mono.git("push", "-q", "origin", "main")
    mono.branch("story/1-1").write(OAS, "openapi: 3.0.0\npaths: {/x: {}}\n").commit("local only")
    mono.checkout("main")
    _, res = cli(mono, "status", "--no-host")
    assert by_key(res)["1-1"]["branch"] is None, res
    mono.git("push", "-q", "origin", "story/1-1")
    _, res = cli(mono, "status", "--no-host")
    assert by_key(res)["1-1"]["branch"] == "story/1-1"


def test_epic_filter_keeps_cross_epic_dependents_and_rejects_unknown_epics(mono):
    text = EPICS + "\n## Epic 2: More\n### Story 2.1: Users again\n**Subproject:** user-service\n**Depends on:** 1.1\n"
    mono.write("_bmad-output/planning-artifacts/epics.md", text).commit()
    _, res = cli(mono, "status", "--no-host", "--epic", "1")
    bottleneck = next(a for a in res["anomalies"] if a["code"] == "contract-bottleneck")
    assert "2-1" in bottleneck["waiting"] and {s["epic"] for s in res["stories"]} == {1}
    code, res = cli(mono, "status", "--no-host", "--epic", "9")
    assert code == 2 and "epic 9" in res["error"], res
    assert cli(mono, "report", "--epic", "9", "--no-host")[0] == 2


def test_migration_draft_is_a_ready_story_with_a_free_id(mono):
    _run_epic(mono, adopt=False)
    _, res = cli(mono, "status", "--no-host")
    draft = next(a for a in res["anomalies"] if a["code"] == "pin-drift")["migration_draft"]
    assert draft["id"] == "1.5" and draft["markdown"].startswith("### Story 1.5: payment-service ")
    assert "**Depends on:** 1.4" in draft["markdown"]
    lag = next(a for a in res["anomalies"] if a["code"] == "sprint-status-lag")
    assert lag["actions"][0]["args"] == ["sprint-status", "derive"]   # preview first; the skill adds --write


def test_unrecorded_contract_change_gets_no_migration_draft(mono):
    land(mono, "1-1", {OAS: "openapi: 3.0.0\npaths: {/pay: {}}\n"})
    land(mono, "1-2", {COPY: "openapi: 3.0.0\npaths: {/pay: {}}\n"})
    mono.write(OAS, "openapi: 3.0.0\npaths: {/pay: {}, /sneaky: {}}\n").commit("edit outside a story")
    _, res = cli(mono, "status", "--no-host")
    drift = [a for a in res["anomalies"] if a["code"] == "pin-drift"]
    assert drift and all(a["reason"] == "unrecorded-change" and "migration_draft" not in a for a in drift), drift


def test_close_push_refuses_a_pass_other_than_the_confirmed_one(mono):
    _run_epic(mono, adopt=True)
    code, res = cli(mono, "epic", "close", "--epic", "1", "--push", "--expect-pass", "record")
    assert code == 1 and res["code"] == "pass-changed" and res["pass"] == "archive" and "published" not in res
    assert not mono.git("branch", "--list", "orch/close-epic-1")


def test_plan_check_flags_an_ambiguous_narrow_target(mono):
    from conftest import R, registry_yaml
    ledger = f"{C}/ledger-service/openapi.yaml"
    mono.write(f"{R}/ledger-service.yaml", registry_yaml("ledger-service", "services/ledger", exports=[("openapi", ledger, None)]))
    mono.write(ledger, "openapi: 3.0.0\npaths: {}\n").write("services/ledger/app.py", "print('ledger')\n")
    mono.write(f"{R}/payment-service.yaml", registry_yaml("payment-service", "services/payment", imports=["ledger-service"],
                                                         exports=[("openapi", OAS, COPY)]))
    mono.write(f"{R}/user-service.yaml", registry_yaml("user-service", "services/user", imports=["payment-service", "ledger-service"]))
    mono.commit()
    code, res = cli(mono, "plan-check")
    assert code == 0 and res["verdict"] == "CONCERNS", res
    assert [f["code"] for f in res["findings"]] == ["ambiguous-narrow-target"]


def test_truncated_review_list_adds_a_notice(tmp_path, monkeypatch):
    rows = [{"headRefName": "story/1-2", "createdAt": OLD, "url": "u"}]
    monkeypatch.setenv("PATH", fake_bin(tmp_path, "gh", f"print({json.dumps(json.dumps(rows))})\n"))
    monkeypatch.setattr(status_mod, "GH_LIMIT", 1)
    reg = {"pay": SimpleNamespace(repo="git@github.com:acme/pay.git")}
    found, _, notices = status_mod.reviews(reg, tmp_path)
    assert found and [n["code"] for n in notices] == ["review-list-truncated"]


# --- regressions from the second analyze report ---

def _reversed_history(mono):
    mono.write("_bmad-output/planning-artifacts/epics.md", REVERSED).commit("plan")
    land(mono, "1-2", {OAS: "openapi: 3.0.0\npaths: {/a: {}}\n"})
    land(mono, "1-1", {OAS: "openapi: 3.0.0\npaths: {/a: {}, /b: {}}\n"})


def test_shallow_clone_cut_through_the_markers_refuses_to_guess_the_order(mono, tmp_path):
    _reversed_history(mono)
    for depth, cut in ((1, True), (3, False)):     # 3 reaches the plan commit, before any marker
        clone = tmp_path / f"clone{depth}"
        mono.git("clone", "-q", f"--depth={depth}", f"file://{mono.path}", str(clone))
        mono.git("-C", str(clone), "remote", "set-url", "origin", str(tmp_path / "gone.git"))
        mono.git("-C", str(clone), "update-ref", "refs/remotes/origin/main", "HEAD")
        code, res = run_cli("status", "--no-host", "--offline", "--repo", str(clone))
        if cut:
            assert code == 2 and "shallow clone" in res["error"], res
        else:
            assert code == 0 and "pin-drift" not in codes(res), res


def test_shallow_clone_deepens_itself_from_origin(mono, tmp_path):
    _reversed_history(mono)
    clone = tmp_path / "clone"
    mono.git("clone", "-q", "--depth=1", f"file://{mono.path}", str(clone))
    code, res = run_cli("status", "--no-host", "--repo", str(clone))
    assert code == 0 and "pin-drift" not in codes(res), res
    assert mono.git("-C", str(clone), "rev-parse", "--is-shallow-repository") == "false"


def _record_branch(mono, contract_stories: int):
    """Main with epic 1 archived, and orch/close-epic-1-record built on it; `contract_stories` 1 or 2 change the contract."""
    if contract_stories == 2:
        _run_epic(mono, adopt=True)
    else:
        mono.write("_bmad-output/planning-artifacts/epics.md", EPICS.split("### Story 1.4")[0]).commit("plan")
        v1 = "openapi: 3.0.0\npaths: {/pay: {}}\n"
        land(mono, "1-1", {OAS: v1})
        land(mono, "1-2", {COPY: v1, "services/payment/app.py": "v1\n"})
        land(mono, "1-3", {"services/user/app.py": "migrated\n"})
    cli(mono, "epic", "close", "--epic", "1", "--push", "--user", "Ann <a@x>")
    mono.git("merge", "-q", "--no-ff", "orch/close-epic-1")
    code, res = cli(mono, "epic", "close", "--epic", "1", "--push", "--user", "Ann <a@x>")
    assert res["pass"] == "record", res


def _gate_in_clone(mono, tmp_path, depth, origin_reachable):
    clone = tmp_path / f"ci{depth}{origin_reachable}"
    mono.git("clone", "-q", "--no-single-branch", f"--depth={depth}", f"file://{mono.path}", str(clone))
    mono.git("-C", str(clone), "checkout", "-q", "orch/close-epic-1-record")
    if not origin_reachable:
        mono.git("-C", str(clone), "remote", "set-url", "origin", str(tmp_path / "gone.git"))
    return run_cli("gate", "--repo", str(clone), "--base", "origin/main", env={"CI": "true"})


def test_record_pr_gate_needs_no_history_while_each_contract_changed_once(mono, tmp_path):
    _record_branch(mono, contract_stories=1)
    code, res = _gate_in_clone(mono, tmp_path, 2, origin_reachable=False)
    assert code == 0 and res["verdict"] == "pass", res


def test_record_pr_gate_deepens_a_ci_clone_or_fails_with_a_hint(mono, tmp_path):
    _record_branch(mono, contract_stories=2)      # expand then narrow: the merge order decides convergence
    code, res = _gate_in_clone(mono, tmp_path, 2, origin_reachable=True)
    assert code == 0 and res["verdict"] == "pass", res
    code, res = _gate_in_clone(mono, tmp_path, 2, origin_reachable=False)
    found = [f for f in check(res, "sprint-status")["findings"] if f["level"] == "fail"]
    assert code == 1 and [f["code"] for f in found] == ["epic-close-unverifiable"], res
    assert "fetch-depth: 0" in found[0]["hint"]


def test_fast_forward_merge_orders_a_story_by_its_last_marker_rewrite(mono):
    mono.write("_bmad-output/planning-artifacts/epics.md", REVERSED).commit("plan")
    mono.branch("story/1-1").write("docs/1-1.md", "draft\n").commit("early")
    assert cli(mono, "marker", "write", "--story", "1-1")[0] == 0
    mono.commit("early marker")
    mono.checkout("main")
    land(mono, "1-2", {OAS: "openapi: 3.0.0\npaths: {/a: {}}\n"})
    mono.checkout("story/1-1").git("merge", "-q", "--no-edit", "main")
    mono.write(OAS, "openapi: 3.0.0\npaths: {/a: {}, /b: {}}\n").commit("contract")
    assert cli(mono, "marker", "write", "--story", "1-1")[0] == 0
    mono.commit("marker rewrite")
    mono.checkout("main").git("merge", "-q", "--ff-only", "story/1-1")
    _, res = cli(mono, "status", "--no-host")
    assert "pin-drift" not in codes(res), res["anomalies"]


CROSS_EPIC = EPICS.split("### Story 1.4")[0] + """## Epic 2: Cleanup
### Story 2.1: Remove legacy field
**Subproject:** contracts
**Depends on:** 1.3
**Contract change:** narrow
"""


def test_migration_draft_lands_after_its_dependency_and_passes_plan_check(mono):
    mono.write("_bmad-output/planning-artifacts/epics.md", CROSS_EPIC).commit("plan")
    v1, v2 = "openapi: 3.0.0\npaths: {/pay: {}, /legacy: {}}\n", "openapi: 3.0.0\npaths: {/pay: {}}\n"
    land(mono, "1-1", {OAS: v1})
    land(mono, "1-2", {COPY: v1})
    land(mono, "1-3", {"services/user/app.py": "migrated\n"})
    land(mono, "2-1", {OAS: v2})
    _, res = cli(mono, "status", "--no-host")
    draft = next(a for a in res["anomalies"] if a["code"] == "pin-drift")["migration_draft"]
    assert (draft["id"], draft["epic"], draft["depends_on"]) == ("2.2", 2, ["2.1"]), draft
    mono.write("_bmad-output/planning-artifacts/epics.md", CROSS_EPIC + "\n" + draft["markdown"]).commit("paste")
    code, res = cli(mono, "plan-check")
    assert code == 0 and res["verdict"] == "PASS", res


def test_one_migration_draft_covers_every_drifting_contract_of_a_subproject(mono):
    from conftest import R, registry_yaml
    events, events_copy = f"{C}/payment-service/events.yaml", "services/payment/events.yaml"
    mono.write(f"{R}/payment-service.yaml", registry_yaml("payment-service", "services/payment",
                                                         exports=[("openapi", OAS, COPY), ("events", events, events_copy)]))
    mono.write(events, "asyncapi: 2.0.0\n").write(events_copy, "asyncapi: 2.0.0\n").commit("second contract")
    e1, e2 = "asyncapi: 2.0.0\nchannels: {a: {}, legacy: {}}\n", "asyncapi: 2.0.0\nchannels: {a: {}}\n"
    v1, v2 = "openapi: 3.0.0\npaths: {/pay: {}, /legacy: {}}\n", "openapi: 3.0.0\npaths: {/pay: {}}\n"
    land(mono, "1-1", {OAS: v1, events: e1})
    land(mono, "1-2", {COPY: v1, events_copy: e1})
    land(mono, "1-3", {"services/user/app.py": "migrated\n"})
    land(mono, "1-4", {OAS: v2, events: e2})
    _, res = cli(mono, "status", "--no-host")
    drafts = [a["migration_draft"] for a in res["anomalies"] if a["code"] == "pin-drift"]
    assert len(drafts) == 2 and drafts[0] == drafts[1], drafts
    assert drafts[0]["id"] == "1.5" and drafts[0]["contracts"] == sorted([OAS, events])


# --- regressions from the code review of orch-status ---

def test_a_second_archive_pass_moves_the_branch_left_by_the_first(mono):
    _run_epic(mono, adopt=False)
    cli(mono, "epic", "close", "--epic", "1", "--push", "--user", "Ann <a@x>")
    mono.git("merge", "-q", "--no-ff", "orch/close-epic-1")
    mono.write("_bmad-output/planning-artifacts/epics.md", EPICS_ADOPT).commit("plan 1.5")   # the migration story
    land(mono, "1-5", {COPY: "openapi: 3.0.0\npaths: {/pay: {}}\n"})
    code, res = cli(mono, "epic", "close", "--epic", "1", "--push", "--user", "Ann <a@x>", "--expect-pass", "archive")
    assert code == 0 and res["published"][0]["status"] == "updated", res
    assert ".orch/archive/epic-1/1-5.yaml" in mono.git("ls-tree", "-r", "--name-only", "orch/close-epic-1")
    code, res = cli(mono, "epic", "close", "--epic", "1", "--push", "--user", "Ann <a@x>", "--expect-pass", "archive")
    assert res["published"][0]["status"] == "exists", res       # rerun before merging: the PR stays
    mono.git("merge", "-q", "--no-ff", "orch/close-epic-1")
    code, res = cli(mono, "epic", "close", "--epic", "1")
    assert code == 0 and res["pass"] == "record", res


REVERT = """## Epic 1: Payments

### Story 1.1: Contract A
**Subproject:** contracts
**Depends on:** none
**Contract change:** expand

### Story 1.2: Payment on A
**Subproject:** payment-service
**Depends on:** 1.1

### Story 1.3: Contract B adds extra
**Subproject:** contracts
**Depends on:** 1.2
**Contract change:** expand

### Story 1.4: Payment implements extra
**Subproject:** payment-service
**Depends on:** 1.3

### Story 1.5: User service
**Subproject:** user-service
**Depends on:** 1.3

### Story 1.6: Drop extra again
**Subproject:** contracts
**Depends on:** 1.5
**Contract change:** narrow
"""


def test_a_contract_reverted_to_an_older_version_still_shows_the_exporter_drift(mono):
    mono.write("_bmad-output/planning-artifacts/epics.md", REVERT).commit("plan")
    a, b = "openapi: 3.0.0\npaths: {/pay: {}}\n", "openapi: 3.0.0\npaths: {/pay: {}, /extra: {}}\n"
    land(mono, "1-1", {OAS: a})
    land(mono, "1-2", {COPY: a})
    land(mono, "1-3", {OAS: b})
    land(mono, "1-4", {COPY: b})
    land(mono, "1-5", {"services/user/app.py": "x\n"})
    land(mono, "1-6", {OAS: a})                                  # back to A without migrating payment-service
    _, res = cli(mono, "status", "--no-host")
    drift = [(a["story"], a["reason"]) for a in res["anomalies"] if a["code"] == "pin-drift"]
    assert drift == [("1-4", "not-migrated")] and res["epics"][0]["state"] == "drift", res["anomalies"]


def test_status_reads_claims_as_last_fetched_when_origin_is_offline_or_gone(mono, tmp_path):
    cli(mono, "claim", "create", "--story", "1-1", "--user", "Ann <a@x>", "--local")
    mono.git("remote", "add", "origin", str(tmp_path / "gone.git"))
    mono.git("update-ref", "refs/remotes/origin/main", "main")
    mono.git("update-ref", "refs/orch/claims/1-1", "refs/heads/claim/1-1")      # the mirror of the last fetch
    for flags, why in ((["--offline"], "claims-offline"), ([], "claims-unreadable")):
        code, res = cli(mono, "status", "--no-host", *flags)
        assert code == 0 and why in {u["code"] for u in res["unread_repos"]}, res
        assert by_key(res)["1-1"]["claimant"] == "Ann <a@x>"


def test_plan_check_fails_a_registry_the_gate_rejects(mono):
    mono.write(f"{R}/user-service.yaml", registry_yaml("user-service", "services/user", imports=["payment-service", "billing"]))
    mono.commit("typo")
    code, res = cli(mono, "plan-check")
    assert code == 1 and res["verdict"] == "FAIL", res
    assert [f["code"] for f in res["findings"]] == ["unknown-import"]


def test_plan_check_passes_a_narrow_of_a_contract_nobody_imports(mono):
    mono.write(f"{R}/user-service.yaml", registry_yaml("user-service", "services/user")).commit("no imports")
    code, res = cli(mono, "plan-check")
    assert code == 0 and res["verdict"] == "PASS", res


def test_a_contract_story_nobody_waits_on_is_no_bottleneck(mono):
    _, res = cli(mono, "status", "--no-host")
    assert by_key(res)["1-4"]["critical"] and by_key(res)["1-4"]["downstream"] == 0
    assert [a["story"] for a in res["anomalies"] if a["code"] == "contract-bottleneck"] == ["1-1"]


def test_migration_draft_skips_a_closed_epic(mono):
    mono.write("_bmad-output/planning-artifacts/epics.md", CROSS_EPIC).commit("plan")
    mono.write(STATUS, "development_status:\n  epic-1: in-progress\n  1-1-payment-api-contract: backlog\n"
                       "  1-2-implement-payment-api: backlog\n  1-3-user-service-calls-payments: backlog\n"
                       "  epic-2: in-progress\n  2-1-remove-legacy-field: backlog\n").commit("sprint status")
    v1, v2 = "openapi: 3.0.0\npaths: {/pay: {}, /legacy: {}}\n", "openapi: 3.0.0\npaths: {/pay: {}}\n"
    land(mono, "1-1", {OAS: v1})
    land(mono, "1-2", {COPY: v1})
    land(mono, "1-3", {"services/user/app.py": "migrated\n"})
    land(mono, "2-1", {OAS: v2})
    for _ in ("archive", "record"):
        code, res = cli(mono, "epic", "close", "--epic", "2", "--push", "--user", "Ann <a@x>")
        assert code == 0, res
        mono.checkout("main").git("merge", "-q", "--no-ff", res["published"][0]["branch"])
    _, res = cli(mono, "status", "--no-host")
    draft = next(a for a in res["anomalies"] if a["code"] == "pin-drift")["migration_draft"]
    assert (draft["id"], draft["epic"], draft["new_epic"]) == ("3.1", 3, True), draft
    mono.write("_bmad-output/planning-artifacts/epics.md", CROSS_EPIC + "\n" + draft["markdown"]).commit("paste")
    code, res = cli(mono, "plan-check")
    assert code == 0 and res["verdict"] == "PASS", res


def test_story_in_review_without_a_review_host_is_stuck_not_stale_even_after_a_rename(mono):
    mono.write(STATUS, "development_status:\n  epic-1: in-progress\n  1-1-old-title: review\n").commit("sprint status")
    cli(mono, "claim", "create", "--story", "1-1", "--user", "Ann <a@x>", env={"GIT_COMMITTER_DATE": OLD})
    _, res = cli(mono, "status", "--no-host")
    assert codes(res, "1-1") == {"stuck-review", "contract-bottleneck"}, res["anomalies"]


def test_downstream_counts_every_story_of_a_dependency_cycle():
    from orchlib.stories import Story, StorySet
    stories = StorySet()
    for sid, dep in (("1.1", "1.2"), ("1.2", "1.1")):
        stories[sid.replace(".", "-")] = Story(sid, sid.replace(".", "-"), 1, sid, f"{sid}-t", "x", [dep])
    assert status_mod._downstream(stories) == {"1-1": {"1-2"}, "1-2": {"1-1"}}


def test_a_file_url_has_no_review_host():
    assert status_mod._host_tool("file:///srv/git/coord.git") is None
    assert status_mod._host_tool("git@github.com:acme/pay.git") == "gh"


def test_report_to_a_pdf_path_keeps_the_html_beside_it(mono, tmp_path):
    path = fake_bin(tmp_path, "fakechrome", "import sys\n"
                    "arg = next(a for a in sys.argv if a.startswith('--print-to-pdf='))\n"
                    "open(arg.split('=', 1)[1], 'wb').write(b'%PDF-1.4')\n")
    code, res = cli(mono, "report", "--epic", "1", "--no-host", "-o", str(tmp_path / "r.pdf"),
                    env={"PATH": path, "ORCH_CHROME": "fakechrome"})
    assert code == 0 and res["html"] == str(tmp_path / "r.html") and res["pdf"] == str(tmp_path / "r.pdf"), res
    assert (tmp_path / "r.pdf").read_bytes() == b"%PDF-1.4" and "<svg" in (tmp_path / "r.html").read_text()
