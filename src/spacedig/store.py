"""Snapshot storage.

Snapshots live under a per-user data directory (overridable with the
``SPACEDIG_HOME`` environment variable, default ``%LOCALAPPDATA%\\spacedig`` on
Windows, ``~/.local/share/spacedig`` elsewhere).

Snapshots for a given set of roots are grouped together so we can diff the
two most recent runs of the *same* target.  The group key is a short hash of
the normalized root list, and the human-readable roots are recorded inside
each snapshot for display.

Layout::

    <home>/snapshots/<key>/<unix_ts>.snap   (gzip-compressed snapshot)
    <home>/snapshots/<key>/roots.txt        (the roots, for `spacedig list`)
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from typing import List, Optional

from . import snapshot as snap_mod
from .snapshot import Snapshot


def home_dir() -> str:
    env = os.environ.get("SPACEDIG_HOME")
    if env:
        return os.path.abspath(env)
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
        return os.path.join(base, "spacedig")
    base = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    return os.path.join(base, "spacedig")


def _snapshots_dir() -> str:
    return os.path.join(home_dir(), "snapshots")


def key_for_roots(roots: List[str]) -> str:
    """Stable short key for a set of roots (order-independent, case-folded)."""
    norm = sorted(os.path.normcase(os.path.normpath(os.path.abspath(r))) for r in roots)
    h = hashlib.sha1("\x00".join(norm).encode("utf-8")).hexdigest()
    return h[:12]


def _group_dir(key: str) -> str:
    return os.path.join(_snapshots_dir(), key)


@dataclass
class StoredSnapshot:
    key: str
    timestamp: int
    path: str

    def load(self) -> Snapshot:
        with open(self.path, "rb") as fh:
            return snap_mod.loads(fh.read())


def save(snap: Snapshot) -> StoredSnapshot:
    """Persist a snapshot and return a handle to it."""
    key = key_for_roots(snap.roots)
    group = _group_dir(key)
    os.makedirs(group, exist_ok=True)

    # Record roots once for `list`.
    roots_file = os.path.join(group, "roots.txt")
    if not os.path.exists(roots_file):
        with open(roots_file, "w", encoding="utf-8") as fh:
            fh.write("\n".join(snap.roots))

    ts = snap.created
    path = os.path.join(group, f"{ts}.snap")
    # Avoid clobbering if two runs land on the same second.
    n = 1
    while os.path.exists(path):
        path = os.path.join(group, f"{ts}_{n}.snap")
        n += 1
    with open(path, "wb") as fh:
        fh.write(snap_mod.dumps(snap))
    return StoredSnapshot(key=key, timestamp=ts, path=path)


def _parse_ts(filename: str) -> int:
    base = filename[:-5]  # strip .snap
    base = base.split("_", 1)[0]
    try:
        return int(base)
    except ValueError:
        return 0


def list_for_key(key: str) -> List[StoredSnapshot]:
    """All stored snapshots for a key, oldest first."""
    group = _group_dir(key)
    if not os.path.isdir(group):
        return []
    items: List[StoredSnapshot] = []
    for name in os.listdir(group):
        if not name.endswith(".snap"):
            continue
        items.append(
            StoredSnapshot(
                key=key, timestamp=_parse_ts(name), path=os.path.join(group, name)
            )
        )
    items.sort(key=lambda s: (s.timestamp, s.path))
    return items


def latest_for_roots(roots: List[str]) -> Optional[StoredSnapshot]:
    items = list_for_key(key_for_roots(roots))
    return items[-1] if items else None


def previous_for_roots(roots: List[str]) -> Optional[StoredSnapshot]:
    """The second-most-recent snapshot for these roots, if any."""
    items = list_for_key(key_for_roots(roots))
    return items[-2] if len(items) >= 2 else None


@dataclass
class Group:
    key: str
    roots: List[str]
    snapshots: List[StoredSnapshot]


def list_groups() -> List[Group]:
    root = _snapshots_dir()
    if not os.path.isdir(root):
        return []
    groups: List[Group] = []
    for key in os.listdir(root):
        gdir = os.path.join(root, key)
        if not os.path.isdir(gdir):
            continue
        roots: List[str] = []
        roots_file = os.path.join(gdir, "roots.txt")
        if os.path.exists(roots_file):
            with open(roots_file, "r", encoding="utf-8") as fh:
                roots = [ln for ln in fh.read().splitlines() if ln]
        groups.append(Group(key=key, roots=roots, snapshots=list_for_key(key)))
    groups.sort(key=lambda g: g.roots)
    return groups


def prune(key: str, keep: int) -> List[StoredSnapshot]:
    """Delete all but the newest ``keep`` snapshots for a key.

    Returns the list of removed snapshots.
    """
    items = list_for_key(key)
    if keep < 0 or len(items) <= keep:
        return []
    to_remove = items[: len(items) - keep]
    for s in to_remove:
        try:
            os.remove(s.path)
        except OSError:
            pass
    return to_remove
