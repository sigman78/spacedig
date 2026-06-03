"""Tests for the filesystem scanner against a real temp tree."""

import os
import subprocess
import sys

import pytest

from spacedig.scanner import scan


def _write(path, size):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(b"\0" * size)


def test_recursive_directory_sizes(tmp_path):
    root = tmp_path / "root"
    _write(str(root / "a.txt"), 100)
    _write(str(root / "sub" / "b.txt"), 200)
    _write(str(root / "sub" / "deep" / "c.txt"), 300)

    snap = scan([str(root)], threshold=10**12, now=123)
    by = {e.path: e for e in snap.entries}

    rootp = os.path.normpath(str(root))
    subp = os.path.join(rootp, "sub")
    deepp = os.path.join(subp, "deep")

    assert by[rootp].size == 600
    assert by[rootp].fcount == 3
    assert by[subp].size == 500
    assert by[subp].fcount == 2
    assert by[deepp].size == 300
    assert by[deepp].fcount == 1


def test_large_files_recorded_above_threshold(tmp_path):
    root = tmp_path / "root"
    _write(str(root / "small.bin"), 100)
    _write(str(root / "big.bin"), 5000)

    snap = scan([str(root)], threshold=1000, now=1)
    files = {e.path: e for e in snap.entries if not e.is_dir}

    bigp = os.path.join(os.path.normpath(str(root)), "big.bin")
    smallp = os.path.join(os.path.normpath(str(root)), "small.bin")
    assert bigp in files
    assert smallp not in files  # below threshold, not stored individually
    # ...but it still counts toward the directory total.
    rootp = os.path.normpath(str(root))
    dirs = {e.path: e for e in snap.entries if e.is_dir}
    assert dirs[rootp].size == 5100


def test_empty_directory(tmp_path):
    root = tmp_path / "empty"
    root.mkdir()
    snap = scan([str(root)], threshold=0, now=1)
    rootp = os.path.normpath(str(root))
    by = {e.path: e for e in snap.entries}
    assert by[rootp].size == 0
    assert by[rootp].fcount == 0


def test_total_size_is_root_recursive_size(tmp_path):
    root = tmp_path / "root"
    _write(str(root / "a.txt"), 100)
    _write(str(root / "sub" / "b.txt"), 200)
    snap = scan([str(root)], threshold=10**12, now=1)
    assert snap.total_size == 300


# --- link handling -------------------------------------------------------- #
def _make_junction(link: str, target: str) -> bool:
    """Create a Windows directory junction; return False if unsupported."""
    if sys.platform != "win32":
        return False
    try:
        proc = subprocess.run(
            ["cmd", "/c", "mklink", "/J", link, target],
            capture_output=True, text=True,
        )
        return proc.returncode == 0
    except OSError:
        return False


def test_directory_symlink_not_followed(tmp_path):
    outside = tmp_path / "outside"
    _write(str(outside / "big.bin"), 5000)
    root = tmp_path / "root"
    _write(str(root / "a.bin"), 100)
    try:
        os.symlink(str(outside), str(root / "link"), target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("directory symlinks not permitted in this environment")

    snap = scan([str(root)], threshold=10**12, now=1)
    rootp = os.path.normpath(str(root))
    dirs = {e.path: e for e in snap.entries if e.is_dir}
    # The symlinked tree is not descended, so its 5000 bytes are excluded.
    assert dirs[rootp].size == 100
    assert not any("big.bin" in e.path for e in snap.entries)


def test_junction_not_followed(tmp_path):
    outside = tmp_path / "outside"
    _write(str(outside / "big.bin"), 5000)
    root = tmp_path / "root"
    _write(str(root / "a.bin"), 100)
    if not _make_junction(str(root / "junc"), str(outside)):
        pytest.skip("junctions not supported in this environment")

    snap = scan([str(root)], threshold=10**12, now=1)
    rootp = os.path.normpath(str(root))
    dirs = {e.path: e for e in snap.entries if e.is_dir}
    # os.walk would descend into junctions; our scanner must not.
    assert dirs[rootp].size == 100
    assert not any("big.bin" in e.path for e in snap.entries)


def test_file_symlink_not_followed(tmp_path):
    root = tmp_path / "root"
    target = root / "target.bin"
    _write(str(target), 5000)
    try:
        os.symlink(str(target), str(root / "alias.bin"))
    except (OSError, NotImplementedError):
        pytest.skip("file symlinks not permitted in this environment")

    snap = scan([str(root)], threshold=10**12, now=1)
    rootp = os.path.normpath(str(root))
    dirs = {e.path: e for e in snap.entries if e.is_dir}
    # The symlink itself is a reparse point (~0 bytes) and is not followed,
    # so only target.bin's 5000 bytes count.
    assert dirs[rootp].size == 5000


def test_hardlink_double_counted_by_default(tmp_path):
    root = tmp_path / "root"
    original = root / "original.bin"
    _write(str(original), 1000)
    try:
        os.link(str(original), str(root / "hardlink.bin"))
    except (OSError, NotImplementedError, AttributeError):
        pytest.skip("hard links not supported in this environment")

    snap = scan([str(root)], threshold=10**12, now=1)  # default: no dedupe
    rootp = os.path.normpath(str(root))
    dirs = {e.path: e for e in snap.entries if e.is_dir}
    assert dirs[rootp].size == 2000  # documented default behaviour
    assert dirs[rootp].fcount == 2


def test_hardlink_deduped_when_requested(tmp_path):
    root = tmp_path / "root"
    original = root / "original.bin"
    _write(str(original), 1000)
    try:
        os.link(str(original), str(root / "hardlink.bin"))
    except (OSError, NotImplementedError, AttributeError):
        pytest.skip("hard links not supported in this environment")

    snap = scan([str(root)], threshold=10**12, now=1, dedupe_hardlinks=True)
    rootp = os.path.normpath(str(root))
    dirs = {e.path: e for e in snap.entries if e.is_dir}
    # Physical bytes counted once; both names still tallied in the file count.
    assert dirs[rootp].size == 1000
    assert dirs[rootp].fcount == 2
