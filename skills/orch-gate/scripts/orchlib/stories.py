"""Stories and the story DAG, parsed from the stock `epics.md` plus orch bold-label metadata.

Under each `### Story N.M: Title` heading, orch expects:

    **Subproject:** payment-service
    **Depends on:** 1.1, 1.3        (or: none)
    **Contract change:** none       (none | expand | narrow; optional, default none)
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import asdict, dataclass, field
from pathlib import PurePosixPath

from .config import Config
from .gitio import Tree
from .registry import CONTRACTS, Registry

# Same heading grammar as stock bmad-sprint-planning's sprint_plan.py.
EPIC_RE = re.compile(r"^#{1,3}\s*Epic\s+(\d+)\s*:?\s*(.*?)\s*#*\s*$", re.IGNORECASE)
STORY_RE = re.compile(r"^#{2,4}\s*Story\s+(\d+)\.(\d+[a-z]?)\s*:?\s*(.*?)\s*#*\s*$", re.IGNORECASE)
FENCE_RE = re.compile(r"^\s{0,3}(?:```|~~~)")
LABEL_RE = re.compile(r"^\s*\*\*\s*(subproject|depends on|contract change)\s*(?::\*\*|\*\*\s*:)\s*(.*?)\s*$", re.IGNORECASE)
ID_RE = re.compile(r"^(\d+)\.(\d+[a-z]?)$")
KEY_RE = re.compile(r"^(\d+)-(\d+[a-z]?)$")
SPRINT_KEY_RE = re.compile(r"^(\d+)-(\d+[a-z]?)-.+")
CONTRACT_CHANGES = ("none", "expand", "narrow")
NONE_WORDS = {"", "none", "-", "—", "n/a"}


@dataclass
class Story:
    id: str                 # "1.2"
    key: str                # "1-2" — marker, claim and branch id
    epic: int
    title: str
    sprint_key: str         # stock sprint-status key "1-2-title-slug"
    subproject: str | None = None
    depends_on: list[str] = field(default_factory=list)
    contract_change: str = "none"
    source: str = ""
    line: int = 0

    def to_dict(self) -> dict:
        return asdict(self)


def id_to_key(story_id: str) -> str:
    return story_id.replace(".", "-")


def key_from_any(value: str) -> str | None:
    """Accept '1.2', '1-2' or a sprint key '1-2-some-title' and return '1-2'."""
    v = value.strip()
    for rx in (ID_RE, KEY_RE, SPRINT_KEY_RE):
        m = rx.match(v)
        if m:
            return f"{m.group(1)}-{m.group(2)}"
    return None


def _slug(text: str, maxlen: int = 60) -> str:
    # Mirrors stock sprint_plan._slug so sprint keys line up.
    slug = re.sub(r"[^\w]+", "-", str(text).lower(), flags=re.UNICODE).strip("-")
    slug = slug[:maxlen].strip("-")
    return slug or hashlib.sha256(str(text).encode("utf-8")).hexdigest()[:8]


def parse(text: str, source: str) -> tuple[list[Story], list[dict]]:
    stories, issues = [], []
    current: Story | None = None
    seen_labels: set[str] = set()
    in_fence = False
    for lineno, line in enumerate(text.splitlines(), 1):
        if FENCE_RE.match(line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        if EPIC_RE.match(line):
            current = None
            continue
        m = STORY_RE.match(line)
        if m:
            epic, num, title = int(m.group(1)), m.group(2), m.group(3)
            sid = f"{epic}.{num}"
            current = Story(sid, id_to_key(sid), epic, title, f"{epic}-{num}-{_slug(title)}", source=source, line=lineno)
            stories.append(current)
            seen_labels = set()
            continue
        if line.startswith("#"):
            current = None
            continue
        lm = LABEL_RE.match(line)
        if not (lm and current):
            continue
        label, value = lm.group(1).lower(), lm.group(2).strip()
        where = f"{source}:{lineno}"
        if label in seen_labels:
            issues.append(_issue("duplicate-label", f"story {current.id}: '{label}' given twice ({where})", current.id))
        seen_labels.add(label)
        if label == "subproject":
            current.subproject = value.strip("`") or None
        elif label == "depends on":
            deps = [] if value.lower() in NONE_WORDS else [d.strip().strip("`") for d in re.split(r"[,\s]+", value) if d.strip()]
            bad = [d for d in deps if not ID_RE.match(d)]
            if bad:
                issues.append(_issue("bad-depends-on", f"story {current.id}: depends_on entries must look like N.M, got {bad} ({where})", current.id))
            current.depends_on = [d for d in deps if ID_RE.match(d)]
        else:
            v = value.lower().strip("`")
            if v not in CONTRACT_CHANGES:
                issues.append(_issue("bad-contract-change", f"story {current.id}: contract change must be one of {CONTRACT_CHANGES}, got '{value}' ({where})", current.id))
            else:
                current.contract_change = v
    return stories, issues


def _issue(code: str, message: str, story: str | None = None) -> dict:
    return {"code": code, "story": story, "message": message}


def epic_files(tree: Tree, cfg: Config) -> list[str]:
    return sorted(p for p in tree.list(cfg.planning_artifacts)
                  if p.endswith(".md") and PurePosixPath(p).name.lower().startswith("epic"))


class StorySet(dict):
    """key ('1-2') -> Story, in document order; `issues` holds parse and validation problems."""

    issues: list[dict]

    def by_id(self, story_id: str) -> Story | None:
        return self.get(id_to_key(story_id))

    def dependents(self, key: str) -> list[str]:
        sid = self[key].id
        return [s.key for s in self.values() if sid in s.depends_on]

    def to_dict(self) -> dict:
        return {"stories": [s.to_dict() for s in self.values()], "issues": self.issues}


def load(tree: Tree, cfg: Config, reg: Registry | None = None) -> StorySet:
    out = StorySet()
    out.issues = []
    files = epic_files(tree, cfg)
    if not files:
        out.issues.append(_issue("no-epics", f"no epic files (epic*.md) under {cfg.planning_artifacts}"))
    for path in files:
        stories, issues = parse(tree.text(path) or "", path)
        out.issues += issues
        for s in stories:
            if s.key in out:
                out.issues.append(_issue("duplicate-story", f"story {s.id} defined twice ({out[s.key].source}, {path})", s.id))
                continue
            out[s.key] = s
    out.issues += validate(out, reg)
    return out


def validate(stories: StorySet, reg: Registry | None) -> list[dict]:
    issues = []
    order = {k: i for i, k in enumerate(stories)}
    for s in stories.values():
        if not s.subproject:
            issues.append(_issue("missing-subproject", f"story {s.id} has no **Subproject:** line", s.id))
        elif reg is not None and s.subproject not in reg:
            issues.append(_issue("unknown-subproject", f"story {s.id}: subproject '{s.subproject}' is not in the registry", s.id))
        if s.contract_change != "none" and s.subproject and s.subproject != CONTRACTS:
            issues.append(_issue("contract-change-outside-contracts", f"story {s.id}: contract change '{s.contract_change}' only applies to '{CONTRACTS}' stories", s.id))
        for dep in s.depends_on:
            dk = id_to_key(dep)
            if dk not in stories:
                issues.append(_issue("unknown-dependency", f"story {s.id} depends on unknown story {dep}", s.id))
            elif order[dk] >= order[s.key]:
                issues.append(_issue("forward-dependency", f"story {s.id} depends on {dep}, which is not earlier in the plan", s.id))
    return issues
