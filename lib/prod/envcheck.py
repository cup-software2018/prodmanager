"""Checks of a node's environment, run where they matter (there is no separate doctor command):

  release   the new vlist loads from its new modulefiles (else no release); missing packages and ldd: warnings
  sync      the synced vlist loads; missing packages and ldd: warnings
  verify    the installed vlists still load (else an error); missing packages and ldd (not with --quick): warnings;
            for the Products owner, the links install.sh makes in $HOME

  packages  the system packages the vlist's products need (rpm -q), from the lists in <Products>/packages. What
            counts is that they are installed, however that happened (postinstall.sh is a convenience).
  ldd       shared libraries that ELF files of the versions need and do not find in the vlist's environment: a
            warning to look at (an optional part may need a library that is not installed). Not listed: a library
            that lies next to the file (bundled by a pip wheel, loaded before it), and Qt plugins and QML modules
            (loaded only on demand; platform plugins are listed).

For tests only: PROD_RPM replaces the rpm command.
"""

import os
import pwd
import re
import stat
import subprocess

from . import packages as packages_mod

LDD_BATCH = 200
MAX_SHOWN = 10

# Qt plugins and QML modules of the Qt copies in pip wheels (PySide6/Qt, PyQt6/Qt6, PyQt5/Qt5) are loaded only when
# a program asks for them, and many need libraries nobody installs (other databases, Wayland compositor, 3D, ...).
# The platform plugins are what every GUI program loads, so they still count.
_QT_ON_DEMAND_RE = re.compile(r"/Qt[56]?/(plugins|qml)/")
_QT_REQUIRED_RE = re.compile(r"/Qt[56]?/plugins/(platforms|xcbglintegrations)/")


def tool_problems(paths, home):
    """For the Products owner: the links install.sh makes in $HOME that are missing or wrong."""
    if pwd.getpwuid(os.stat(paths.products).st_uid).pw_name != pwd.getpwuid(os.getuid()).pw_name:
        return []
    problems = []
    for name, target in (("setup_prod.sh", "share/setup_prod.sh"), ("setup_prod.csh", "share/setup_prod.csh"),
                         (".local/bin/prodman", "bin/prodman")):
        link, want = os.path.join(home, name), os.path.join(paths.current, target)
        if not os.path.islink(link) or os.readlink(link) != want:
            problems.append(f"~/{name} does not point to {want} (run install.sh)")
    return problems


def package_warnings(paths, products):
    """Warnings about the system packages that the products need (and the base group) but are not installed."""
    if not os.path.isdir(paths.packages):
        return [f"no package lists in {paths.packages}, so the system packages were not checked"]
    rpm = os.environ.get("PROD_RPM", "rpm")
    groups = packages_mod.groups_for_products(packages_mod.load_all(paths.packages), products)
    missing = packages_mod.rpm_missing(sorted({p for g in groups for p in g.packages}), rpm=rpm)
    missing_opt = packages_mod.rpm_missing(sorted({p for g in groups for p in g.optional}), rpm=rpm)
    warnings = []
    if missing:
        warnings.append(f"{len(missing)} required system package(s) not installed: {' '.join(missing)} "
                        "(install them, e.g. with <Products>/.prod/current/share/postinstall.sh as root)")
    if missing_opt:
        warnings.append(f"optional system packages not installed: {' '.join(missing_opt)}")
    return warnings


def module_environment(modulepath, name):
    """Environment after `module load vlist/<name>` with MODULEPATH = modulepath (a list) in a clean bash: used
    by release to try new modulefiles before they are written. None and stderr on failure."""
    script = ('. /usr/share/Modules/init/bash && module purge && export MODULEPATH="$1" '
              '&& MODULES_VERBOSITY=concise module load "vlist/$2" && env -0')
    out = subprocess.run(["bash", "--noprofile", "--norc", "-c", script, "_", ":".join(modulepath), name],
                         env={"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8", "HOME": os.environ.get("HOME", "/")},
                         capture_output=True, check=False, stdin=subprocess.DEVNULL)
    return _env_result(out)


