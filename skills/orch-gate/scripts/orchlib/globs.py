"""Path globs with `**` support, matched against repo-relative POSIX paths."""

from __future__ import annotations

import re
from functools import lru_cache


@lru_cache(maxsize=None)
def compile_glob(pattern: str) -> re.Pattern:
    p = pattern.strip().lstrip("/")
    if p.endswith("/"):
        p += "**"
    i, rx = 0, ""
    while i < len(p):
        if p.startswith("**/", i):
            rx += r"(?:.*/)?"
            i += 3
        elif p.startswith("**", i):
            rx += r".*"
            i += 2
        elif p[i] == "*":
            rx += r"[^/]*"
            i += 1
        elif p[i] == "?":
            rx += r"[^/]"
            i += 1
        else:
            rx += re.escape(p[i])
            i += 1
    return re.compile(rf"^{rx}$")


def matches(path: str, patterns: list[str]) -> bool:
    return any(compile_glob(p).match(path) for p in patterns)


def literal_prefix(pattern: str) -> str:
    """The part of a glob before its first wildcard, used to approximate overlap between globs."""
    m = re.search(r"[*?\[]", pattern)
    return pattern[: m.start()] if m else pattern


def may_overlap(a: str, b: str) -> bool:
    pa, pb = literal_prefix(a.lstrip("/")), literal_prefix(b.lstrip("/"))
    return pa.startswith(pb) or pb.startswith(pa)
