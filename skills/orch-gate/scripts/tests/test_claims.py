import subprocess

from conftest import Repo, run_cli


def test_local_claim_is_atomic_and_take_over_needs_lease(mono):
    p = str(mono.path)
    code, res = run_cli("claim", "create", "--story", "1.2", "--user", "Ann <a@x>", "--repo", p, "--local")
    assert code == 0 and res["ok"]
    first = res["sha"]
    code, res = run_cli("claim", "create", "--story", "1-2", "--user", "Bob <b@x>", "--repo", p, "--local")
    assert code == 1 and res["reason"] == "already-claimed" and res["claim"]["user"] == "Ann <a@x>"
    code, res = run_cli("claim", "take-over", "--story", "1-2", "--user", "Bob <b@x>", "--expect", "0" * 40, "--repo", p, "--local")
    assert code == 1 and res["reason"] == "claim-changed"
    code, res = run_cli("claim", "take-over", "--story", "1-2", "--user", "Bob <b@x>", "--expect", first, "--repo", p, "--local")
    assert code == 0 and res["previous"] == first
    code, res = run_cli("claim", "list", "--repo", p, "--local")
    assert [(c["story"], c["user"], c["action"]) for c in res["claims"]] == [("1-2", "Bob <b@x>", "take-over")]


def test_remote_claim_race(tmp_path, mono):
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "clone", "-q", "--bare", str(mono.path), str(remote)], check=True)
    a, b = Repo(tmp_path / "a"), Repo(tmp_path / "b")
    for r in (a, b):
        r.git("remote", "add", "origin", str(remote))
        r.git("fetch", "-q", "origin")
        r.git("checkout", "-q", "-b", "main", "origin/main")
    code, res = run_cli("claim", "create", "--story", "1-3", "--user", "A", "--repo", str(a.path))
    assert code == 0 and res["remote"] == "origin"
    code, res = run_cli("claim", "create", "--story", "1-3", "--user", "B", "--repo", str(b.path))
    assert code == 1 and res["claim"]["user"] == "A"
    code, res = run_cli("claim", "release", "--story", "1-3", "--expect", res["claim"]["sha"], "--repo", str(b.path))
    assert code == 0 and res["released"]
    code, res = run_cli("claim", "list", "--repo", str(a.path))
    assert res["claims"] == []
