"""status, plan-check, epic close and report: the commands orch-status (and orch-next's warnings) run on."""

import json
import os
from types import SimpleNamespace

import yaml
from conftest import C, EPICS, fake_bin, run_cli

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