def _env_result(out):
    if out.returncode != 0:
        return None, out.stderr.decode("utf-8", "replace").strip()
    result = {}
    for item in out.stdout.split(b"\0"):
        k, sep, v = item.partition(b"=")
        if sep:
            result[k.decode("utf-8", "replace")] = v.decode("utf-8", "replace")
    return result, ""


def vlist_environment(paths, name, home):
    """Environment after `. setup_prod.sh <name>` in a clean, non-interactive bash; None and stderr on failure."""
    setup = os.path.join(paths.current, "share", "setup_prod.sh")
    env = {"HOME": home, "PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"}
    out = subprocess.run(["bash", "--noprofile", "--norc", "-c", f'. "{setup}" "$1" && env -0', "_", name],
                         env=env, capture_output=True, check=False, stdin=subprocess.DEVNULL)
    return _env_result(out)


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


def classify_missing(missing, owner):
    """Split ldd's [(file, missing library)] into (missing [(file, lib)], on-demand Qt files {file}).
    owner: {file: version dir}. A library lying next to the file is not missing at all."""
    real, on_demand = [], set()
    for f, lib in missing:
        if os.path.exists(os.path.join(os.path.dirname(f), lib)):
            continue
        rel = os.path.relpath(f, owner[f])
        if _QT_ON_DEMAND_RE.search("/" + rel) and not _QT_REQUIRED_RE.search("/" + rel):
            on_demand.add(f)
        else:
            real.append((f, lib))
    return real, on_demand


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


def classify_missing(missing, owner):
    """Split ldd's [(file, missing library)] into (missing [(file, lib)], on-demand Qt files {file}).
    owner: {file: version dir}. A library lying next to the file is not missing at all."""
    real, on_demand = [], set()
    for f, lib in missing:
        if os.path.exists(os.path.join(os.path.dirname(f), lib)):
            continue
        rel = os.path.relpath(f, owner[f])
        if _QT_ON_DEMAND_RE.search("/" + rel) and not _QT_REQUIRED_RE.search("/" + rel):
            on_demand.add(f)
        else:
            real.append((f, lib))
    return real, on_demand


def ldd_warnings(products_dir, versions, env, progress=None):
    """Warnings about the shared libraries that ELF files of the versions [(name, version)] do not find in env."""
    files, owner = [], {}
    for pname, pver in versions:
        vdir = os.path.join(products_dir, pname, pver)
        for f in elf_files(vdir):
            files.append(f)
            owner[f] = vdir
    if progress is not None:
        progress.line(f"  ldd: {len(files)} ELF files ...")
    missing = []
    for i in range(0, len(files), LDD_BATCH):
        batch = files[i:i + LDD_BATCH]
        out = subprocess.run(["ldd"] + batch, env=env, capture_output=True, text=True, check=False)
        missing += parse_ldd(out.stdout, batch)
    real, on_demand = classify_missing(missing, owner)
    if not real:
        return []
    shown = "; ".join(f"{os.path.relpath(f, products_dir)}: {lib}" for f, lib in real[:MAX_SHOWN])
    more = f" (and {len(real) - MAX_SHOWN} more)" if len(real) > MAX_SHOWN else ""
    return [f"{len(real)} missing shared libraries in {len(files)} ELF files (check whether they matter): "
            f"{shown}{more}"]


def environment_check(paths, name, versions, *, home, run_ldd, env=None, progress=None):
    """(errors, warnings) for a vlist on this node: it must load (setup_prod.sh, unless env is given); then the
    system packages and, with run_ldd, the shared libraries."""
    errors, warnings = [], []
    if env is None:
        if not os.path.exists(os.path.join(paths.current, "share", "setup_prod.sh")):
            return [], [f"the tool is not installed in {paths.prod}, so the environment was not checked"]
        env, err = vlist_environment(paths, name, home)
        if env is None:
            return [f"'. setup_prod.sh {name}' fails: " + " / ".join(err.splitlines()[-3:])], []
    warnings += package_warnings(paths, {n for n, _ in versions})
    if run_ldd:
        warnings += ldd_warnings(paths.products, versions, env, progress)
    return errors, warnings
