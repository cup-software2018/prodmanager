"""prod command line. Commands are implemented step by step."""

import argparse
import json
import os
import subprocess
import sys

from . import config, inventory, release, state
from . import doctor as doctor_mod
from . import sync as sync_mod
from . import verify as verify_mod
from .errors import CheckFailed, ProdError, UsageError

NOT_YET = ()


def tool_version(tool_root):
    """An installed tool has a VERSION file (written by install.sh); a checkout uses git describe."""
    version_file = os.path.join(tool_root, "VERSION")
    if os.path.isfile(version_file):
        with open(version_file, encoding="utf-8") as f:
            return f.read().strip()
    try:
        out = subprocess.run(
            ["git", "-C", tool_root, "describe", "--tags", "--dirty", "--always"],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
        return f"{out} (checkout)"
    except (OSError, subprocess.CalledProcessError):
        return "unknown (checkout)"


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise UsageError(f"{message}\nrun 'prod help' for usage")


def _parser():
    p = _Parser(prog="prod", description="prodmanager management tool")
    p.add_argument("--version", action="store_true", help="print the tool version and exit")
    p.add_argument("-q", "--quiet", action="store_true", help="print only errors")
    p.add_argument("--json", action="store_true", help="machine-readable output (list, verify)")
    sub = p.add_subparsers(dest="command", metavar="<command>", parser_class=_Parser)

    s = sub.add_parser("sync", help="install vlists from the products server (client)")
    s.add_argument("names", nargs="*", metavar="vlist")
    s.add_argument("--installed", action="store_true", help="sync every vlist already on this node again")
    s.add_argument("--dry-run", action="store_true", help="check and show what would be transferred")
    s.add_argument("--force", action="store_true", help="install a withdrawn vlist anyway")
    s.add_argument("--retrust", action="store_true",
                   help="accept a server vlist that differs from the one first synced")

    v = sub.add_parser("verify", help="check installed vlists against their manifests")
    v.add_argument("names", nargs="*", metavar="vlist")
    v.add_argument("--quick", action="store_true", help="skip content hashes")
    v.add_argument("--fix-perms", action="store_true", help="lock writable files again")
    v.add_argument("--remote", action="store_true", help="also compare with the server's vlist (client)")

    d = sub.add_parser("doctor", help="check the tool, system packages, modules and shared libraries")
    d.add_argument("names", nargs="*", metavar="vlist")
    d.add_argument("--quick", action="store_true", help="skip the ldd check")

    sub.add_parser("list", help="show what is installed on this node").add_argument(
        "--remote", action="store_true", help="also list the vlists on the products server")

    rm = sub.add_parser("remove", help="take vlists off this node (client)")
    rm.add_argument("names", nargs="+", metavar="vlist")

    g = sub.add_parser("gc", help="delete what no installed vlist uses (client)")
    g.add_argument("--yes", action="store_true", help="really delete (default: only show)")

    r = sub.add_parser("release", help="release a vlist from its draft (server)")
    r.add_argument("name")
    r.add_argument("--check", action="store_true", help="check and report only; write nothing")
    r.add_argument("--migrate", action="store_true", help="register the legacy <name>.vlist")
    r.add_argument("--module-rev", action="append", default=[], metavar="NAME/VERSION",
                   help="create a new revision of the fixed modulefile of NAME/VERSION")

    w = sub.add_parser("withdraw", help="withdraw a released vlist (server)")
    w.add_argument("name")
    w.add_argument("--reason", required=True)
    w.add_argument("--replaced-by", metavar="NAME")

    for name in NOT_YET:
        sub.add_parser(name, help="(not implemented yet)").add_argument("args", nargs="*")
    sub.add_parser("help", help="show this help")
    return p


def _context(tool_root, products):
    paths = config.Paths(products or config.products_dir_of(tool_root))
    return paths, config.load(paths)


# --------------------------------------------------------------------------- output


def _print_release(rp, check, out):
    print(f"{'check' if check else 'release'} {rp.name}: {len(rp.products)} products", file=out)
    for pp in rp.products:
        how = "frozen, verified" if pp.status == "frozen" else f"staging -> frozen, {len(pp.entries)} entries"
        mod = pp.module + (" (new)" if pp.module_text is not None else "")
        print(f"  {pp.name:<16} {pp.version:<24} {how:<34} module {mod}", file=out)
        for note in pp.notes:
            print(f"      note: {note}", file=out)
    for w in rp.warnings:
        print(f"  warning: {w}", file=out)
    if check:
        print("check only: nothing was written", file=out)
    else:
        print(f"vlist hash {rp.vlist_hash}", file=out)


def _print_sync(results, out):
    for r in results:
        tags = [t for t, on in (("first sync", r.first_sync), ("re-trusted", r.retrusted),
                                ("WITHDRAWN", r.withdrawn is not None), ("dry run", r.dry_run)) if on]
        print(f"sync {r.name} {r.vlist_hash}" + (f" ({', '.join(tags)})" if tags else ""), file=out)
        for x in r.versions:
            print(f"  {x.name:<16} {x.version:<24} {x.action}", file=out)
        for w in r.warnings:
            print(f"  warning: {w}", file=out)
        if not r.dry_run:
            print(f"  done in {r.seconds:.1f} s", file=out)


def _print_verify(reports, out):
    for r in reports:
        status = "ok" if r.ok else f"{len(r.errors)} problem(s)"
        print(f"{r.name:<12} {status:<14} {r.vlist_hash or ''}", file=out)
        for e in r.errors:
            print(f"  error:   {e}", file=out)
        for w in r.warnings:
            print(f"  warning: {w}", file=out)


def _print_doctor(checks, out):
    marks = {"ok": "ok  ", "warn": "WARN", "fail": "FAIL"}
    for c in checks:
        print(f"{marks[c.level]}  {c.name:<16} {c.summary}", file=out)
        for d in c.details:
            print(f"      {d}", file=out)


def _print_list(lst, out):
    print(f"tool    {lst.tool}", file=out)
    print(f"role    {lst.role}" + (f", server {lst.server}" if lst.server else ""), file=out)
    print("vlists:", file=out)
    if not lst.vlists:
        print("  (none)", file=out)
    for v in lst.vlists:
        flags = []
        if v["trusted"] is False:
            flags.append("HASH DIFFERS FROM FIRST SYNC")
        if v["withdrawn"]:
            flags.append("withdrawn")
        print(f"  {v['name']:<12} {v['hash'][:19]}…  {v['description']}" + (f"  [{', '.join(flags)}]" if flags else ""),
              file=out)
        if v["last_sync"] or v["last_verify"]:
            print(f"  {'':<12} first sync {v['first_sync'] or '-'}, last sync {v['last_sync'] or '-'}, "
                  f"last verify {v['last_verify'] or '-'}", file=out)
    print("versions:", file=out)
    if not lst.versions:
        print("  (none)", file=out)
    for x in lst.versions:
        used = ", ".join(x["used_by"]) if x["used_by"] else "unused (prod gc)"
        print(f"  {x['name']:<16} {x['version']:<24} {used}", file=out)
    if lst.unmanaged:
        print("not managed by prod:", file=out)
        for u in lst.unmanaged:
            print(f"  {u}", file=out)
    if lst.leftovers:
        print("leftovers of interrupted syncs (prod gc):", file=out)
        for u in lst.leftovers:
            print(f"  {u}", file=out)
    print(f"system packages: {'applied, see ' + lst.packages if lst.packages else 'no record'}", file=out)
    if lst.remote is not None:
        print("on the products server:", file=out)
        for v in lst.remote:
            print(f"  {v['name']}" + ("  [withdrawn]" if v["withdrawn"] else ""), file=out)


# --------------------------------------------------------------------------- main


def main(argv=None, tool_root=None, products=None):
    """Run prod. products overrides the Products directory derived from tool_root (tests only)."""
    tool_root = tool_root or os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    parser = _parser()
    try:
        ns = parser.parse_args(argv)
        if ns.version:
            print(f"prod {tool_version(tool_root)}")
            return 0
        if ns.command in (None, "help"):
            parser.print_help()
            return 0
        if ns.command in NOT_YET:
            raise UsageError(f"'{ns.command}' is not implemented yet")
        if os.geteuid() == 0:
            raise UsageError("do not run prod as root")
        tool = tool_version(tool_root)
        paths, cfg = _context(tool_root, products)
        out = open(os.devnull, "w") if ns.quiet else sys.stdout

        if ns.command == "sync":
            results = sync_mod.sync(paths, cfg, ns.names, tool=tool, dry_run=ns.dry_run, force=ns.force,
                                    retrust=ns.retrust, installed=ns.installed)
            _print_sync(results, out)
        elif ns.command == "verify":
            reports = verify_mod.verify(paths, cfg, ns.names, quick=ns.quick, fix_perms=ns.fix_perms,
                                        remote=ns.remote)
            ok = all(r.ok for r in reports)
            state.record(paths, tool, "verify", vlists=[r.name for r in reports],
                         result="ok" if ok else "error", quick=ns.quick)
            if ns.json:
                print(json.dumps([r.as_dict() for r in reports], ensure_ascii=False, indent=2))
            else:
                _print_verify(reports, out)
            if not reports:
                print("no vlists installed", file=out)
            if not ok:
                return CheckFailed.exit_code
        elif ns.command == "doctor":
            checks = doctor_mod.doctor(paths, cfg, ns.names, tool_root=tool_root, tool=tool, run_ldd=not ns.quick)
            failed = [c.name for c in checks if c.level == "fail"]
            state.record(paths, tool, "doctor", result="error" if failed else "ok", failed=failed)
            if ns.json:
                print(json.dumps([c.as_dict() for c in checks], ensure_ascii=False, indent=2))
            else:
                _print_doctor(checks, out)
            if failed:
                return CheckFailed.exit_code
        elif ns.command == "list":
            lst = inventory.listing(paths, cfg, tool=tool, remote=ns.remote)
            if ns.json:
                print(json.dumps(lst.as_dict(), ensure_ascii=False, indent=2))
            else:
                _print_list(lst, out)
        elif ns.command == "remove":
            inventory.remove(paths, cfg, ns.names, tool=tool)
            print(f"removed {', '.join(ns.names)} (prod gc deletes versions no longer used)", file=out)
        elif ns.command == "gc":
            candidates = inventory.gc(paths, cfg, tool=tool, yes=ns.yes)
            for c in candidates:
                print(f"  {os.path.relpath(c, paths.products)}", file=out)
            if not candidates:
                print("nothing to delete", file=out)
            elif ns.yes:
                print(f"deleted {len(candidates)} item(s)", file=out)
            else:
                print(f"{len(candidates)} item(s) would be deleted; run 'prod gc --yes' to delete them", file=out)
        elif ns.command == "release":
            rp = release.release(paths, cfg, ns.name, tool_root=tool_root, tool=tool, check=ns.check,
                                 migrate=ns.migrate, module_revs=ns.module_rev)
            _print_release(rp, ns.check, out)
        elif ns.command == "withdraw":
            release.withdraw(paths, cfg, ns.name, tool=tool, reason=ns.reason, replaced_by=ns.replaced_by)
            print(f"withdrew {ns.name}", file=out)
        return 0
    except ProdError as e:
        print(f"prod: {e}", file=sys.stderr)
        return e.exit_code
