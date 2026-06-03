# spacedig

**Find out what's eating your disk space — and what's *growing*.**

`spacedig` scans disk locations, stores a tiny compressed snapshot of the
file/directory metadata, and on the next run shows a simple text map of what
changed: which folders and files grew, shrank, appeared, or vanished. It can
schedule itself on Windows to track disk-space regressions over time.

- **Disk change map** — a tree of where the space actually went, biggest
  changes first.
-  **Compact snapshots** — sorted paths are *front-coded* (prefix-compressed)
  then gzipped, so a snapshot of a huge tree stays small.
- **Scheduling** — register a recurring scan with Windows Task Scheduler and
  log regressions automatically.
- **Zero runtime dependencies** — pure Python standard library; installs in
  seconds with [uv](https://docs.astral.sh/uv/).

---

## Install

### With uv (recommended)

```powershell
# from the project directory
uv tool install .
```

This puts a `spacedig` command on your PATH. Alternatively, run it without
installing:

```powershell
uv run spacedig --help
```

For development (editable install + tests):

```powershell
uv venv
uv pip install -e ".[dev]"
uv run pytest
```

### With pip

```powershell
pip install .
spacedig --help
```

---

## Quick start

```powershell
# 1) First scan — records a baseline snapshot
spacedig scan D:\Projects

# ... time passes, files change ...

# 2) Scan again — saves a new snapshot AND prints what changed
spacedig scan D:\Projects
```

Example output:

```
spacedig — disk change report
  roots:  D:\Projects
  span:   2026-06-01 09:00  ->  2026-06-02 09:00
  total:  12.4 GB  ->  14.1 GB   (Δ +1.7 GB)

Where the space went:
  D:\Projects  +1.7 GB  (+812 files)
  ├─ node_modules  +1.1 GB  grew  (+790 files)
  ├─ build         +640.0 MB  [NEW]
  └─ .cache        -120.0 MB  shrank

Top growers:
       +1.1 GB  D:\Projects\node_modules
     +640.0 MB  D:\Projects\build [NEW]

Top shrinkers:
     -120.0 MB  D:\Projects\.cache
```

You can scan multiple roots at once; their history is tracked together:

```powershell
spacedig scan D:\Projects C:\Users\me\Downloads
```

---

## Commands

| Command | What it does |
|---|---|
| `spacedig scan <paths...>` | Scan paths, save a snapshot, and show the diff vs. the previous snapshot of the same paths. |
| `spacedig diff [paths]` | Show the diff between the two latest snapshots of a target (or use `--from`/`--to` with snapshot files). |
| `spacedig list` | List stored snapshot groups, their roots, count, and on-disk size. |
| `spacedig prune [paths]` | Delete old snapshots, keeping the newest N (`--keep`, default 10). |
| `spacedig schedule install <paths...>` | Register a recurring Windows scan task. |
| `spacedig schedule status` / `remove` | Inspect or delete the scheduled task. |

### Useful flags

- `--threshold 50MB` — record individual files at least this big (directories
  are always aggregated). Smaller threshold = more detail, bigger snapshots.
- `--top 30` — how many directories to show in the tree map.
- `--min-bytes 10MB` — hide changes smaller than this.
- `--no-lists` — show only the tree map.
- `--keep 20` — prune to the newest 20 snapshots right after scanning.
- `--dedupe-hardlinks` — count hard-linked files once (slower; see *Links* below).
- `--quiet` / `--log <file>` — for scheduled runs (see below).

Run `spacedig <command> --help` for the full list.

---

## Scheduling regular scans (Windows)

Register a daily scan that logs disk-space regressions automatically:

```powershell
spacedig schedule install D:\Projects C:\Users\me\Downloads --interval DAILY --time 09:00
```

This creates a Windows Task Scheduler task named `spacedig\scan` that runs:

```
python -m spacedig scan <paths> --quiet --log <home>\regressions.log
```

In `--quiet` mode the task prints nothing unless the total used space *grew*,
and it appends every report to the log file so you build a history of when and
where space regressions happened.

```powershell
spacedig schedule status      # show the task
spacedig schedule remove      # delete it
```

The regression log lives next to the snapshots (see below). Open it any time to
see the timeline of changes.

---

## How it works

### Snapshots

A scan walks each root **bottom-up** and records, for every directory, its
*recursive* size and file count — that's what answers "which folder is eating
space" without storing an entry per file. Individual files are only recorded
when they exceed `--threshold` (default 50 MiB), surfacing the few giant files
that matter while keeping snapshots tiny.

### Links (symlinks, junctions, hard links)

To avoid counting one physical file more than once, spacedig handles Windows
link types deliberately:

- **Symbolic links** and **directory junctions** (any reparse point) are **not
  followed**. This is detected via the reparse attribute bit, so directory
  *junctions* — which `os.walk` would otherwise descend into — are correctly
  skipped, along with the targets of symlinks. This also prevents infinite
  loops from cyclic links. The link itself occupies negligible space and is
  not counted.
- **Hard links** (multiple names for one physical file) are, by default,
  counted under **each** name. Detecting them reliably on Windows requires a
  full `stat()` (an open) of every file — too costly for the tool's main job of
  sweeping large disks — so it is opt-in. Pass **`--dedupe-hardlinks`** to count
  hard-linked bytes only once (matching `du`); both names are still reflected in
  the file count. For most directories (where hard links are rare) the default
  is both fast and accurate.

### Compact representation

Entries are sorted by path and **front-coded**: each record stores only the
number of characters it shares with the previous path plus the differing
suffix. Sorted filesystem paths share long prefixes, so this shrinks the
payload dramatically; the result is then gzipped. Special characters that are
legal in NTFS names (tabs, newlines) are escaped so they can't corrupt the
format — round-tripping is lossless (covered by tests).

### Diff

Two snapshots are matched by path; each entry is classified as
**grew / shrank / new / removed**, sorted by absolute change, and rendered as a
tree (with parent directories stitched in for context) plus top-N lists.

### Where data lives

Snapshots are stored under:

- Windows: `%LOCALAPPDATA%\spacedig\snapshots\`
- Other OSes: `~/.local/share/spacedig/snapshots/`
- Override with the `SPACEDIG_HOME` environment variable.

Each set of roots gets its own subfolder (keyed by a hash of the normalized
roots) so re-scanning the same target appends to its history.

---

## Testing

```powershell
uv run pytest
```

The suite covers snapshot round-tripping (including pathological filenames and
compression), scanner size aggregation, link handling (symlinks, junctions, and
hard links), diff classification, history keying, pruning, and report rendering.

---

## License

MIT
