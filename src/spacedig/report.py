"""Render a diff as a human-readable text map.

The output has three parts:

1. A one-line **summary** (roots, time span, net change).
2. A **tree map** showing the directories where space changed the most, with
   their parent directories included for context.
3. Compact **lists** of the biggest growers / shrinkers / new & removed items.
"""

from __future__ import annotations

import os
import time
from typing import Dict, List, Optional, Set

from .diff import Change, DiffResult, Status

_UNITS = ["B", "KB", "MB", "GB", "TB", "PB"]


def humansize(n: int, signed: bool = False) -> str:
    """Format a byte count, optionally with an explicit +/- sign."""
    a = abs(n)
    f = float(a)
    i = 0
    while f >= 1024 and i < len(_UNITS) - 1:
        f /= 1024.0
        i += 1
    sign = ""
    if signed:
        sign = "+" if n > 0 else ("-" if n < 0 else "")
    if i == 0:
        return f"{sign}{int(a)} {_UNITS[i]}"
    return f"{sign}{f:.1f} {_UNITS[i]}"


def _tag(status: Status) -> str:
    return {
        Status.NEW: "NEW",
        Status.REMOVED: "DEL",
        Status.GREW: "grew",
        Status.SHRANK: "shrank",
        Status.SAME: "",
    }[status]


def _fmt_time(ts: int) -> str:
    if not ts:
        return "?"
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(ts))


def _node_label(c: Change, name: str) -> str:
    parts = [name, humansize(c.delta, signed=True)]
    tag = _tag(c.status)
    if tag:
        parts.append(f"[{tag}]")
    fdelta = c.new_fcount - c.old_fcount
    if c.is_dir and fdelta:
        parts.append(f"({fdelta:+d} files)")
    return "  ".join(parts)


def _build_tree(
    diff: DiffResult, top: int, min_bytes: int
) -> (Dict[str, Change], Dict[str, List[str]], List[str]):
    """Pick the most-changed paths and stitch in their ancestors.

    Returns ``(nodes, children, roots)`` where ``nodes`` maps path -> Change,
    ``children`` maps path -> child paths, and ``roots`` are the top-level
    paths to start rendering from.
    """
    by_path: Dict[str, Change] = {c.path: c for c in diff.changes}

    changed = [c for c in diff.changed() if abs(c.delta) >= min_bytes]
    # changed() is already sorted by |delta| desc.
    interesting = changed[:top]

    keep: Set[str] = set()
    for c in interesting:
        path = c.path
        while path and path not in keep:
            keep.add(path)
            parent = os.path.dirname(path)
            if parent == path or parent not in by_path:
                break
            path = parent

    nodes = {p: by_path[p] for p in keep if p in by_path}
    children: Dict[str, List[str]] = {p: [] for p in nodes}
    roots: List[str] = []
    for p in nodes:
        parent = os.path.dirname(p)
        if parent in nodes:
            children[parent].append(p)
        else:
            roots.append(p)

    roots.sort(key=lambda p: (-abs(nodes[p].delta), p))
    return nodes, children, roots


def _render_tree(diff: DiffResult, top: int, min_bytes: int) -> List[str]:
    nodes, children, roots = _build_tree(diff, top, min_bytes)
    if not roots:
        return []

    out: List[str] = []

    def rec(path: str, prefix: str) -> None:
        kids = sorted(children.get(path, []), key=lambda p: (-abs(nodes[p].delta), p))
        for i, kid in enumerate(kids):
            last = i == len(kids) - 1
            branch = "└─ " if last else "├─ "
            out.append(prefix + branch + _node_label(nodes[kid], os.path.basename(kid) or kid))
            rec(kid, prefix + ("   " if last else "│  "))

    for r in roots:
        out.append(_node_label(nodes[r], r))
        rec(r, "")
    return out


def _render_lists(diff: DiffResult, limit: int) -> List[str]:
    changed = diff.changed()
    growers = [c for c in changed if c.status in (Status.GREW, Status.NEW)]
    shrinkers = [c for c in changed if c.status in (Status.SHRANK, Status.REMOVED)]
    new_files = [c for c in changed if c.status is Status.NEW and not c.is_dir]
    removed = [c for c in changed if c.status is Status.REMOVED]

    out: List[str] = []

    def section(title: str, items: List[Change]) -> None:
        if not items:
            return
        out.append("")
        out.append(title)
        for c in items[:limit]:
            tag = _tag(c.status)
            tagstr = f" [{tag}]" if tag in ("NEW", "DEL") else ""
            out.append(f"  {humansize(c.delta, signed=True):>12}  {c.path}{tagstr}")

    section("Top growers:", growers)
    section("Top shrinkers:", shrinkers)
    if new_files:
        section("New large files:", new_files)
    if removed:
        section("Removed:", removed)
    return out


def render(
    diff: DiffResult,
    top: int = 20,
    list_limit: int = 10,
    min_bytes: int = 1,
    show_lists: bool = True,
) -> str:
    """Render the full text report for a diff."""
    lines: List[str] = []
    roots = diff.new.roots or diff.old.roots
    lines.append("spacedig — disk change report")
    lines.append(f"  roots:  {', '.join(roots)}")
    lines.append(
        f"  span:   {_fmt_time(diff.old.created)}  ->  {_fmt_time(diff.new.created)}"
    )
    lines.append(
        f"  total:  {humansize(diff.old.total_size)}  ->  "
        f"{humansize(diff.new.total_size)}   "
        f"(Δ {humansize(diff.total_delta, signed=True)})"
    )

    n_changed = len(diff.changed())
    if n_changed == 0:
        lines.append("")
        lines.append("No changes since the previous snapshot.")
        return "\n".join(lines)

    tree = _render_tree(diff, top=top, min_bytes=min_bytes)
    if tree:
        lines.append("")
        lines.append("Where the space went:")
        lines.extend("  " + t for t in tree)

    if show_lists:
        lines.extend(_render_lists(diff, limit=list_limit))

    return "\n".join(lines)
