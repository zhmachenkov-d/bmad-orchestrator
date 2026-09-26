"""Plan validation before implementation starts: PASS / CONCERNS / FAIL over the epics and the registry.

FAIL: story-structure issues (the same ones the gate enforces per PR), and a `narrow` contract story that does not
depend on a story of every registry consumer of the contract it narrows. CONCERNS: a dependency between two
subprojects that the registry `imports` do not connect, and a `narrow` with no earlier `expand` story.
"""

from __future__ import annotations

from .registry import CONTRACTS, Registry
from .stories import StorySet


def check(stories: StorySet, reg: Registry) -> dict:
    findings = [{"severity": "fail", **i} for i in stories.issues]
    order = list(stories)
    for s in stories.values():
        deps = [d for dep in s.depends_on if (d := stories.by_id(dep))]
        if s.contract_change == "narrow":
            findings += _narrow(s, deps, reg)
            if not any(p.contract_change == "expand" for p in stories.values()
                       if p.subproject == CONTRACTS and order.index(p.key) < order.index(s.key)):
                findings.append({"severity": "concern", "code": "narrow-without-expand", "story": s.id,
                                 "message": f"story {s.id} narrows a contract but no earlier contract story expands one; "
                                            "breaking changes go expand -> migrate -> contract"})
        if not s.subproject or s.subproject == CONTRACTS or s.subproject not in reg:
            continue
        for d in deps:
            if d.subproject in (s.subproject, CONTRACTS, None) or d.subproject not in reg:
                continue
            if d.subproject not in reg[s.subproject].imports:
                findings.append({"severity": "concern", "code": "dependency-not-imported", "story": s.id,
                                 "message": f"story {s.id} ({s.subproject}) depends on {d.id} ({d.subproject}), "
                                            f"but {s.subproject} does not import {d.subproject} in the registry"})
    verdict = "FAIL" if any(f["severity"] == "fail" for f in findings) else (
        "CONCERNS" if findings else "PASS")
    return {"verdict": verdict, "findings": findings}


def _narrow(s, deps, reg: Registry) -> list[dict]:
    """A narrow story passes when, for some exporter its dependencies point at, it depends on every consumer."""
    dep_subs = {d.subproject for d in deps}
    candidates = {e.name: reg.consumers(e.name) for e in reg.values() if e.exports and e.name != CONTRACTS}
    related = {name: cons for name, cons in candidates.items() if set(cons) & dep_subs}
    if any(set(cons) <= dep_subs for cons in related.values()):
        return []
    if not related:
        missing = "; ".join(f"{n}: {', '.join(c)}" for n, c in sorted(candidates.items()) if c) or "none"
        return [{"severity": "fail", "code": "narrow-without-migration", "story": s.id,
                 "message": f"story {s.id} narrows a contract but depends on no consumer's migration story "
                            f"(consumers per exporter: {missing})"}]
    gaps = "; ".join(f"{n}: {', '.join(sorted(set(c) - dep_subs))}" for n, c in sorted(related.items()))
    return [{"severity": "fail", "code": "narrow-missing-consumers", "story": s.id,
             "message": f"story {s.id} narrows a contract without depending on a story of every consumer ({gaps})"}]
