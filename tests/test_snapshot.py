"""Round-trip and correctness tests for the compact snapshot format."""

from spacedig.snapshot import Entry, Snapshot, dumps, loads


def _sample() -> Snapshot:
    return Snapshot(
        roots=["D:\\data"],
        created=1_700_000_000,
        threshold=50 * 1024 * 1024,
        entries=[
            Entry("D:\\data", 3000, 0, True, 4),
            Entry("D:\\data\\a", 1000, 0, True, 2),
            Entry("D:\\data\\a\\big.bin", 900, 1_699_999_000, False, 0),
            Entry("D:\\data\\b", 2000, 0, True, 2),
        ],
    )


def test_roundtrip_preserves_entries():
    snap = _sample()
    out = loads(dumps(snap))
    assert out.roots == snap.roots
    assert out.created == snap.created
    assert out.threshold == snap.threshold
    got = {(e.path, e.size, e.is_dir, e.fcount) for e in out.entries}
    want = {(e.path, e.size, e.is_dir, e.fcount) for e in snap.entries}
    assert got == want


def test_total_size_only_counts_roots():
    snap = _sample()
    # Only D:\data is a root, so total == its recursive size (3000), not the
    # sum of every directory entry.
    assert snap.total_size == 3000


def test_front_coding_handles_unsorted_input():
    snap = Snapshot(
        roots=["C:\\x"],
        created=1,
        threshold=0,
        entries=[
            Entry("C:\\x\\zeta", 1, 0, False, 0),
            Entry("C:\\x", 10, 0, True, 2),
            Entry("C:\\x\\alpha", 9, 0, False, 0),
        ],
    )
    out = loads(dumps(snap))
    paths = [e.path for e in out.entries]
    assert paths == sorted(paths)
    assert {e.path for e in out.entries} == {e.path for e in snap.entries}


def test_special_characters_in_paths_survive():
    weird = "C:\\x\\we%ird\tname\nwith\rbreaks"
    snap = Snapshot(
        roots=["C:\\x"],
        created=1,
        threshold=0,
        entries=[
            Entry("C:\\x", 5, 0, True, 1),
            Entry(weird, 5, 0, False, 0),
        ],
    )
    out = loads(dumps(snap))
    assert weird in {e.path for e in out.entries}


def test_compression_is_smaller_than_raw_paths():
    # Many sibling directories with a long shared prefix should compress well.
    base = "C:\\Users\\someone\\AppData\\Local\\projects\\repo\\node_modules"
    entries = [Entry(base, 0, 0, True, 0)]
    for i in range(500):
        entries.append(Entry(f"{base}\\package_{i:04d}", i, 0, True, 1))
    snap = Snapshot(roots=[base], created=1, threshold=0, entries=entries)
    blob = dumps(snap)
    raw = sum(len(e.path) for e in entries)
    # front coding + gzip should beat the raw path bytes comfortably.
    assert len(blob) < raw
