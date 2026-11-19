"""`prod doctor`: read-only health checks of a node, no root needed.

  tool       the installed tool, prod.conf, and (for the owner) the links in $HOME
  server     a client can read the products server
  packages   the system packages needed by the installed vlists (rpm -q)
  record     whether postinstall.sh has applied the package lists of this tool version
  modules    `. setup_prod.sh <vlist>` loads the vlist in a clean shell
  ldd        no ELF file of the vlist's versions misses a shared library, inside that environment

For tests only: PROD_RPM replaces the rpm command.
"""

import os
import pwd
import stat
import subprocess
from dataclasses import dataclass, field
from typing import List

from . import packages as packages_mod
from . import transfer
from . import vlist as vlist_mod
from .errors import EnvError, FormatError
from .verify import installed_vlists

LDD_BATCH = 200
MAX_SHOWN = 10


@dataclass
class Check:
    level: str  # 'ok', 'warn', 'fail'
    name: str
    summary: str
    details: List[str] = field(default_factory=list)

    def as_dict(self):
        return dict(self.__dict__)


def _packages_dir(tool_root):
    for d in (os.path.join(tool_root, "share", "packages"), os.path.join(tool_root, "packages")):
        if os.path.isdir(d):
            return d
    raise FormatError(f"no packages/ under {tool_root}")


def check_tool(paths, cfg, tool, home):
    details = []
    level = "ok"
    if not os.path.exists(paths.current):
        return Check("fail", "tool", f"{paths.current} is missing; run install.sh")
    owner = pwd.getpwuid(os.stat(paths.products).st_uid).pw_name
    me = pwd.getpwuid(os.getuid()).pw_name
    # install.sh links $HOME only for a real node (Products is ~/Products) or with --link-home
    home_prod = os.path.join(home, ".local", "bin", "prod")
    linked_here = (os.path.normpath(paths.products) == os.path.join(home, "Products")
                   or (os.path.islink(home_prod) and os.readlink(home_prod).startswith(paths.prod + os.sep)))
    if me == owner and not linked_here:
        details.append(f"nothing in {home} points here ({paths.products} is not ~/Products: a test install); "
                       f"the tool is {os.path.join(paths.current, 'bin', 'prod')}")
    elif me == owner:
        links = [(".local/bin/prod", "bin/prod")]
        legacy = os.path.join(home, "setup_prod.sh")
        if os.path.exists(legacy) and not os.path.islink(legacy):
            # install.sh --keep-legacy-setup: the legacy setup script is kept on purpose (transition)
            details.append(f"~/setup_prod.sh is the legacy one (kept on purpose); the new one is "
                           f"{os.path.join(paths.current, 'share', 'setup_prod.sh')}")
        else:
            links = [("setup_prod.sh", "share/setup_prod.sh"), ("setup_prod.csh", "share/setup_prod.csh")] + links
        for name, target in links:
            link = os.path.join(home, name)
            want = os.path.join(paths.current, target)
            if not os.path.islink(link) or os.readlink(link) != want:
                level = "warn"
                details.append(f"~/{name} does not point to {want} (run install.sh)")
    server = f", server {cfg.server.spec}" if cfg.server else ""
    return Check(level, "tool", f"prod {tool}, role {cfg.role}{server}", details)


def check_server(cfg):
    if cfg.role != "client":
        return None
    try:
        names = transfer.list_dir(cfg, "vlist")
    except EnvError as e:
        return Check("fail", "server", "cannot read the products server", [str(e)])
    released = [n for n in names if n.endswith(vlist_mod.SUFFIX)]
    return Check("ok", "server", f"reachable, {len(released)} released vlists")


def _vlist_products(paths, names):
    products, versions = set(), []
    for name in names:
        try:
            v = vlist_mod.load(paths.vlist_file(name))
        except (FormatError, FileNotFoundError):
            continue
        for p in v.products:
            products.add(p.name)
            versions.append((p.name, p.version))
    return products, versions


def check_packages(pkg_dir, products, rpm):
    groups = packages_mod.groups_for_products(packages_mod.load_all(pkg_dir), products)
    required = sorted({p for g in groups for p in g.packages})
    optional = sorted({p for g in groups for p in g.optional})
    missing = packages_mod.rpm_missing(required, rpm=rpm)
    missing_opt = packages_mod.rpm_missing(optional, rpm=rpm)
    names = ", ".join(g.name for g in groups)
    details = []
    if missing_opt:
        details.append(f"optional, not installed: {' '.join(missing_opt)}")
    if missing:
        return Check("fail", "packages", f"{len(missing)} required package(s) missing (groups: {names})",
                     [f"missing: {' '.join(missing)}", "install them with: sudo <Products>/.prod/current/share/"
                      "postinstall.sh"] + details)
    return Check("ok", "packages", f"{len(required)} required packages installed (groups: {names})", details)


