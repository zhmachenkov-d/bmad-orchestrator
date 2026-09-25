"""sprint-status.yaml: done-invariant check, derivation from markers, and the git merge driver.

The check reads statuses with a YAML parser. Edits (derive, merge driver) are line-level so the stock
file's comments, ordering and other sections survive untouched; a file whose entries the line editor
cannot see the way YAML does is rejected rather than half-edited.
Invariants orch owns: a story is `done` only if its marker is merged; an epic is `done` only if its close
record exists. `in-progress` / `review` stay free because stock bmad-build writes them.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

from . import OrchError

STORY_RANK = {"backlog": 0, "ready-for-dev": 1, "in-progress": 2, "review": 3, "done": 4}
EPIC_RANK = {"backlog": 0, "in-progress": 1, "done": 2}
RETRO_RANK = {"optional": 0, "done": 1}
LEGACY = {"drafted": "ready-for-dev", "contexted": "in-progress"}

BLOCK_RE = re.compile(r"^development_status:\s*(#.*)?$")
ENTRY_RE = re.compile(r"^(?P<indent>\s+)(?P<key>[\w.-]+):(?P<sp>\s*)(?P<status>[\w-]+)(?P<rest>\s*(#.*)?)$")
EPIC_KEY_RE = re.compile(r"^epic-(\d+)$")
RETRO_KEY_RE = re.compile(r"^epic-(\d+)-retrospective$")
STORY_KEY_RE = re.compile(r"^(\d+)-(\d+[a-z]?)-.+")
CONFLICT_RE = re.compile(r"^(<<<<<<<|>>>>>>>|=======$)", re.MULTILINE)
UPDATED_RE = re.compile(r"^last_updated:.*$", re.MULTILINE)

DRIVER = "orch-sprint-status"


def kind(key: str) -> str | None:
    if EPIC_KEY_RE.match(key):
        return "epic"
    if RETRO_KEY_RE.match(key):
        return "retro"
    if STORY_KEY_RE.match(key):
        return "story"
    return None


def rank(key: str, status: str) -> int:
    table = {"epic": EPIC_RANK, "retro": RETRO_RANK, "story": STORY_RANK}.get(kind(key) or "", {})
    return table.get(LEGACY.get(status, status), -1)


def story_key(key: str) -> str | None:
    m = STORY_KEY_RE.match(key)
    return f"{m.group(1)}-{m.group(2)}" if m else None


def split(text: str) -> tuple[list[str], list[str], list[str]]:
    """(lines before development_status entries, entry lines, lines after)."""
    lines = text.splitlines(keepends=True)
    start = next((i for i, l in enumerate(lines) if BLOCK_RE.match(l.rstrip("\n"))), None)
    if start is None:
        return lines, [], []
    end = start + 1
    while end < len(lines) and (lines[end].startswith((" ", "\t")) or not lines[end].strip()):
        end += 1
    # Trailing blank lines belong to the next section.
    while end > start + 1 and not lines[end - 1].strip():
        end -= 1
    return lines[: start + 1], lines[start + 1: end], lines[end:]


def entries(text: str) -> dict[str, str]:
    _, block, _ = split(text)
    found = {}
    for line in block:
        m = ENTRY_RE.match(line.rstrip("\n"))
        if m:
            found[m.group("key")] = LEGACY.get(m.group("status"), m.group("status"))
    return found


def parsed(text: str) -> dict[str, str] | None:
    """development_status as YAML sees it (legacy statuses mapped); None if the file is not valid YAML."""
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError:
        return None
    block = data.get("development_status") if isinstance(data, dict) else None
    if not isinstance(block, dict):
        return {}
    return {str(k): LEGACY.get(str(v), str(v)) for k, v in block.items()}


def line_mismatch(text: str) -> list[str]:
    """Keys whose status the line editor reads differently from YAML (comments at column 0, quoted values...)."""
    by_yaml, by_line = parsed(text) or {}, entries(text)
    return sorted(k for k in set(by_yaml) | set(by_line) if by_yaml.get(k) != by_line.get(k))


def _set(block: list[str], updates: dict[str, str]) -> list[str]:
    out = []
    for line in block:
        m = ENTRY_RE.match(line.rstrip("\n"))
        if m and m.group("key") in updates:
            nl = "\n" if line.endswith("\n") else ""
            line = f"{m.group('indent')}{m.group('key')}:{m.group('sp') or ' '}{updates[m.group('key')]}{m.group('rest')}{nl}"
        out.append(line)
    return out


def check(text: str, merged: set[str], closed: set[int], unknown: set[str] = frozenset(),
          pending: set[str] = frozenset()) -> dict:
    """Done invariants. `merged` = story keys ('1-2') with a merged marker, incl. this PR's.

    `unknown` = story keys whose repo could not be read; a done story there fails as unverifiable, not as unmerged.
    `pending` = this PR's own story: it may be done here, but is not yet merged, so it is never told to be done.
    """
    fails, warns = [], []
    if CONFLICT_RE.search(text):
        fails.append({"code": "conflict-markers", "message": "sprint-status.yaml contains merge conflict markers",
                      "hint": "register the merge driver in this clone (orch.py sprint-status install-driver), then rebuild with orch.py sprint-status derive --write"})
        return {"fail": fails, "warn": warns}
    statuses = parsed(text)
    if statuses is None:
        fails.append({"code": "sprint-status-invalid", "message": "sprint-status.yaml is not valid YAML"})
        return {"fail": fails, "warn": warns}
    if not statuses:
        fails.append({"code": "no-development-status", "message": "sprint-status.yaml has no development_status entries"})
        return {"fail": fails, "warn": warns}
    mismatch = line_mismatch(text)
    if mismatch:
        fails.append({"code": "sprint-status-layout", "keys": mismatch,
                      "message": "development_status entries orch cannot edit line by line: " + ", ".join(mismatch),
                      "hint": "keep the block as unquoted `key: status` lines indented under development_status, with no column-0 comments inside it"})
    for key, status in statuses.items():
        k = kind(key)
        if k == "story":
            sk = story_key(key)
            if status == "done" and sk not in merged and sk in unknown:
                fails.append({"code": "done-unverifiable", "key": key,
                              "message": f"{key} is done but its repo could not be read, so its marker cannot be verified",
                              "hint": "give this runner read access to that repo (or drop --offline) and re-run"})
            elif status == "done" and sk not in merged:
                fails.append({"code": "done-without-marker", "key": key,
                              "message": f"{key} is done but no .orch/stories/{sk}.yaml is merged",
                              "hint": "only a merged story may be done; set it back to review or merge the story PR first"})
            elif sk in merged and status != "done" and sk not in pending:
                warns.append({"code": "merged-not-done", "key": key, "message": f"{key} is merged but marked {status}",
                              "hint": "run orch.py sprint-status derive --write"})
            if rank(key, status) < 0:
                fails.append({"code": "illegal-status", "key": key, "message": f"{key}: unknown status '{status}'"})
        elif k == "epic":
            n = int(EPIC_KEY_RE.match(key).group(1))
            if status == "done" and n not in closed:
                fails.append({"code": "epic-done-without-close", "key": key,
                              "message": f"{key} is done without an epic close record",
                              "hint": "close the epic through orch-status; it archives markers, verifies pins and sets done"})
            elif n in closed and status != "done":
                warns.append({"code": "closed-not-done", "key": key, "message": f"{key} is closed but marked {status}",
                              "hint": "run orch.py sprint-status derive --write"})
    return {"fail": fails, "warn": warns}


def derive(text: str, merged: set[str], closed: set[int]) -> tuple[str, list[dict]]:
    """Apply the invariants: merged stories -> done, unmerged done -> review, epic done iff closed."""
    mismatch = line_mismatch(text)
    if mismatch:
        raise OrchError("sprint-status.yaml has development_status entries orch cannot edit line by line: "
                        + ", ".join(mismatch) + "; use unquoted `key: status` lines with no column-0 comments inside the block")
    head, block, tail = split(text)
    updates, changes = {}, []
    for key, status in entries(text).items():
        k, new = kind(key), status
        if k == "story":
            sk = story_key(key)
            if sk in merged:
                new = "done"
            elif status == "done":
                new = "review"
        elif k == "epic":
            n = int(EPIC_KEY_RE.match(key).group(1))
            if n in closed:
                new = "done"
            elif status == "done":
                new = "in-progress"
        if new != status:
            updates[key] = new
            changes.append({"key": key, "from": status, "to": new})
    return "".join(head + _set(block, updates) + tail), changes


def _three_way(base: str, ours: str, theirs: str) -> str | None:
    if ours == theirs or theirs == base:
        return ours
    if ours == base:
        return theirs
    return None


def merge(base: str, ours: str, theirs: str) -> str | None:
    """Merge-driver resolution: per-key max rank, trivial 3-way for everything else; None = real conflict."""
    b_head, _, b_tail = split(base)
    o_head, o_block, o_tail = split(ours)
    t_head, t_block, t_tail = split(theirs)

    def strip_updated(lines):
        return UPDATED_RE.sub("last_updated:", "".join(lines))

    if _three_way(strip_updated(b_head), strip_updated(o_head), strip_updated(t_head)) is None:
        return None
    tail = _three_way("".join(b_tail), "".join(o_tail), "".join(t_tail))
    if tail is None:
        return None
    ours_changed = strip_updated(o_head) != strip_updated(b_head)
    head = "".join(o_head if ours_changed or strip_updated(t_head) == strip_updated(b_head) else t_head)
    stamps = [m.group(0) for m in UPDATED_RE.finditer("".join(o_head) + "".join(t_head))]
    if stamps:
        head = UPDATED_RE.sub(max(stamps, key=_stamp_order), head, count=1)

    o_entries, t_entries = entries(ours), entries(theirs)
    updates = {k: s for k, s in t_entries.items() if k in o_entries and rank(k, s) > rank(k, o_entries[k])}
    block = _set(o_block, updates)
    # Keys only on their side go right after their nearest predecessor that is already in the block.
    t_lines = [(m.group("key"), l) for l in t_block if (m := ENTRY_RE.match(l.rstrip("\n")))]
    for i, (key, line) in enumerate(t_lines):
        if key in o_entries:
            continue
        keys_now = [m.group("key") if (m := ENTRY_RE.match(l.rstrip("\n"))) else None for l in block]
        pos = len(block)
        for pred, _ in reversed(t_lines[:i]):
            if pred in keys_now:
                pos = keys_now.index(pred) + 1
                break
        block.insert(pos, line if line.endswith("\n") else line + "\n")
        o_entries[key] = t_entries[key]
    if block and not block[-1].endswith("\n"):
        block[-1] += "\n"
    return head + "".join(block) + tail


def _stamp_order(line: str) -> str:
    """Sortable form of a last_updated stamp (stock format MM-DD-YYYY HH:MM, ISO accepted)."""
    value = line.split(":", 1)[1].strip().strip("'\"")
    m = re.match(r"^(\d{2})-(\d{2})-(\d{4})(.*)$", value)
    return f"{m.group(3)}-{m.group(1)}-{m.group(2)}{m.group(4)}" if m else value


def install_driver(repo: Path, status_path: str, orch_py: Path) -> dict:
    """Idempotently add the .gitattributes entry and register the driver in this clone's git config."""
    from .gitio import git

    attrs = Path(repo) / ".gitattributes"
    line = f"{status_path} merge={DRIVER}"
    existing = attrs.read_text(encoding="utf-8") if attrs.exists() else ""
    added = line not in existing.splitlines()
    if added:
        attrs.write_text(existing + ("" if not existing or existing.endswith("\n") else "\n") + line + "\n", encoding="utf-8")
    cmd = f'uv run "{Path(orch_py).resolve()}" sprint-status merge %O %A %B'
    git(repo, "config", f"merge.{DRIVER}.name", "orch sprint-status merge (derived from markers)")
    git(repo, "config", f"merge.{DRIVER}.driver", cmd)
    return {"gitattributes": str(attrs), "attribute_added": added, "driver": cmd}


def driver_registered(repo: Path) -> bool:
    from .gitio import git

    return git(repo, "config", "--get", f"merge.{DRIVER}.driver", check=False).returncode == 0


def attribute_present(repo: Path) -> bool:
    attrs = Path(repo) / ".gitattributes"
    return attrs.exists() and f"merge={DRIVER}" in attrs.read_text(encoding="utf-8")
