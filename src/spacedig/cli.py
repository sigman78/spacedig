"""Command-line interface for spacedig."""

from __future__ import annotations

import argparse
import os
import sys
import time
from typing import List, Optional

from . import __version__, report, schedule, store
from .diff import diff as diff_snapshots
from .scanner import DEFAULT_THRESHOLD, scan
from .snapshot import Snapshot, loads


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
_SIZE_UNITS = {
    "": 1,
    "B": 1,
    "K": 1024,
    "KB": 1024,
    "KIB": 1024,
    "M": 1024**2,
    "MB": 1024**2,
    "MIB": 1024**2,
    "G": 1024**3,
    "GB": 1024**3,
    "GIB": 1024**3,
    "T": 1024**4,
    "TB": 1024**4,
}


def parse_size(text: str) -> int:
    """Parse a size like '50', '50MB', '1.5G' into a byte count."""
    s = text.strip().upper()
    num = s
    unit = ""
    for i, ch in enumerate(s):
        if not (ch.isdigit() or ch == "." ):
            num, unit = s[:i], s[i:]
            break
    unit = unit.strip()
    if unit not in _SIZE_UNITS:
        raise argparse.ArgumentTypeError(f"invalid size: {text!r}")
    try:
        value = float(num)
    except ValueError:
        raise argparse.ArgumentTypeError(f"invalid size: {text!r}")
    return int(value * _SIZE_UNITS[unit])


def _now() -> int:
    return int(time.time())


def _print_scan_errors(errors: List[str], stream) -> None:
    if errors:
        shown = errors[:5]
        for e in shown:
            print(f"  ! {e}", file=stream)
        if len(errors) > len(shown):
            print(f"  ! ... and {len(errors) - len(shown)} more unreadable paths",
                  file=stream)


# --------------------------------------------------------------------------- #
# commands
# --------------------------------------------------------------------------- #
def cmd_scan(args: argparse.Namespace) -> int:
    roots = [os.path.abspath(p) for p in args.paths]
    missing = [r for r in roots if not os.path.isdir(r)]
    if missing:
        for m in missing:
            print(f"error: not a directory: {m}", file=sys.stderr)
        return 2

    # Capture the previous snapshot *before* saving the new one.
    prev_handle = store.latest_for_roots(roots)
    prev_snap: Optional[Snapshot] = prev_handle.load() if prev_handle else None

    errors: List[str] = []
    snap = scan(
        roots,
        threshold=args.threshold,
        now=_now(),
        on_error=lambda exc: errors.append(str(exc)),
    )
    handle = store.save(snap)

    if args.keep is not None:
        store.prune(handle.key, args.keep)

    if not args.quiet:
        print(f"Scanned {len(roots)} root(s): "
              f"{report.humansize(snap.total_size)}, "
              f"{len(snap.entries)} entries recorded")
        print(f"Snapshot saved: {handle.path}")
        _print_scan_errors(errors, sys.stdout)

    if prev_snap is None or args.no_diff:
        if not args.quiet:
            if prev_snap is None:
                print("Baseline snapshot saved — run again later to see changes.")
        return 0

    result = diff_snapshots(prev_snap, snap)
    text = report.render(
        result,
        top=args.top,
        list_limit=args.list_limit,
        min_bytes=args.min_bytes,
        show_lists=not args.no_lists,
    )

    has_regression = result.total_delta > 0 and len(result.changed()) > 0

    if args.log:
        _append_log(args.log, text)

    if args.quiet:
        # In quiet mode (scheduled runs) only surface actual regressions.
        if has_regression:
            print(text)
    else:
        print()
        print(text)
    return 0


def _append_log(path: str, text: str) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(_now()))
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(f"\n===== {stamp} =====\n")
        fh.write(text)
        fh.write("\n")


def _load_snapshot_file(path: str) -> Snapshot:
    with open(path, "rb") as fh:
        return loads(fh.read())


def cmd_diff(args: argparse.Namespace) -> int:
    if args.from_file or args.to_file:
        if not (args.from_file and args.to_file):
            print("error: --from and --to must be used together", file=sys.stderr)
            return 2
        old = _load_snapshot_file(args.from_file)
        new = _load_snapshot_file(args.to_file)
    else:
        if args.paths:
            roots = [os.path.abspath(p) for p in args.paths]
            items = store.list_for_key(store.key_for_roots(roots))
        elif args.key:
            items = store.list_for_key(args.key)
        else:
            print("error: provide paths, --key, or --from/--to", file=sys.stderr)
            return 2
        if len(items) < 2:
            print("error: need at least two snapshots to diff "
                  f"(found {len(items)})", file=sys.stderr)
            return 1
        old = items[-2].load()
        new = items[-1].load()

    result = diff_snapshots(old, new)
    text = report.render(
        result,
        top=args.top,
        list_limit=args.list_limit,
        min_bytes=args.min_bytes,
        show_lists=not args.no_lists,
    )
    print(text)
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    groups = store.list_groups()
    if not groups:
        print("No snapshots yet. Run: spacedig scan <path>")
        return 0
    for g in groups:
        roots = ", ".join(g.roots) if g.roots else "(unknown roots)"
        size_on_disk = sum(
            os.path.getsize(s.path) for s in g.snapshots if os.path.exists(s.path)
        )
        print(f"[{g.key}] {roots}")
        print(f"    snapshots: {len(g.snapshots)}   "
              f"store size: {report.humansize(size_on_disk)}")
        if g.snapshots:
            latest = g.snapshots[-1]
            stamp = time.strftime(
                "%Y-%m-%d %H:%M", time.localtime(latest.timestamp)
            )
            print(f"    latest:    {stamp}")
    return 0


