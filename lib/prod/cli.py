"""prodman command line. Commands are implemented step by step."""

import argparse
import json
import os
import subprocess
import sys

from . import config, inventory, release, state
from . import progress as progress_mod
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
        raise UsageError(f"{message}\nrun 'prodman help' for usage")


def _common_options(parser, default):
    parser.add_argument("-q", "--quiet", action="store_true", default=default,
                        help="print only errors (no progress lines)")
    parser.add_argument("--json", action="store_true", default=default,
                        help="machine-readable output (list, verify)")


def _parser():
    p = _Parser(prog="prodman", description="prodmanager management tool")
    p.add_argument("--version", action="store_true", help="print the tool version and exit")
    _common_options(p, False)
    # the same options after the command (prodman list --json); SUPPRESS keeps a value given before it
    common = _Parser(add_help=False)
    _common_options(common, argparse.SUPPRESS)
    sub = p.add_subparsers(dest="command", metavar="<command>", parser_class=_Parser)

    def command(name, **kw):
        return sub.add_parser(name, parents=[common], **kw)

    s = command("sync", help="install vlists from the products server (client)")
    s.add_argument("names", nargs="+", metavar="vlist")
    s.add_argument("--dry-run", action="store_true", help="check and show what would be transferred")

    rs = command("resync", help="sync every vlist on this node again (client)")
    rs.add_argument("--dry-run", action="store_true", help="check and show what would be transferred")

    v = command("verify", help="check installed vlists against their manifests; lock writable files again")
    v.add_argument("names", nargs="*", metavar="vlist")
    v.add_argument("--quick", action="store_true", help="skip content hashes and the shared library check")
    v.add_argument("--remote", action="store_true", help="also compare with the server's vlist (client)")

    command("list", help="show the releases on this node: vlists, released and not released versions").add_argument(
        "--remote", action="store_true", help="also list the vlists on the products server")

    rm = command("remove", help="delete vlists or product versions (NAME/VERSION) from this node")
    rm.add_argument("names", nargs="+", metavar="vlist|NAME/VERSION")
    rm.add_argument("--yes", action="store_true", help="really delete (default: only show)")

    g = command("gc", help="delete versions and modulefiles that no vlist uses")
    g.add_argument("--yes", action="store_true", help="really delete (default: only show)")

    r = command("release", help="release a vlist from its draft (server)")
    r.add_argument("name")
    r.add_argument("--check", action="store_true", help="check and report only; write nothing")

    for name in NOT_YET:
        command(name, help="(not implemented yet)").add_argument("args", nargs="*")
    command("help", help="show this help")
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


def _print_sync(results, out):
    for r in results:
        print(f"sync {r.name}" + (" (dry run)" if r.dry_run else ""), file=out)
        for x in r.versions:
            print(f"  {x.name:<16} {x.version:<24} {x.action}", file=out)
        for w in r.warnings:
            print(f"  warning: {w}", file=out)
        if not r.dry_run:
            print(f"  done in {r.seconds:.1f} s", file=out)


def _print_verify(reports, out):
    for r in reports:
        status = "ok" if r.ok else f"{len(r.errors)} problem(s)"
        print(f"{r.name:<12} {status}", file=out)
        for e in r.errors:
            print(f"  error:   {e}", file=out)
        for w in r.warnings:
            print(f"  warning: {w}", file=out)


def _print_list(lst, out):
    print(f"tool    {lst.tool}", file=out)
    print(f"role    {lst.role}" + (f", server {lst.server}" if lst.server else ""), file=out)
    print("vlists:", file=out)
    if not lst.vlists:
        print("  (none)", file=out)
    for v in lst.vlists:
        print(f"  {v['name']:<12} {v['description']}", file=out)
        if v["last_sync"] or v["last_verify"]:
            print(f"  {'':<12} first sync {v['first_sync'] or '-'}, last sync {v['last_sync'] or '-'}, "
                  f"last verify {v['last_verify'] or '-'}", file=out)
    print("versions:", file=out)
    if not lst.versions:
        print("  (none)", file=out)
    for x in lst.versions:
        used = ", ".join(x["used_by"]) if x["used_by"] else "unused (prodman gc)"
        print(f"  {x['name']:<16} {x['version']:<24} {used}", file=out)
    if lst.not_released:
        print("not released:", file=out)
        for x in lst.not_released:
            print(f"  {x['name']:<16} {x['version']}", file=out)
    if lst.leftovers:
        print("leftovers of interrupted syncs (prodman gc):", file=out)
        for u in lst.leftovers:
            print(f"  {u}", file=out)
    if lst.remote is not None:
        print("on the products server:", file=out)
        for v in lst.remote:
            print(f"  {v['name']}", file=out)


# --------------------------------------------------------------------------- main


def main(argv=None, tool_root=None, products=None):
    """Run prod. products overrides the Products directory derived from tool_root (tests only)."""
    tool_root = tool_root or os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    parser = _parser()
    try:
        ns = parser.parse_args(argv)
        if ns.version:
            print(f"prodman {tool_version(tool_root)}")
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
        progress = progress_mod.Progress(sys.stderr, quiet=ns.quiet)

        if ns.command in ("sync", "resync"):
            results = sync_mod.sync(paths, cfg, getattr(ns, "names", []), tool=tool, dry_run=ns.dry_run,
                                    resync=ns.command == "resync", check_environment=True, progress=progress)
            _print_sync(results, out)
        elif ns.command == "verify":
            reports = verify_mod.verify(paths, cfg, ns.names, quick=ns.quick, remote=ns.remote,
                                        check_environment=True, progress=progress)
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
        elif ns.command == "list":
            lst = inventory.listing(paths, cfg, tool=tool, remote=ns.remote)
            if ns.json:
                print(json.dumps(lst.as_dict(), ensure_ascii=False, indent=2))
            else:
                _print_list(lst, out)
        elif ns.command == "remove":
            files = inventory.remove(paths, cfg, ns.names, tool=tool, yes=ns.yes)
            for f in files:
                print(f"  {os.path.relpath(f, paths.products)}", file=out)
            if ns.yes:
                print(f"removed {', '.join(ns.names)}", file=out)
            else:
                print(f"{len(files)} item(s) would be deleted; run 'prodman remove {' '.join(ns.names)} --yes' "
                      "to delete them", file=out)
        elif ns.command == "gc":
            candidates = inventory.gc(paths, cfg, tool=tool, yes=ns.yes)
            for c in candidates:
                print(f"  {os.path.relpath(c, paths.products)}", file=out)
            if not candidates:
                print("nothing to delete", file=out)
            elif ns.yes:
                print(f"deleted {len(candidates)} item(s)", file=out)
            else:
                print(f"{len(candidates)} item(s) would be deleted; run 'prodman gc --yes' to delete them", file=out)
        elif ns.command == "release":
            rp = release.release(paths, cfg, ns.name, tool_root=tool_root, tool=tool, check=ns.check,
                                 check_environment=True, progress=progress)
            _print_release(rp, ns.check, out)
        return 0
    except ProdError as e:
        print(f"prodman: {e}", file=sys.stderr)
        return e.exit_code
