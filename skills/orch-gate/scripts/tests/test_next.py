"""next, worktree and context: the commands orch-next runs on."""

import json
import subprocess
from types import SimpleNamespace

from conftest import C, EPICS, R, Repo, registry_yaml, run_cli

from orchlib import work

OAS = f"{C}/payment-service/openapi.yaml"
ANN, BOB = "Ann <a@x>", "Bob <b@x>"


def cli(r, *argv, env=None):
    return run_cli(*argv, "--repo", str(r.path), env=env)


def land(r, key, files):
    r.branch(f"story/{key}")
    for path, text in files.items():
        r.write(path, text)
    r.commit(f"story {key}")
    assert cli(r, "marker", "write", "--story", key)[0] == 0
    r.commit(f"marker {key}")
    r.checkout("main").git("merge", "-q", "--no-ff", f"story/{key}")
    r.git("branch", "-D", f"story/{key}")


def test_ready_list_is_ranked_by_critical_path_then_downstream(mono):
    code, res = cli(mono, "next", "--no-host", "--user", ANN)
    assert code == 0, res
    assert [e["key"] for e in res["ready"]] == ["1-1"]
    first = res["ready"][0]
    assert first["critical"] and first["downstream"] == 3 and first["unblocks"] == ["1-2", "1-3"]
    assert first["repo"] == "." and first["rank"] == 1 and not res["mine"]

    land(mono, "1-1", {OAS: "openapi: 3.0.0\npaths: {/pay: {}}\n"})
    _, res = cli(mono, "next", "--no-host", "--user", ANN)
    # 1.3 leads to the narrow story 1.4, so it is on the critical path; 1.2 unblocks nothing
    assert [(e["key"], e["rank"], e["unblocks"]) for e in res["ready"]] == [("1-3", 1, ["1-4"]), ("1-2", 2, [])]


def test_own_open_claims_are_listed_and_leave_the_ready_list(mono):
    assert cli(mono, "claim", "create", "--story", "1-1", "--user", ANN)[0] == 0
    _, res = cli(mono, "next", "--no-host", "--user", "Ann Smith <A@X>")   # same email, other spelling
    assert [m["key"] for m in res["mine"]] == ["1-1"] and res["mine"][0]["claim_sha"]
    assert not res["ready"] and res["counts"]["in-progress"] == 1
    _, res = cli(mono, "next", "--no-host", "--user", BOB)
    assert not res["mine"]


def test_a_ready_story_with_plan_issues_is_held_back(mono):
    mono.write("_bmad-output/planning-artifacts/epics.md",
               EPICS + "\n### Story 1.5: Ghost work\n**Subproject:** ghost\n**Depends on:** none\n").commit("plan")
    _, res = cli(mono, "next", "--no-host", "--user", ANN)
    assert [e["key"] for e in res["ready"]] == ["1-1"]
    assert [h["key"] for h in res["held"]] == ["1-5"]
    assert res["held"][0]["plan_issues"][0]["code"] == "unknown-subproject"


def test_nothing_ready_lists_what_the_work_waits_for(mono):
    _, res = cli(mono, "next", "--no-host", "--user", ANN)
    assert "waiting" not in res                                  # 1.1 is ready
    cli(mono, "claim", "create", "--story", "1-1", "--user", BOB)
    _, res = cli(mono, "next", "--no-host", "--user", ANN)
    assert not res["ready"]
    assert [(w["key"], w["state"]) for w in res["waiting"]][:2] == [("1-1", "in-progress"), ("1-2", "blocked")]
    assert res["waiting"][0]["claimant"] == BOB and res["waiting"][1]["blocked_by"] == ["1-1"]
    _, res = cli(mono, "next", "--no-host", "--user", BOB)
    assert [m["key"] for m in res["mine"]] == ["1-1"] and "1-1" not in [w["key"] for w in res["waiting"]]


def test_claims_refuse_offline(mono):
    code, res = cli(mono, "claim", "create", "--story", "1-1", "--user", ANN, "--offline")
    assert code == 2 and res["code"] == "offline-claim"
    assert cli(mono, "claim", "list", "--offline")[1]["claims"] == []


def test_stale_claims_come_first_among_warnings(mono):
    cli(mono, "claim", "create", "--story", "1-1", "--user", ANN, env={"GIT_COMMITTER_DATE": "2020-01-01T00:00:00Z"})
    _, res = cli(mono, "next", "--no-host", "--user", BOB)
    assert res["warnings"][0]["code"] == "stale-claim"
    assert any(a["label"] == "take over" for a in res["warnings"][0]["actions"])


def test_worktree_needs_the_callers_claim(mono, tmp_path):
    code, res = cli(mono, "worktree", "--story", "1-1", "--user", ANN, "--path", str(tmp_path / "wt"))
    assert code == 1 and res["reason"] == "not-claimed"
    cli(mono, "claim", "create", "--story", "1-1", "--user", ANN)
    code, res = cli(mono, "worktree", "--story", "1-1", "--user", BOB, "--path", str(tmp_path / "wt"))
    assert code == 1 and res["reason"] == "claimed-by-other" and res["claim"]["user"] == ANN
    assert not (tmp_path / "wt").exists()


