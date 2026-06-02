"""Tests for storage (history keying) and report rendering."""

import os

import pytest

from spacedig import report, store
from spacedig.diff import diff
from spacedig.scanner import scan
from spacedig.snapshot import Entry, Snapshot


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("SPACEDIG_HOME", str(tmp_path / "home"))


def _write(path, size):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(b"\0" * size)


def test_save_and_history(tmp_path):
    root = tmp_path / "root"
    _write(str(root / "a.txt"), 100)

    snap1 = scan([str(root)], threshold=10**12, now=1000)
    store.save(snap1)
    _write(str(root / "b.txt"), 500)
    snap2 = scan([str(root)], threshold=10**12, now=2000)
    store.save(snap2)

    items = store.list_for_key(store.key_for_roots([str(root)]))
    assert len(items) == 2
    assert items[0].timestamp == 1000
    assert items[1].timestamp == 2000

    latest = store.latest_for_roots([str(root)])
    prev = store.previous_for_roots([str(root)])
    assert latest.timestamp == 2000
    assert prev.timestamp == 1000


def test_key_is_order_independent():
    k1 = store.key_for_roots(["C:\\a", "C:\\b"])
    k2 = store.key_for_roots(["C:\\b", "C:\\a"])
    assert k1 == k2


def test_prune_keeps_newest():
    for ts in (1, 2, 3, 4, 5):
        snap = Snapshot(roots=["C:\\x"], created=ts, threshold=0,
                        entries=[Entry("C:\\x", ts, 0, True, 1)])
        store.save(snap)
    key = store.key_for_roots(["C:\\x"])
    removed = store.prune(key, keep=2)
    assert len(removed) == 3
    remaining = store.list_for_key(key)
    assert [s.timestamp for s in remaining] == [4, 5]


def _diff_result():
    old = Snapshot(roots=["R"], created=1000, threshold=0, entries=[
        Entry("R", 100, 0, True, 2),
        Entry("R\\cache", 60, 0, True, 1),
        Entry("R\\src", 40, 0, True, 1),
    ])
    new = Snapshot(roots=["R"], created=2000, threshold=0, entries=[
        Entry("R", 1060, 0, True, 3),
        Entry("R\\cache", 1000, 0, True, 1),
        Entry("R\\src", 40, 0, True, 1),
        Entry("R\\build", 20, 0, True, 1),
    ])
    return diff(old, new)


def test_report_contains_key_facts():
    text = report.render(_diff_result())
    assert "R\\cache" in text
    assert "grew" in text or "+" in text
    # Net change is +960 bytes.
    assert "960" in text


def test_report_no_changes_message():
    snap = Snapshot(roots=["R"], created=1, threshold=0,
                    entries=[Entry("R", 5, 0, True, 1)])
    text = report.render(diff(snap, Snapshot(
        roots=["R"], created=2, threshold=0,
        entries=[Entry("R", 5, 0, True, 1)])))
    assert "No changes" in text


def test_humansize_signed():
    assert report.humansize(0) == "0 B"
    assert report.humansize(1536, signed=True) == "+1.5 KB"
    assert report.humansize(-1024, signed=True) == "-1.0 KB"
    assert report.humansize(500) == "500 B"