def cmd_prune(args: argparse.Namespace) -> int:
    if args.all:
        groups = store.list_groups()
        keys = [g.key for g in groups]
    elif args.key:
        keys = [args.key]
    elif args.paths:
        keys = [store.key_for_roots([os.path.abspath(p) for p in args.paths])]
    else:
        print("error: provide paths, --key, or --all", file=sys.stderr)
        return 2

    total = 0
    for key in keys:
        removed = store.prune(key, args.keep)
        total += len(removed)
    print(f"Pruned {total} snapshot(s), keeping {args.keep} per group.")
    return 0


def cmd_schedule(args: argparse.Namespace) -> int:
    if args.schedule_cmd == "install":
        roots = [os.path.abspath(p) for p in args.paths]
        try:
            command = schedule.install(
                roots,
                interval=args.interval,
                at=args.time,
                log_path=args.log,
                threshold=args.threshold,
            )
        except NotImplementedError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 3
        except Exception as exc:  # subprocess errors etc.
            print(f"error: failed to create scheduled task: {exc}", file=sys.stderr)
            return 1
        log_path = args.log or schedule.default_log_path()
        print(f"Scheduled task '{schedule.TASK_NAME}' installed ({args.interval} "
              f"at {args.time}).")
        print(f"  command: {command}")
        print(f"  log:     {log_path}")
        return 0

    if args.schedule_cmd == "remove":
        try:
            schedule.remove()
        except NotImplementedError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 3
        except Exception as exc:
            print(f"error: failed to remove task: {exc}", file=sys.stderr)
            return 1
        print(f"Scheduled task '{schedule.TASK_NAME}' removed.")
        return 0

    if args.schedule_cmd == "status":
        try:
            text = schedule.status()
        except NotImplementedError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 3
        if text is None:
            print(f"No scheduled task named '{schedule.TASK_NAME}'.")
            return 1
        print(text)
        return 0

    print("error: unknown schedule subcommand", file=sys.stderr)
    return 2


# --------------------------------------------------------------------------- #
# parser
# --------------------------------------------------------------------------- #
def _add_report_opts(p: argparse.ArgumentParser) -> None:
    p.add_argument("--top", type=int, default=20,
                   help="max directories to show in the tree map (default 20)")
    p.add_argument("--list-limit", type=int, default=10,
                   help="max items per summary list (default 10)")
    p.add_argument("--min-bytes", type=parse_size, default=1,
                   help="ignore changes smaller than this (e.g. 1MB)")
    p.add_argument("--no-lists", action="store_true",
                   help="show only the tree map, omit the summary lists")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="spacedig",
        description="Scan disk locations and report what's eating your space "
                    "by diffing compact snapshots over time.",
    )
    parser.add_argument("--version", action="version",
                        version=f"spacedig {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    # scan
    sp = sub.add_parser("scan", help="scan paths, save a snapshot, show changes")
    sp.add_argument("paths", nargs="+", help="directories to scan")
    sp.add_argument("--threshold", type=parse_size, default=DEFAULT_THRESHOLD,
                    help="record individual files at least this big "
                         "(default 50MB)")
    sp.add_argument("--no-diff", action="store_true",
                    help="just save a snapshot, don't show a diff")
    sp.add_argument("--keep", type=int, default=None,
                    help="after saving, keep only the newest N snapshots")
    sp.add_argument("--quiet", action="store_true",
                    help="suppress output unless a regression is found "
                         "(for scheduled runs)")
    sp.add_argument("--log", default=None,
                    help="append the report to this log file")
    _add_report_opts(sp)
    sp.set_defaults(func=cmd_scan)

    # diff
    dp = sub.add_parser("diff", help="show the diff between the two latest "
                                     "snapshots of a target")
    dp.add_argument("paths", nargs="*", help="directories whose history to diff")
    dp.add_argument("--key", help="snapshot group key (see `spacedig list`)")
    dp.add_argument("--from", dest="from_file",
                    help="diff this snapshot file as the 'old' side")
    dp.add_argument("--to", dest="to_file",
                    help="diff this snapshot file as the 'new' side")
    _add_report_opts(dp)
    dp.set_defaults(func=cmd_diff)

    # list
    lp = sub.add_parser("list", help="list stored snapshot groups")
    lp.set_defaults(func=cmd_list)

    # prune
    pp = sub.add_parser("prune", help="delete old snapshots")
    pp.add_argument("paths", nargs="*", help="target directories to prune")
    pp.add_argument("--key", help="snapshot group key")
    pp.add_argument("--all", action="store_true", help="prune every group")
    pp.add_argument("--keep", type=int, default=10,
                    help="snapshots to keep per group (default 10)")
    pp.set_defaults(func=cmd_prune)

    # schedule
    scp = sub.add_parser("schedule",
                         help="manage a Windows scheduled scan task")
    ssub = scp.add_subparsers(dest="schedule_cmd", required=True)

    si = ssub.add_parser("install", help="create/replace the scheduled task")
    si.add_argument("paths", nargs="+", help="directories to scan on schedule")
    si.add_argument("--interval", default="DAILY",
                    choices=["MINUTE", "HOURLY", "DAILY", "WEEKLY", "MONTHLY",
                             "ONLOGON"],
                    help="schtasks schedule type (default DAILY)")
    si.add_argument("--time", default="09:00",
                    help="start time HH:MM (default 09:00)")
    si.add_argument("--threshold", type=parse_size, default=None,
                    help="large-file threshold for scheduled scans")
    si.add_argument("--log", default=None, help="log file for regressions")

    ssub.add_parser("remove", help="delete the scheduled task")
    ssub.add_parser("status", help="show the scheduled task status")
    scp.set_defaults(func=cmd_schedule)

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    # Ensure UTF-8 output so the tree map's box characters render on Windows.
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8")
            except (ValueError, OSError):
                pass

    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
