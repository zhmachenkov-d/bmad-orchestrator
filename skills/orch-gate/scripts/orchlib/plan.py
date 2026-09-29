"""Plan validation before implementation starts: PASS / CONCERNS / FAIL over the epics and the registry.

FAIL: registry and story-structure issues (the same ones the gate enforces per PR), and a `narrow` contract story that
does not depend on a story of every registry consumer of the contract it narrows. CONCERNS: registry paths not found, a
dependency between two subprojects that the registry `imports` do not connect, a `narrow` with no earlier `expand`
story, and a `narrow` whose target exporter the plan leaves ambiguous.
"""

from __future__ import annotations

from .registry import CONTRACTS, EXISTENCE_ISSUES, Registry
from .stories import StorySet


def check(stories: StorySet, reg: Registry, registry_issues: list[dict] = ()) -> dict:
    """`registry_issues` (registry.validate) weigh as the gate weighs them: missing paths concern, the rest fail."""
    findings = [{"severity": "concern" if i["code"] in EXISTENCE_ISSUES else "fail", "story": None, **i,
                 "refs": _refs(subprojects=([i["subproject"]] if i.get("subproject") else [])
                               + list((i.get("refs") or {}).get("subprojects", ())))}
                for i in registry_issues]
    findings += [{"severity": "fail", **i} for i in stories.issues]
    order = list(stories)
    for s in stories.values():
        deps = [d for dep in s.depends_on if (d := stories.by_id(dep))]
        if s.contract_change == "narrow":
            findings += _narrow(s, deps, reg)
            if not any(p.contract_change == "expand" for p in stories.values()
                       if p.subproject == CONTRACTS and order.index(p.key) < order.index(s.key)):
                findings.append({"severity": "concern", "code": "narrow-without-expand", "story": s.id, "refs": _refs(),
                                 "message": f"story {s.id} narrows a contract but no earlier contract story expands one; "
                                            "breaking changes go expand -> migrate -> contract"})
        if not s.subproject or s.subproject == CONTRACTS or s.subproject not in reg:
            continue
        for d in deps:
            if d.subproject in (s.subproject, CONTRACTS, None) or d.subproject not in reg:
                continue
            if d.subproject not in reg[s.subproject].imports:
                findings.append({"severity": "concern", "code": "dependency-not-imported", "story": s.id,
                                 "refs": _refs([d.id], [s.subproject, d.subproject]),
                                 "message": f"story {s.id} ({s.subproject}) depends on {d.id} ({d.subproject}), "
                                            f"but {s.subproject} does not import {d.subproject} in the registry"})
    verdict = "FAIL" if any(f["severity"] == "fail" for f in findings) else (
        "CONCERNS" if findings else "PASS")
    return {"verdict": verdict, "findings": findings}


def _refs(stories=(), subprojects=()) -> dict:
    """Story ids and subproject names a message mentions besides its `story`, in message order, each once."""
    return {"stories": list(dict.fromkeys(stories)), "subprojects": list(dict.fromkeys(subprojects))}


def _named(pairs) -> list[str]:
    """Subproject names of `name: consumer, ...` groups, in the order the message prints them."""
    return [x for n, cons in pairs for x in (n, *cons)]


def _narrow(s, deps, reg: Registry) -> list[dict]:
    """A narrow story passes when it depends on every consumer of each exporter its dependencies point at.

    The plan does not say which contract a narrow changes, so when its dependencies cover the consumers of one
    such exporter but not of another, the target is ambiguous: the gate decides on the real diff.
    """
    dep_subs = {d.subproject for d in deps}
    candidates = {e.name: reg.consumers(e.name) for e in reg.values() if e.exports and e.name != CONTRACTS}
    related = {name: cons for name, cons in candidates.items() if set(cons) & dep_subs}
    covered = sorted(n for n, cons in related.items() if set(cons) <= dep_subs)
    if related and len(covered) == len(related):
        return []
    if covered:
        gap_pairs = [(n, sorted(set(c) - dep_subs)) for n, c in sorted(related.items()) if n not in covered]
        gaps = "; ".join(f"{n}: {', '.join(c)}" for n, c in gap_pairs)
        return [{"severity": "concern", "code": "ambiguous-narrow-target", "story": s.id,
                 "refs": _refs(subprojects=[*covered, *_named(gap_pairs)]),
                 "message": f"story {s.id} covers every consumer of {', '.join(covered)} but not of other exporters its "
                            f"dependencies point at ({gaps}); if it narrows one of those, the gate will fail it"}]
    if not related:
        unconsumed = sorted(n for n, c in candidates.items() if not c)
        if len(unconsumed) == len(candidates):
            return []  # nobody imports these contracts: narrowing one migrates no one, and the gate accepts it
        missing_pairs = [(n, c) for n, c in sorted(candidates.items()) if c]
        missing = "; ".join(f"{n}: {', '.join(c)}" for n, c in missing_pairs)
        if unconsumed:
            return [{"severity": "concern", "code": "ambiguous-narrow-target", "story": s.id,
                     "refs": _refs(subprojects=[*unconsumed, *_named(missing_pairs)]),
                     "message": f"story {s.id} depends on no consumer's migration story: it passes the gate only if it "
                                f"narrows a contract nobody imports ({', '.join(unconsumed)}), not one of {missing}"}]
        return [{"severity": "fail", "code": "narrow-without-migration", "story": s.id,
                 "refs": _refs(subprojects=_named(missing_pairs)),
                 "message": f"story {s.id} narrows a contract but depends on no consumer's migration story "
                            f"(consumers per exporter: {missing})"}]
    gap_pairs = [(n, sorted(set(c) - dep_subs)) for n, c in sorted(related.items())]
    gaps = "; ".join(f"{n}: {', '.join(c)}" for n, c in gap_pairs)
    return [{"severity": "fail", "code": "narrow-missing-consumers", "story": s.id,
             "refs": _refs(subprojects=_named(gap_pairs)),
             "message": f"story {s.id} narrows a contract without depending on a story of every consumer ({gaps})"}]
