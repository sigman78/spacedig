"""Filesystem scanning.

Walks one or more root directories and produces a :class:`~spacedig.snapshot.Snapshot`.

Design choices that keep snapshots compact *and* useful:

* Every **directory** is recorded with its *recursive* size and file count.
  This is what answers "which folders are eating space" without storing an
  entry per file.
* Individual **files** are only recorded when they are at least ``threshold``
  bytes (default 50 MiB).  This surfaces the handful of giant files that
  matter while keeping the snapshot tiny.

The walk is bottom-up (``topdown=False``) so each directory's children are
already summed when we reach it.  Symlinks/junctions are not followed, which
avoids cycles and double counting.
"""

from __future__ import annotations

import os
from typing import Callable, Dict, Iterable, List, Optional

from .snapshot import Entry, Snapshot

DEFAULT_THRESHOLD = 50 * 1024 * 1024  # 50 MiB


def _norm_root(root: str) -> str:
    return os.path.normpath(os.path.abspath(root))


def scan(
    roots: Iterable[str],
    threshold: int = DEFAULT_THRESHOLD,
    now: int = 0,
    on_error: Optional[Callable[[OSError], None]] = None,
) -> Snapshot:
    """Scan ``roots`` and return a snapshot.

    ``now`` is the snapshot timestamp (unix seconds); pass it explicitly so
    callers control time (and tests stay deterministic).  ``on_error`` is
    invoked for each path that cannot be read (it is otherwise skipped).
    """
    norm_roots: List[str] = []
    for r in roots:
        nr = _norm_root(r)
        if nr not in norm_roots:
            norm_roots.append(nr)

    dir_size: Dict[str, int] = {}
    dir_fcount: Dict[str, int] = {}
    file_entries: List[Entry] = []

    def _report(exc: OSError) -> None:
        if on_error is not None:
            on_error(exc)

    for root in norm_roots:
        if not os.path.isdir(root):
            _report(OSError(f"not a directory: {root}"))
            continue

        for dirpath, dirnames, filenames in os.walk(
            root, topdown=False, onerror=_report, followlinks=False
        ):
            own_size = 0
            own_files = 0
            for name in filenames:
                fpath = os.path.join(dirpath, name)
                try:
                    st = os.stat(fpath, follow_symlinks=False)
                except OSError as exc:
                    _report(exc)
                    continue
                size = st.st_size
                own_size += size
                own_files += 1
                if size >= threshold:
                    file_entries.append(
                        Entry(
                            path=fpath,
                            size=size,
                            mtime=int(st.st_mtime),
                            is_dir=False,
                            fcount=0,
                        )
                    )

            total = own_size
            tfcount = own_files
            for d in dirnames:
                cpath = os.path.join(dirpath, d)
                # A child missing from the maps is a symlink/junction we did
                # not descend into, or an unreadable dir: count it as zero.
                total += dir_size.get(cpath, 0)
                tfcount += dir_fcount.get(cpath, 0)

            dir_size[dirpath] = total
            dir_fcount[dirpath] = tfcount

    # Directory mtime is not used by diffs, so we skip the extra stat and keep
    # it at 0 (file mtimes are recorded). This keeps the scan fast and the
    # snapshot compact.
    entries: List[Entry] = [
        Entry(path=p, size=dir_size[p], mtime=0, is_dir=True, fcount=dir_fcount[p])
        for p in dir_size
    ]
    entries.extend(file_entries)
    entries.sort(key=lambda e: e.path)

    return Snapshot(
        roots=norm_roots,
        created=now,
        threshold=threshold,
        entries=entries,
    )
