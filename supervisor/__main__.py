"""Ops Room supervisor — CLI.

    python -m supervisor release create
    python -m supervisor release list
    python -m supervisor promote <stamp>|latest
    python -m supervisor verify <stamp>|latest
    python -m supervisor start [--daemon]
    python -m supervisor stop
    python -m supervisor status
    python -m supervisor rollback
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys

from . import core


def _resolve(stamp: str) -> str:
    if stamp == "latest":
        rels = core.list_releases()
        if not rels:
            sys.exit("no releases yet — run `release create` first")
        return rels[-1]
    return stamp


def cmd_prune(args) -> int:
    r = core.prune_releases(keep=args.keep, dry_run=args.dry_run)
    if not r["removed"]:
        print(f"nothing to prune (keeping {args.keep}, {len(r['kept'])} present)")
    else:
        verb = "would remove" if r["dry_run"] else "removed"
        print(f"{verb} {len(r['removed'])} release(s), {r['freed_bytes'] / 1048576:.1f} MiB:")
        for s_ in r["removed"]:
            print(f"  {s_}")
    if r["protected"]:
        print(f"protected (current/previous): {', '.join(r['protected'])}")
    for name in core.dangling_links():
        print(f"WARNING: var/{name} points at a release that no longer exists")
    return 0


def cmd_release(args) -> int:
    if args.action == "create":
        print(core.create_release())
    elif args.action == "list":
        cur, prev = core.current_release(), core.previous_release()
        rels = core.list_releases()
        if not rels:
            print("no releases")
            return 0
        for r in rels:
            marks = []
            if r == cur:
                marks.append("current")
            if r == prev:
                marks.append("previous")
            print(f"  {r}  {'[' + ','.join(marks) + ']' if marks else ''}")
    return 0


def cmd_promote(args) -> int:
    core.promote(_resolve(args.stamp))
    if core.running_pid():
        print("supervisor is running — restart it to pick up the new release:")
        print("  python -m supervisor stop && python -m supervisor start --daemon")
    return 0


def cmd_verify(args) -> int:
    return 0 if core.verify(_resolve(args.stamp)) else 1


def cmd_rollback(_args) -> int:
    now = core.rollback()
    print(f"current is now {now}")
    if core.running_pid():
        print("restart the supervisor to serve it:")
        print("  python -m supervisor stop && python -m supervisor start --daemon")
    return 0


def cmd_start(args) -> int:
    if core.running_pid():
        sys.exit(f"supervisor already running (pid {core.running_pid()})")
    if not core.CURRENT.is_symlink():
        sys.exit("var/current not set — run `release create` then `promote latest`")

    if args.daemon:
        core.ensure_layout()
        with core.LOGFILE.open("a") as fh:
            subprocess.Popen(
                [sys.executable, "-m", "supervisor", "start"],
                cwd=str(core.REPO),
                stdout=fh, stderr=subprocess.STDOUT,
                start_new_session=True,
                env=dict(os.environ),
            )
        print(f"supervisor started in background; log: {core.LOGFILE}")
        return 0

    return core.Supervisor().run()


def cmd_stop(_args) -> int:
    print("stopped" if core.stop_supervisor() else "not running")
    return 0


def cmd_status(_args) -> int:
    pid = core.running_pid()
    print(f"supervisor : {'running pid ' + str(pid) if pid else 'stopped'}")
    print(f"current    : {core.current_release() or '-'}")
    print(f"previous   : {core.previous_release() or '-'}")
    print(f"releases   : {len(core.list_releases())}")
    for name in core.dangling_links():
        print(f"WARNING    : var/{name} -> missing release")
    print(f"endpoint   : http://{core.HOST}:{core.PORT}")
    print(f"health     : {'ok' if core.healthy() else 'down'}")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(prog="supervisor", description="Ops Room supervisor")
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("release", help="manage release snapshots")
    r.add_argument("action", choices=["create", "list"])
    r.set_defaults(fn=cmd_release)

    pn = sub.add_parser("prune", help="delete old releases (never current/previous)")
    pn.add_argument("--keep", type=int, default=5, help="how many recent releases to keep (default 5)")
    pn.add_argument("--dry-run", action="store_true", help="show what would go, delete nothing")
    pn.set_defaults(fn=cmd_prune)

    pr = sub.add_parser("promote", help="point current at a release")
    pr.add_argument("stamp")
    pr.set_defaults(fn=cmd_promote)

    v = sub.add_parser("verify", help="boot a release on a scratch port and health-check")
    v.add_argument("stamp")
    v.set_defaults(fn=cmd_verify)

    s = sub.add_parser("start", help="run the supervisor")
    s.add_argument("--daemon", action="store_true")
    s.set_defaults(fn=cmd_start)

    sub.add_parser("stop").set_defaults(fn=cmd_stop)
    sub.add_parser("status").set_defaults(fn=cmd_status)
    sub.add_parser("rollback").set_defaults(fn=cmd_rollback)

    args = p.parse_args()
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
