"""Compute the difference between two snapshots."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Dict, List

from .snapshot import Entry, Snapshot


class Status(str, Enum):
    NEW = "new"
    REMOVED = "removed"
    GREW = "grew"
    SHRANK = "shrank"
    SAME = "same"


@dataclass
class Change:
    path: str
    is_dir: bool
    old_size: int
    new_size: int
    old_fcount: int
    new_fcount: int

    @property
    def delta(self) -> int:
        return self.new_size - self.old_size

    # _in_old / _in_new record whether the path existed in each snapshot;
    # they are set by diff() and drive NEW/REMOVED classification.
    _in_old: bool = True
    _in_new: bool = True

    @property
    def status(self) -> Status:
        if not self._in_old:
            return Status.NEW
        if not self._in_new:
            return Status.REMOVED
        if self.delta > 0:
            return Status.GREW
        if self.delta < 0:
            return Status.SHRANK
        return Status.SAME


@dataclass
class DiffResult:
    old: Snapshot
    new: Snapshot
    changes: List[Change]

    @property
    def total_delta(self) -> int:
        return self.new.total_size - self.old.total_size

    def changed(self) -> List[Change]:
        """Only entries whose size actually changed (or appeared/vanished)."""
        return [c for c in self.changes if c.status is not Status.SAME]


def diff(old: Snapshot, new: Snapshot) -> DiffResult:
    """Diff two snapshots, returning a :class:`DiffResult`.

    Both snapshots' entries are matched by path.  Directories and files are
    diffed the same way; the consumer can filter by ``is_dir``.
    """
    old_map: Dict[str, Entry] = old.by_path()
    new_map: Dict[str, Entry] = new.by_path()

    changes: List[Change] = []
    for path in old_map.keys() | new_map.keys():
        o = old_map.get(path)
        n = new_map.get(path)
        is_dir = (o.is_dir if o is not None else n.is_dir)

        # Individual files are only recorded above each snapshot's threshold.
        # If a file appears/vanishes only because the two snapshots used
        # different thresholds (it stayed below one of them), that's not a real
        # change — its size is already reflected in the directory totals. Skip
        # it to avoid spurious NEW/REMOVED noise. Directories are always
        # recorded, so they are never skipped.
        if not is_dir:
            if o is None and n is not None and n.size < old.threshold:
                continue
            if n is None and o is not None and o.size < new.threshold:
                continue
        c = Change(
            path=path,
            is_dir=is_dir,
            old_size=o.size if o else 0,
            new_size=n.size if n else 0,
            old_fcount=o.fcount if o else 0,
            new_fcount=n.fcount if n else 0,
        )
        c._in_old = o is not None
        c._in_new = n is not None
        changes.append(c)

    # Largest absolute change first; ties broken by path for stable output.
    changes.sort(key=lambda c: (-abs(c.delta), c.path))
    return DiffResult(old=old, new=new, changes=changes)