def test_worktree_for_a_contract_story_in_a_monorepo(mono, tmp_path):
    cli(mono, "claim", "create", "--story", "1-1", "--user", ANN)
    code, res = cli(mono, "worktree", "--story", "1.1", "--user", ANN)
    assert code == 0, res
    wt = res["worktree"]
    assert wt["status"] == "created" and wt["branch"] == "story/1-1" and wt["start"] == "main"
    assert wt["path"] == str((tmp_path / "mono-worktrees" / "story-1-1").resolve())   # ../{project_name}-worktrees
    tree = Repo.__new__(Repo)
    tree.path = tmp_path / "mono-worktrees" / "story-1-1"
    assert tree.git("rev-parse", "--abbrev-ref", "HEAD") == "story/1-1"
    assert tree.git("status", "--porcelain") == ""          # the context is excluded, never committed
    ctx = json.loads((tree.path / work.CONTEXT_JSON).read_text())
    assert ctx["story"]["contract_change"] == "expand" and ctx["pins"] is None and not ctx["snapshots"]
    assert [(c["path"], c["consumers"]) for c in ctx["contracts"]] == [(OAS, ["user-service"])]
    md = (tree.path / work.CONTEXT_MD).read_text()
    assert "As a dev, I want a contract." in md and "Story 1.2" not in md and "Expand only" in md

    code, again = cli(mono, "worktree", "--story", "1-1", "--user", ANN)
    assert code == 0 and again["worktree"]["status"] == "exists" and again["worktree"]["path"] == wt["path"]


def test_context_in_a_worktree_reads_the_story_from_the_branch(mono, tmp_path):
    land(mono, "1-1", {OAS: "openapi: 3.0.0\npaths: {/pay: {}}\n"})
    cli(mono, "claim", "create", "--story", "1-2", "--user", ANN)
    assert cli(mono, "worktree", "--story", "1-2", "--user", ANN, "--path", str(tmp_path / "wt"))[0] == 0
    code, res = run_cli("context", "--repo", str(tmp_path / "wt"))
    assert code == 0, res
    ctx = res["context"]
    assert ctx["story"]["key"] == "1-2" and ctx["subproject"]["allowed_write"] == ["services/payment/**"]
    assert ctx["pins"]["contract_pins"] == {OAS: mono.blob(OAS)}
    assert [(c["role"], c["copy"]) for c in ctx["contracts"]] == [("export", "services/payment/openapi.yaml")]
    code, res = run_cli("context", "--repo", str(mono.path))
    assert code == 2 and res["code"] == "not-story-branch" and "--story" in res["error"]


def test_take_over_continues_from_the_pushed_story_branch(mono, tmp_path):
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "clone", "-q", "--bare", str(mono.path), str(remote)], check=True)
    mono.git("remote", "add", "origin", str(remote))
    mono.git("fetch", "-q", "origin")
    mono.git("remote", "set-head", "origin", "main")
    assert cli(mono, "claim", "create", "--story", "1-1", "--user", ANN)[0] == 0
    _, res = cli(mono, "worktree", "--story", "1-1", "--user", ANN, "--path", str(tmp_path / "ann"))
    ann = Repo.__new__(Repo)
    ann.path = tmp_path / "ann"
    ann.write(OAS, "openapi: 3.0.0\npaths: {/wip: {}}\n").commit("wip")
    ann.git("push", "-q", "origin", "story/1-1")

    bob = Repo.__new__(Repo)
    bob.path = tmp_path / "bob"
    subprocess.run(["git", "clone", "-q", str(remote), str(bob.path)], check=True)
    _, claims = cli(bob, "claim", "list")
    sha = claims["claims"][0]["sha"]
    assert cli(bob, "claim", "take-over", "--story", "1-1", "--user", BOB, "--expect", sha)[0] == 0
    code, res = cli(bob, "worktree", "--story", "1-1", "--user", BOB, "--path", str(tmp_path / "bob-wt"))
    assert code == 0 and res["worktree"]["status"] == "from-remote", res
    assert (tmp_path / "bob-wt" / OAS).read_text() == "openapi: 3.0.0\npaths: {/wip: {}}\n"


