"""Command line interface for iterm-organizer."""

from __future__ import annotations

import argparse
import os
import sys
import time

from . import model, plan as plan_mod
from .config import ConfigError, load_config
from .model import Store


def _load_config(store: Store):
    try:
        return load_config(store.config)
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc


def cmd_list(store: Store) -> int:
    files = store.list_sessions()
    current, note = store.load()
    if note:
        print(f"note: {note}", file=sys.stderr)
    if current:
        print(
            f"current  {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(current.created_at))}"
            f"  {len(current.windows)} window(s)  {model.count_panes(current)} pane(s)"
        )
    if not files:
        print("no archived snapshots")
        return 0
    for path in files:
        snap = store.load_file(path)
        if snap is None:
            print(f"{os.path.basename(path)}  (unreadable)")
            continue
        print(
            f"{os.path.basename(path)}  {len(snap.windows)} window(s)  "
            f"{model.count_panes(snap)} pane(s)"
        )
    return 0


def cmd_dry_run(store: Store, path: str | None) -> int:
    config = _load_config(store)
    if path:
        snap = store.load_file(os.path.expanduser(path))
        note = "" if snap else f"cannot read {path}"
    else:
        snap, note = store.load()
    if snap is None:
        print(note or "no snapshot to restore", file=sys.stderr)
        return 1
    if note:
        print(f"note: {note}", file=sys.stderr)
    print(plan_mod.render_plan(plan_mod.build_plan(snap, config)))
    print()
    print(
        "would prompt at launch: "
        + ("yes" if plan_mod.should_prompt(snap, config) else "no")
    )
    return 0


def cmd_snapshot_now(store: Store) -> int:
    import iterm2

    from . import daemon

    config = _load_config(store)
    status = {"code": 1}

    async def entry(connection):
        snap = await daemon.capture(connection)
        store.save(snap, keep=config.keep_snapshots)
        print(
            f"captured {len(snap.windows)} window(s), "
            f"{model.count_panes(snap)} pane(s) -> {store.current}"
        )
        status["code"] = 0

    iterm2.run_until_complete(entry)
    return status["code"]


def cmd_restore(store: Store, path: str | None) -> int:
    import iterm2

    from . import daemon

    config = _load_config(store)
    if path:
        snap = store.load_file(os.path.expanduser(path))
    else:
        snap, _ = store.load()
    if snap is None:
        print("no snapshot to restore", file=sys.stderr)
        return 1

    restore_plan = plan_mod.build_plan(snap, config)
    if restore_plan.is_empty:
        print("nothing to restore")
        return 0
    status = {"code": 0}

    async def entry(connection):
        errors = await daemon.realize(connection, restore_plan)
        for warning in restore_plan.warnings:
            print(f"WARNING: {warning}", file=sys.stderr)
        for err in errors:
            print(f"ERROR: {err}", file=sys.stderr)
            status["code"] = 1
        if errors:
            print(
                f"restore finished with {len(errors)} error(s); "
                f"attempted {len(restore_plan.windows)} window(s), "
                f"{restore_plan.pane_count} pane(s)"
            )
        else:
            print(
                f"restored {len(restore_plan.windows)} window(s), "
                f"{restore_plan.pane_count} pane(s)"
            )

    iterm2.run_until_complete(entry)
    return status["code"]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="iterm-organizer",
        description="Snapshot and restore iTerm2 sessions across crashes.",
    )
    parser.add_argument("--store", default=None, help="state directory")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--dry-run", action="store_true", help="print the restore plan, change nothing"
    )
    group.add_argument("--list", action="store_true", help="list saved snapshots")
    group.add_argument(
        "--snapshot-now", action="store_true", help="capture one snapshot and exit"
    )
    group.add_argument(
        "--restore",
        nargs="?",
        const="",
        metavar="FILE",
        help="restore now, optionally from a specific snapshot file",
    )
    parser.add_argument(
        "--from", dest="source", default=None, help="snapshot file for --dry-run"
    )
    args = parser.parse_args(argv)

    store = Store(args.store) if args.store else Store()
    if args.list:
        return cmd_list(store)
    if args.dry_run:
        return cmd_dry_run(store, args.source)
    if args.snapshot_now:
        return cmd_snapshot_now(store)
    return cmd_restore(store, args.restore or None)


if __name__ == "__main__":
    raise SystemExit(main())
