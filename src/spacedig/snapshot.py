"""Compact, compressed snapshot format.

A snapshot is a sorted list of :class:`Entry` records plus a small metadata
header.  On disk it is stored as gzip-compressed text using *front coding*
(a.k.a. incremental / prefix encoding): because entries are sorted by path,
each record only stores how many leading characters it shares with the
previous path plus the differing suffix.  Sorted filesystem paths share long
common prefixes, so this shrinks the payload substantially even before gzip,
and gzip squeezes the rest.

Record line layout (tab separated)::

    <common>\t<kind>\t<size>\t<mtime>\t<fcount>\t<suffix>

* ``common``  number of leading chars shared with the previous path
* ``kind``    ``d`` for directory, ``f`` for file
* ``size``    bytes (recursive total for directories)
* ``mtime``   modification time, integer seconds
* ``fcount``  recursive file count for directories (0 for files)
* ``suffix``  the path characters after the shared prefix (always last so it
              may safely contain tab characters)

``suffix`` is minimally escaped so newlines/carriage returns (legal in NTFS
names) cannot corrupt the line structure.
"""

from __future__ import annotations

import gzip
import json
from dataclasses import dataclass
from typing import Dict, List

FORMAT_VERSION = 1


@dataclass
class Entry:
    """A single scanned path.

    For directories ``size`` is the recursive total of everything beneath it
    and ``fcount`` is the recursive file count.  For files ``size`` is the
    file size and ``fcount`` is 0.
    """

    path: str
    size: int
    mtime: int
    is_dir: bool
    fcount: int = 0


@dataclass
class Snapshot:
    roots: List[str]
    created: int  # unix seconds
    threshold: int  # file-size threshold used when scanning
    entries: List[Entry]

    @property
    def total_size(self) -> int:
        """Sum of the root directories' recursive sizes (no double counting)."""
        roots = set(self.roots)
        return sum(e.size for e in self.entries if e.is_dir and e.path in roots)

    def by_path(self) -> Dict[str, Entry]:
        return {e.path: e for e in self.entries}


# --- suffix escaping -------------------------------------------------------
# Only escape the handful of characters that would break line/field parsing.
# '%' is escaped so the encoding is reversible; it is rare in paths so the
# size cost is negligible.
def _escape(s: str) -> str:
    if "%" in s:
        s = s.replace("%", "%25")
    if "\n" in s:
        s = s.replace("\n", "%0A")
    if "\r" in s:
        s = s.replace("\r", "%0D")
    return s


def _unescape(s: str) -> str:
    if "%" not in s:
        return s
    return s.replace("%0D", "\r").replace("%0A", "\n").replace("%25", "%")


def _common_prefix_len(a: str, b: str) -> int:
    n = min(len(a), len(b))
    i = 0
    while i < n and a[i] == b[i]:
        i += 1
    return i


def dumps(snap: Snapshot) -> bytes:
    """Serialize a snapshot to compressed bytes."""
    header = {
        "version": FORMAT_VERSION,
        "roots": snap.roots,
        "created": snap.created,
        "threshold": snap.threshold,
        "count": len(snap.entries),
    }
    lines = ["#" + json.dumps(header, separators=(",", ":"))]

    # Entries must be sorted for front coding to be effective and for the
    # diff merge to work.
    entries = sorted(snap.entries, key=lambda e: e.path)
    prev = ""
    for e in entries:
        common = _common_prefix_len(prev, e.path)
        suffix = _escape(e.path[common:])
        kind = "d" if e.is_dir else "f"
        lines.append(
            f"{common}\t{kind}\t{e.size}\t{e.mtime}\t{e.fcount}\t{suffix}"
        )
        prev = e.path

    payload = "\n".join(lines).encode("utf-8")
    return gzip.compress(payload, compresslevel=9)


def loads(blob: bytes) -> Snapshot:
    """Deserialize a snapshot from compressed bytes."""
    text = gzip.decompress(blob).decode("utf-8")
    lines = text.split("\n")
    if not lines or not lines[0].startswith("#"):
        raise ValueError("not a spacedig snapshot (missing header)")
    header = json.loads(lines[0][1:])
    if header.get("version") != FORMAT_VERSION:
        raise ValueError(f"unsupported snapshot version: {header.get('version')}")

    entries: List[Entry] = []
    prev = ""
    for line in lines[1:]:
        if not line:
            continue
        common_s, kind, size_s, mtime_s, fcount_s, suffix = line.split("\t", 5)
        common = int(common_s)
        path = prev[:common] + _unescape(suffix)
        entries.append(
            Entry(
                path=path,
                size=int(size_s),
                mtime=int(mtime_s),
                is_dir=(kind == "d"),
                fcount=int(fcount_s),
            )
        )
        prev = path

    return Snapshot(
        roots=list(header.get("roots", [])),
        created=int(header.get("created", 0)),
        threshold=int(header.get("threshold", 0)),
        entries=entries,
    )
