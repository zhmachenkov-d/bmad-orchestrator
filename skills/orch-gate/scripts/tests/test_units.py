from conftest import EPICS, SPRINT

from orchlib import globs, sprint_status, stories
from orchlib.gitio import normalize_repo


def test_globs():
    assert globs.matches("services/a/x.py", ["services/a/**"])
    assert not globs.matches("services/ab/x.py", ["services/a/**"])
    assert globs.matches("a/b/c.yaml", ["**/*.yaml"])
    assert globs.matches("c.yaml", ["**/*.yaml"])
    assert not globs.matches("a/c.yml", ["a/*.yaml"])
    assert globs.may_overlap("services/**", "services/a/**")
    assert not globs.may_overlap("services/a/**", "services/b/**")


def test_normalize_repo():
    assert normalize_repo("git@github.com:Org/Pay.git") == "github.com/org/pay"
    assert normalize_repo("https://github.com/org/pay") == "github.com/org/pay"
    assert normalize_repo("ssh://git@github.com/org/pay.git/") == "github.com/org/pay"
    assert normalize_repo(".") == "."


def test_parse_stories_labels_and_keys():
    parsed, issues = stories.parse(EPICS, "epics.md")
    assert issues == []
    by = {s.id: s for s in parsed}
    assert by["1.2"].subproject == "payment-service"
    assert by["1.2"].depends_on == ["1.1"]
    assert by["1.2"].key == "1-2"
    assert by["1.2"].sprint_key == "1-2-implement-payment-api"
    assert by["1.4"].contract_change == "narrow"
    assert by["1.1"].depends_on == []


def test_story_validation_catches_plan_errors():
    text = """## Epic 1: X
### Story 1.1: A
**Subproject:** api
**Depends on:** 1.2
### Story 1.2: B
**Contract change:** narrow
**Subproject:** api
```
### Story 1.9: inside a fence is ignored
```
"""
    s = stories.StorySet()
    parsed, issues = stories.parse(text, "e.md")
    for st in parsed:
        s[st.key] = st
    codes = {i["code"] for i in issues + stories.validate(s, None)}
    assert "forward-dependency" in codes
    assert "contract-change-outside-contracts" in codes
    assert "1-9" not in s


def test_key_from_any():
    assert stories.key_from_any("1.2") == "1-2"
    assert stories.key_from_any("2-6a") == "2-6a"
    assert stories.key_from_any("1-2-implement-payment-api") == "1-2"
    assert stories.key_from_any("x") is None


def test_sprint_check_and_derive():
    text = SPRINT.replace("1-2-implement-payment-api: backlog", "1-2-implement-payment-api: done").replace(
        "epic-1: in-progress", "epic-1: done")
    res = sprint_status.check(text, merged={"1-1"}, closed=set())
    codes = {f["code"] for f in res["fail"]}
    assert codes == {"done-without-marker", "epic-done-without-close"}
    assert {w["code"] for w in res["warn"]} == {"merged-not-done"}
    new, changes = sprint_status.derive(text, merged={"1-1"}, closed=set())
    assert "1-1-payment-api-contract: done" in new
    assert "1-2-implement-payment-api: review" in new
    assert "epic-1: in-progress" in new
    assert new.startswith("# generated") and "action_items: []" in new
    assert len(changes) == 3


def test_sprint_conflict_markers_fail():
    res = sprint_status.check("<<<<<<< ours\n" + SPRINT, set(), set())
    assert res["fail"][0]["code"] == "conflict-markers"


def test_merge_driver_takes_max_rank_and_new_keys():
    ours = SPRINT.replace("1-2-implement-payment-api: backlog", "1-2-implement-payment-api: in-progress").replace(
        "last_updated: 09-25-2026 10:00", "last_updated: 09-25-2026 11:00")
    theirs = SPRINT.replace("1-2-implement-payment-api: backlog", "1-2-implement-payment-api: review").replace(
        "1-3-user-service-calls-payments: backlog", "1-3-user-service-calls-payments: in-progress\n  1-3a-split: backlog").replace(
        "last_updated: 09-25-2026 10:00", "last_updated: 12-01-2025 12:00")
    merged = sprint_status.merge(SPRINT, ours, theirs)
    assert "1-2-implement-payment-api: review" in merged
    assert "1-3-user-service-calls-payments: in-progress\n  1-3a-split: backlog\n" in merged
    assert "last_updated: 09-25-2026 11:00" in merged
    assert sprint_status.merge(SPRINT, ours + "x: 1\n", theirs + "y: 2\n") is None
