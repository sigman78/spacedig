"""Tests for the filesystem scanner against a real temp tree."""

import os

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