def check_record(paths, pkg_dir):
    try:
        with open(paths.packages_record, "rb") as f:
            rec = packages_mod.record_loads(f.read())
    except FileNotFoundError:
        return Check("warn", "record", "postinstall.sh has not recorded an applied package list on this node")
    except FormatError as e:
        return Check("warn", "record", f"applied record is unreadable: {e}")
    details = []
    level = "ok"
    failed = [g for g, r in (rec.get("groups") or {}).items() if isinstance(r, dict) and r.get("result") != "ok"]
    if failed:
        level = "warn"
        details.append(f"groups that failed when applied: {', '.join(sorted(failed))}")
    if rec.get("packages_hash") != packages_mod.lists_hash(pkg_dir):
        level = "warn"
        details.append("the package lists changed since then; run postinstall.sh again")
    return Check(level, "record", f"applied {rec.get('applied', '?')} by tool {rec.get('tool', '?')}", details)


def vlist_environment(paths, name, home):
    """Environment after `. setup_prod.sh <name>` in a clean, non-interactive bash; None and stderr on failure."""
    setup = os.path.join(paths.current, "share", "setup_prod.sh")
    env = {"HOME": home, "PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"}
    out = subprocess.run(["bash", "--noprofile", "--norc", "-c", f'. "{setup}" "$1" && env -0', "_", name],
                         env=env, capture_output=True, check=False, stdin=subprocess.DEVNULL)
    if out.returncode != 0:
        return None, out.stderr.decode("utf-8", "replace").strip()
    result = {}
    for item in out.stdout.split(b"\0"):
        k, sep, v = item.partition(b"=")
        if sep:
            result[k.decode("utf-8", "replace")] = v.decode("utf-8", "replace")
    return result, ""


def elf_files(root):
    """Regular files under root that start with the ELF magic and are executables or shared objects."""
    found = []
    for dirpath, _, filenames in os.walk(root):
        for n in filenames:
            p = os.path.join(dirpath, n)
            try:
                st = os.lstat(p)
                if not stat.S_ISREG(st.st_mode) or not (st.st_mode & 0o111 or ".so" in n):
                    continue
                with open(p, "rb") as f:
                    if f.read(4) == b"\x7fELF":
                        found.append(p)
            except OSError:
                continue
    return found


def parse_ldd(output, files):
    """[(file, missing library)] from `ldd f1 f2 ...` output. ldd prints a `file:` header before each file's
    libraries only when it is given more than one file."""
    result, current = [], (files[0] if len(files) == 1 else None)
    for line in output.splitlines():
        if line and not line.startswith(("\t", " ")) and line.endswith(":"):
            current = line[:-1]
        elif "=> not found" in line and current:
            result.append((current, line.split("=>")[0].strip()))
    return result


def check_vlist(paths, name, versions, home, run_ldd):
    env, err = vlist_environment(paths, name, home)
    if env is None:
        return [Check("fail", f"modules {name}", f"'. setup_prod.sh {name}' failed", err.splitlines()[-MAX_SHOWN:])]
    checks = [Check("ok", f"modules {name}", f"vlist {name} loads (PROD_VLIST={env.get('PROD_VLIST', '?')})")]
    if not run_ldd:
        return checks
    files = []
    for pname, pver in versions:
        files += elf_files(paths.version_dir(pname, pver))
    missing = []
    for i in range(0, len(files), LDD_BATCH):
        batch = files[i:i + LDD_BATCH]
        out = subprocess.run(["ldd"] + batch, env=env, capture_output=True, text=True, check=False)
        missing += parse_ldd(out.stdout, batch)
    if missing:
        details = [f"{os.path.relpath(f, paths.products)}: {lib}" for f, lib in missing[:MAX_SHOWN]]
        if len(missing) > MAX_SHOWN:
            details.append(f"... and {len(missing) - MAX_SHOWN} more")
        checks.append(Check("fail", f"ldd {name}", f"{len(missing)} missing shared libraries in {len(files)} ELF files",
                            details))
    else:
        checks.append(Check("ok", f"ldd {name}", f"{len(files)} ELF files, no missing shared libraries"))
    return checks


def doctor(paths, cfg, names=None, *, tool_root, tool, run_ldd=True, home=None):
    home = home or os.environ.get("HOME", "")
    rpm = os.environ.get("PROD_RPM", "rpm")
    names = list(names) if names else installed_vlists(paths)
    pkg_dir = _packages_dir(tool_root)
    checks = [check_tool(paths, cfg, tool, home)]
    server = check_server(cfg)
    if server:
        checks.append(server)
    products, _ = _vlist_products(paths, names)
    checks.append(check_packages(pkg_dir, products, rpm))
    checks.append(check_record(paths, pkg_dir))
    if os.path.exists(paths.current):
        for name in names:
            _, versions = _vlist_products(paths, [name])
            checks += check_vlist(paths, name, versions, home, run_ldd)
    return checks
