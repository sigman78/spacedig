"""Filesystem scanning.

Walks one or more root directories and produces a :class:`~spacedig.snapshot.Snapshot`.

Design choices that keep snapshots compact *and* useful:

* Every **directory** is recorded with its *recursive* size and file count.
  This is what answers "which folders are eating space" without storing an
  entry per file.
* Individual **files** are only recorded when they are at least ``threshold``
  bytes (default 50 MiB).  This surfaces the handful of giant files that
  matter while keeping the snapshot tiny.

Link handling (so a single physical file is never double-counted):

* **Symlinks and junctions** (any reparse point) are *not* followed.  We detect
  them via the symlink flag and the ``FILE_ATTRIBUTE_REPARSE_POINT`` attribute
  bit (so Windows directory *junctions* — which ``os.walk`` would otherwise
  descend into — are correctly skipped, along with cyclic links).  The reparse
  point itself occupies negligible space and is not counted.
* **Hard links** point multiple names at one physical file.  When a file has
  more than one link (``st_nlink > 1``) we count its bytes only the first time
  we encounter its identity ``(st_dev, st_ino)`` within a scan, matching the
  way ``du`` reports physical usage.  Detecting this requires a real
  ``os.stat`` per file (Windows ``DirEntry.stat()`` does not populate
  ``st_nlink``/``st_ino`` — those need the file to be opened).  Pass
  ``dedupe_hardlinks=False`` to skip that cost on very large trees, at the
  price of double-counting hard-linked files.

The walk is implemented with ``os.scandir`` (post-order, so each directory is
emitted once its children are summed).  Symlink/junction detection uses the
cached ``DirEntry`` metadata and never opens those entries.
"""

from __future__ import annotations

import os
import stat as statmod
from typing import Callable, Dict, Iterable, List, Optional, Set, Tuple

from .snapshot import Entry, Snapshot

DEFAULT_THRESHOLD = 50 * 1024 * 1024  # 50 MiB

_REPARSE = getattr(statmod, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)


def _norm_root(root: str) -> str:
    return os.path.normpath(os.path.abspath(root))


def _is_reparse(entry: os.DirEntry, st: os.stat_result) -> bool:
    """True for symlinks and junctions (anything we must not follow)."""
    attrs = getattr(st, "st_file_attributes", 0)
    if attrs & _REPARSE:
        return True
    try:
        return entry.is_symlink()
    except OSError:
        return False


def scan(
    roots: Iterable[str],
    threshold: int = DEFAULT_THRESHOLD,
    now: int = 0,
    on_error: Optional[Callable[[OSError], None]] = None,
    dedupe_hardlinks: bool = False,
) -> Snapshot:
    """Scan ``roots`` and return a snapshot.

    ``now`` is the snapshot timestamp (unix seconds); pass it explicitly so
    callers control time (and tests stay deterministic).  ``on_error`` is
    invoked for each path that cannot be read (it is otherwise skipped).

    ``dedupe_hardlinks`` (off by default for speed) does a full ``os.stat`` per
    file so hard-linked files are counted once.  With it off, the fast cached
    ``DirEntry`` metadata is used and hard links are counted under each name.
    """
    norm_roots: List[str] = []
    for r in roots:
        nr = _norm_root(r)
        if nr not in norm_roots:
            norm_roots.append(nr)

    dir_entries: List[Entry] = []
    file_entries: List[Entry] = []
    # Identities of hard-linked files already counted, so their bytes are not
    # attributed twice within a single scan.
    seen_hardlinks: Set[Tuple[int, int]] = set()

    def _report(exc: OSError) -> None:
        if on_error is not None:
            on_error(exc)

    def walk(path: str) -> Tuple[int, int]:
        """Recurse into ``path``; return (recursive_size, recursive_fcount)."""
        total = 0
        fcount = 0
        try:
            scan_it = os.scandir(path)
        except OSError as exc:
            _report(exc)
            dir_entries.append(Entry(path, 0, 0, True, 0))
            return 0, 0

        with scan_it:
            while True:
                try:
                    entry = next(scan_it)
                except StopIteration:
                    break
                except OSError as exc:
                    _report(exc)
                    continue

                try:
                    st = entry.stat(follow_symlinks=False)
                except OSError as exc:
                    _report(exc)
                    continue

                # Never follow symlinks or junctions: they are reparse points
                # whose target lives elsewhere (and may form cycles).
                if _is_reparse(entry, st):
                    continue

                if statmod.S_ISDIR(st.st_mode):
                    sub_size, sub_fcount = walk(entry.path)
                    total += sub_size
                    fcount += sub_fcount
                    continue

                size = st.st_size
                mtime = int(st.st_mtime)
                if dedupe_hardlinks:
                    # DirEntry.stat() doesn't populate st_nlink/st_ino on
                    # Windows; a full stat (file open) is needed to detect and
                    # de-duplicate hard links.
                    try:
                        fst = os.stat(entry.path, follow_symlinks=False)
                    except OSError as exc:
                        _report(exc)
                        continue
                    size = fst.st_size
                    mtime = int(fst.st_mtime)
                    if fst.st_nlink > 1:
                        key = (fst.st_dev, fst.st_ino)
                        if key in seen_hardlinks:
                            # A real file name, but its bytes are already
                            # counted under its first-seen sibling: tally the
                            # name, add zero bytes.
                            fcount += 1
                            continue
                        seen_hardlinks.add(key)

                total += size
                fcount += 1
                if size >= threshold:
                    file_entries.append(
                        Entry(
                            path=entry.path,
                            size=size,
                            mtime=mtime,
                            is_dir=False,
                            fcount=0,
                        )
                    )

        dir_entries.append(Entry(path, total, 0, True, fcount))
        return total, fcount

    for root in norm_roots:
        if not os.path.isdir(root):
            _report(OSError(f"not a directory: {root}"))
            continue
        walk(root)

    entries: List[Entry] = dir_entries + file_entries
    entries.sort(key=lambda e: e.path)

    return Snapshot(
        roots=norm_roots,
        created=now,
        threshold=threshold,
        entries=entries,
    )