def test_polyrepo_worktree_goes_to_the_code_repo_with_contract_snapshots(tmp_path):
    coord, code_repo = Repo(tmp_path / "coord"), Repo(tmp_path / "payment")
    url = str(code_repo.path)
    coord.write(f"{R}/payment-service.yaml", registry_yaml(
        "payment-service", "src", repo=url, exports=[("openapi", OAS, "docs/openapi.yaml")]))
    coord.write(OAS, "openapi: 3.0.0\n")
    coord.write("_bmad-output/planning-artifacts/epics.md", "## Epic 1: P\n### Story 1.2: Impl\n**Subproject:** payment-service\n")
    coord.commit()
    code_repo.write("src/app.py", "x\n").write("docs/openapi.yaml", "openapi: 3.0.0\n").commit()
    run_cli("claim", "create", "--story", "1-2", "--user", ANN, "--repo", str(coord.path))

    code, res = run_cli("worktree", "--story", "1-2", "--user", ANN, "--repo", str(coord.path))
    assert code == 2 and res["code"] == "not-a-clone" and res["repo"] == url and "--repo" in res["error"]

    code, res = run_cli("worktree", "--story", "1-2", "--user", ANN, "--repo", url, "--coord", str(coord.path),
                        "--path", str(tmp_path / "wt"))
    assert code == 0 and res["clone"] == url, res
    snap = tmp_path / "wt" / work.CONTEXT_DIR / "contracts" / OAS
    assert snap.read_text() == "openapi: 3.0.0\n"
    assert f"snapshot `{work.CONTEXT_DIR}/contracts/{OAS}`" in (tmp_path / "wt" / work.CONTEXT_MD).read_text()
    assert code_repo.git("-C", str(tmp_path / "wt"), "status", "--porcelain") == ""


def test_story_text_keeps_subheadings_and_stops_at_the_next_story():
    text = ("## Epic 1: P\n### Story 1.1: A\nbody\n#### Acceptance\n- ok\n```\n# not a heading\n```\n"
            "### Story 1.2: B\nother\n")
    tree = SimpleNamespace(text=lambda path: text)
    story = SimpleNamespace(source="epics.md", line=2)
    assert work.story_text(tree, story) == "### Story 1.1: A\nbody\n#### Acceptance\n- ok\n```\n# not a heading\n```\n"


def test_same_user_compares_emails():
    assert work.same_user("Ann <A@x>", "Ann Smith <a@X>")
    assert not work.same_user("Ann <a@x>", "Ann <b@x>")
    assert work.same_user("ann", "ann") and not work.same_user(None, "ann")


def test_claims_with_local_work_offline(mono):
    code, res = cli(mono, "claim", "create", "--story", "1-1", "--user", ANN, "--offline", "--local")
    assert code == 0, res
    assert [c["story"] for c in cli(mono, "claim", "list", "--offline", "--local")[1]["claims"]] == ["1-1"]


def test_absolute_worktrees_dir_is_kept(tmp_path):
    from orchlib.config import _rel
    assert _rel("/home/me/wt", "shop", absolute=True) == "/home/me/wt"
    assert _rel("~/wt/{project_name}/", "shop", absolute=True) == "~/wt/shop"
    assert _rel("{project-root}/wt", "shop", absolute=True) == "wt"
    assert _rel("/home/me/wt", "shop") == "home/me/wt"
    assert work.default_path(tmp_path, str(tmp_path / "wt"), "1-1") == (tmp_path / "wt").resolve() / "story-1-1"


def test_worktree_made_by_hand_still_excludes_the_context(mono, tmp_path):
    cli(mono, "claim", "create", "--story", "1-1", "--user", ANN)
    mono.git("worktree", "add", "-q", "-b", "story/1-1", str(tmp_path / "hand"), "main")
    code, res = cli(mono, "worktree", "--story", "1-1", "--user", ANN)
    assert code == 0 and res["worktree"]["status"] == "exists", res
    assert mono.git("-C", str(tmp_path / "hand"), "status", "--porcelain") == ""


def test_own_claim_in_an_unread_repo_stays_in_mine(tmp_path):
    coord, code_repo = Repo(tmp_path / "coord"), Repo(tmp_path / "payment")
    coord.write(f"{R}/payment-service.yaml", registry_yaml("payment-service", "src", repo=str(code_repo.path)))
    coord.write("_bmad-output/planning-artifacts/epics.md", "## Epic 1: P\n### Story 1.2: Impl\n**Subproject:** payment-service\n")
    coord.commit()
    code_repo.write("src/app.py", "x\n").commit()
    run_cli("claim", "create", "--story", "1-2", "--user", ANN, "--repo", str(coord.path))
    code, res = run_cli("next", "--no-host", "--offline", "--user", ANN, "--repo", str(coord.path))
    assert code == 0, res
    assert [(m["key"], m["state"]) for m in res["mine"]] == [("1-2", "unknown")] and res["mine"][0]["claim_sha"]


def test_missing_canonical_gets_no_snapshot(tmp_path):
    root = tmp_path / "wt"
    root.mkdir()
    ctx = {"story": {"id": "1.2", "key": "1-2", "subproject": "payment-service", "depends_on": [],
                     "contract_change": "none", "source": "e.md:1", "text": "x\n"},
           "subproject": {"name": "payment-service", "repo": ".", "path": "src", "allowed_read": [], "allowed_write": []},
           "branch": "story/1-2", "base_branch": "main", "marker": "m.yaml", "coord_ref": "main", "coord_sha": "0" * 40,
           "plan_issues": [], "contracts": [{"path": OAS, "type": "openapi", "owner": "payment-service",
                                             "role": "import", "copy": None, "sha": None, "consumers": []}]}
    coord = SimpleNamespace(files=lambda path: {}, kind=lambda path: None)
    res = work.write(root, ctx, coord, snapshot=True)
    assert res["snapshots"] == {} and "snapshot `" not in (root / work.CONTEXT_MD).read_text()
