"""Tests for diff classification and totals."""

from spacedig.diff import Status, diff
from spacedig.snapshot import Entry, Snapshot


def _snap(entries, created=1):
    roots = [entries[0].path]
    return Snapshot(roots=roots, created=created, threshold=0, entries=entries)


def test_grow_shrink_new_removed():
    old = _snap([
        Entry("R", 100, 0, True, 3),
        Entry("R\\keep", 50, 0, True, 1),
        Entry("R\\shrinks", 40, 0, True, 1),
        Entry("R\\gone", 10, 0, True, 1),
    ])
    new = _snap([
        Entry("R", 230, 0, True, 4),
        Entry("R\\keep", 50, 0, True, 1),
        Entry("R\\shrinks", 20, 0, True, 1),
        Entry("R\\added", 160, 0, True, 2),
    ], created=2)

    result = diff(old, new)
    by = {c.path: c for c in result.changes}

    assert by["R\\keep"].status is Status.SAME
    assert by["R\\shrinks"].status is Status.SHRANK
    assert by["R\\shrinks"].delta == -20
    assert by["R\\added"].status is Status.NEW
    assert by["R\\added"].delta == 160
    assert by["R\\gone"].status is Status.REMOVED
    assert by["R\\gone"].delta == -10
    assert by["R"].status is Status.GREW
    assert by["R"].delta == 130


def test_total_delta_matches_root_change():
    old = _snap([Entry("R", 100, 0, True, 1)])
    new = _snap([Entry("R", 175, 0, True, 1)], created=2)
    result = diff(old, new)
    assert result.total_delta == 75


def test_changed_excludes_same():
    old = _snap([Entry("R", 10, 0, True, 1), Entry("R\\a", 10, 0, True, 1)])
    new = _snap([Entry("R", 10, 0, True, 1), Entry("R\\a", 10, 0, True, 1)],
                created=2)
    result = diff(old, new)
    assert result.changed() == []


def test_threshold_mismatch_does_not_invent_file_changes():
    # Old snapshot used a high threshold (file not recorded); new used a low
    # one (file recorded). The file should NOT be reported as NEW because it
    # merely fell below the old threshold.
    old = Snapshot(roots=["R"], created=1, threshold=10_000, entries=[
        Entry("R", 500, 0, True, 1),
    ])
    new = Snapshot(roots=["R"], created=2, threshold=100, entries=[
        Entry("R", 500, 0, True, 1),
        Entry("R\\f.bin", 500, 0, False, 0),  # below old threshold of 10000
    ])
    result = diff(old, new)
    paths = {c.path for c in result.changes}
    assert "R\\f.bin" not in paths
    # A genuinely large new file (above both thresholds) is still reported.
    new.entries.append(Entry("R\\big.bin", 50_000, 0, False, 0))
    new.entries[0] = Entry("R", 50_500, 0, True, 2)
    result2 = diff(old, new)
    assert any(c.path == "R\\big.bin" and c.status is Status.NEW
               for c in result2.changes)


def test_changes_sorted_by_absolute_delta():
    old = _snap([Entry("R", 0, 0, True, 0), Entry("R\\a", 0, 0, True, 0),
                 Entry("R\\b", 0, 0, True, 0)])
    new = _snap([Entry("R", 0, 0, True, 0), Entry("R\\a", 5, 0, True, 0),
                 Entry("R\\b", 100, 0, True, 0)], created=2)
    result = diff(old, new)
    # Largest absolute change first.
    deltas = [abs(c.delta) for c in result.changes]
    assert deltas == sorted(deltas, reverse=True)
