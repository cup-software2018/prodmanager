#!/usr/bin/python3 -I
"""Compare the legacy environment with the new modulefiles on real products. Read-only.

    /usr/bin/python3 -I tests/realdata/env_equivalence.py [--products DIR] [--legacy-setup FILE]
                                                          [--buildinfo] [vlist ...]

For each legacy vlist (default: every <Products>/vlist/*.vlist):
  legacy  a clean bash sources the legacy setup_prod.sh <vlist> (default ~/setup_prod.sh)
  new     fixed modulefiles are generated from this checkout's templates and recipes into a temporary
          directory (never under Products), plus a bundle for the vlist, and loaded with `module load`
The two environments are compared variable by variable; path lists entry by entry. Each difference is
either explained by a rule below or reported as UNEXPECTED (exit code 1).

--buildinfo also runs each product's buildinfo.sh once and shows the first lines (it writes nothing:
Python runs with -B and PYTHONDONTWRITEBYTECODE).
"""

import argparse
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(REPO, "lib"))

from prod import buildinfo, modulefile, recipe  # noqa: E402
from prod import vlist as vlist_mod  # noqa: E402

PATH_VARS = {"PATH", "LD_LIBRARY_PATH", "PYTHONPATH", "CMAKE_PREFIX_PATH", "MANPATH", "JUPYTER_PATH",
             "ROOT_INCLUDE_PATH"}
# Bookkeeping of the shell and of environment-modules, not product environment.
IGNORE = {"_", "PWD", "OLDPWD", "SHLVL", "PRODUCTS_DIR", "PROD_VLIST", "LOADEDMODULES", "_LMFILES_",
          "MODULEPATH", "MODULEPATH_modshare", "MODULESHOME", "MODULES_CMD", "ENV", "BASH_ENV", "MANPATH_modshare"}
IGNORE_PREFIXES = ("__MODULES_", "BASH_FUNC_", "MODULES_")
SYSTEM_ENTRIES = {"/usr/lib64", "/usr/lib", "/lib64", "/usr/share/man", "/usr/local/share/man", "/usr/bin", "/bin"}
DROPPED_VARS = {"DYLD_LIBRARY_PATH", "LIBPATH", "SHLIB_PATH"}  # macOS/AIX/HP-UX, dropped on purpose
NEW_HOME_VARS = {"CAENHVWRAPPER_HOME"}  # home variables the legacy setup did not set


def clean_env(home):
    return {"HOME": home, "PATH": "/usr/bin:/bin", "LANG": "C.UTF-8", "USER": os.environ.get("USER", "")}


def capture(script, home):
    out = subprocess.run(["bash", "--noprofile", "--norc", "-c", script + "\nenv -0"], env=clean_env(home),
                         capture_output=True, check=False, stdin=subprocess.DEVNULL, cwd="/")
    env = {}
    for item in out.stdout.split(b"\0"):
        k, sep, v = item.partition(b"=")
        if sep:
            env[k.decode()] = v.decode("utf-8", "replace")
    return out.returncode, env, out.stderr.decode("utf-8", "replace")


def relevant(name):
    return name not in IGNORE and not name.startswith(IGNORE_PREFIXES)


def entries(value):
    return value.split(":") if value is not None else []


def compare(legacy, new, legacy_base, new_base):
    """[(ok, message)] for every variable whose value differs."""
    result = []
    for var in sorted(set(legacy) | set(new)):
        if not relevant(var):
            continue
        a, b = legacy.get(var), new.get(var)
        if a == b:
            continue
        if var in DROPPED_VARS and b is None:
            result.append((True, f"{var}: dropped on purpose (not used on Linux)"))
            continue
        if var in NEW_HOME_VARS and a is None:
            result.append((True, f"{var}: new home variable = {b}"))
            continue
        if var in PATH_VARS:
            base_a, base_b = entries(legacy_base.get(var)), entries(new_base.get(var))
            ea = [e for e in entries(a) if e not in base_a]
            eb = [e for e in entries(b) if e not in base_b]
            only_a = [e for e in ea if e not in eb]
            only_b = [e for e in eb if e not in ea]
            legacy_path = entries(legacy.get("PATH"))
            hdf5_bug = var == "CMAKE_PREFIX_PATH" and any(e in legacy_path for e in entries(a) if e)
            for e in only_a:
                if var == "MANPATH" and any(os.path.normpath(os.path.join(d, "..", sub)) == e
                                            for d in legacy_path for sub in ("share/man", "man")):
                    result.append((True, f"{var}: {e} came from `manpath` (derived from PATH) in legacy; "
                                         f"new keeps a trailing ':' so man derives it the same way"))
                elif e == "":
                    result.append((True, f"{var}: empty entry removed (legacy appended ':' to an empty variable)"))
                elif e in SYSTEM_ENTRIES:
                    result.append((True, f"{var}: system entry {e} removed"))
                elif var == "CMAKE_PREFIX_PATH" and e in entries(legacy.get("PATH")):
                    result.append((True, f"{var}: {e} removed (legacy HDF5 put $PATH there by mistake)"))
                else:
                    result.append((False, f"{var}: legacy has {e}, new does not"))
            for e in only_b:
                if var == "MANPATH" and e == "":
                    result.append((True, f"{var}: trailing empty element (man adds its default path there)"))
                elif hdf5_bug:
                    result.append((True, f"{var}: new has {e}; legacy lost it when setup_HDF5.sh overwrote "
                                         f"CMAKE_PREFIX_PATH with $PATH"))
                else:
                    result.append((False, f"{var}: new has {e}, legacy does not"))
            if not only_a and not only_b and [e for e in ea if e] != [e for e in eb if e]:
                result.append((False, f"{var}: same entries, different order\n      legacy {ea}\n      new    {eb}"))
            continue
        if a is None:
            result.append((False, f"{var}: only new = {b}"))
        elif b is None:
            result.append((False, f"{var}: only legacy = {a}"))
        else:
            result.append((False, f"{var}: legacy {a!r}, new {b!r}"))
    return result


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--products", default=os.path.join(os.environ["HOME"], "Products"))
    ap.add_argument("--legacy-setup", default=os.path.join(os.environ["HOME"], "setup_prod.sh"))
    ap.add_argument("--buildinfo", action="store_true")
    ap.add_argument("names", nargs="*")
    ns = ap.parse_args()
    products = ns.products.rstrip("/")
    home = os.environ["HOME"]
    names = ns.names or sorted(f[:-6] for f in os.listdir(os.path.join(products, "vlist")) if f.endswith(".vlist"))
    src_root, recipes_root = os.path.join(REPO, "modulefiles"), os.path.join(REPO, "recipes")
    tmp = tempfile.mkdtemp(prefix="prod-envcheck-")
    unexpected = 0
    try:
        mdir = os.path.join(tmp, "modulefiles")
        made = {}
        for name in names:
            with open(os.path.join(products, "vlist", name + ".vlist"), encoding="utf-8", errors="replace") as f:
                pairs = vlist_mod.parse_legacy(f.read())
            print(f"=== vlist {name}: {' '.join(f'{p} {v}' for p, v in pairs)}")
            for p, v in pairs:
                if (p, v) in made:
                    continue
                text = modulefile.make_fixed(src_root, recipes_root, products, p, v,
                                             tool="envcheck", host="local", time="now")
                path = os.path.join(mdir, p, v)
                os.makedirs(os.path.dirname(path), exist_ok=True)
                with open(path, "w") as f:
                    f.write(text)
                made[(p, v)] = path
            bundle = modulefile.make_bundle(name, "envcheck", [f"{p}/{v}" for p, v in pairs],
                                            tool="envcheck", host="local", time="now")
            os.makedirs(os.path.join(mdir, "vlist"), exist_ok=True)
            with open(os.path.join(mdir, "vlist", name), "w") as f:
                f.write(bundle)

            _, legacy_base, _ = capture("true", home)
            rc_a, legacy, err_a = capture(f'. "{ns.legacy_setup}" "{name}" >/dev/null 2>&1', home)
            _, new_base, _ = capture(". /usr/share/Modules/init/bash", home)
            rc_b, new, err_b = capture(f'. /usr/share/Modules/init/bash\nmodule use "{mdir}"\n'
                                       f'MODULES_VERBOSITY=concise module load "vlist/{name}" || exit 1', home)
            if rc_b != 0:
                print(f"  UNEXPECTED: module load vlist/{name} failed:\n{err_b}")
                unexpected += 1
                continue
            diffs = compare(legacy, new, legacy_base, new_base)
            same = len([v for v in set(legacy) & set(new) if relevant(v) and legacy[v] == new[v]])
            print(f"  {same} variables identical, {len(diffs)} differences")
            for ok, msg in diffs:
                print(f"  {'expected  ' if ok else 'UNEXPECTED'} {msg}")
                unexpected += 0 if ok else 1

        if ns.buildinfo:
            print("=== buildinfo.sh (first lines)")
            for (p, v) in sorted(made):
                script = buildinfo.script_path(recipes_root, p)
                if not script:
                    print(f"  {p} {v}: no buildinfo.sh")
                    continue
                info = buildinfo.extract(script, os.path.join(products, p, v))
                print(f"  {p} {v}: {len(info)} entries")
                for k, val in info[:4]:
                    print(f"      {k} = {val[:100]}")
    finally:
        shutil.rmtree(tmp)
    print(f"\n{'no unexpected differences' if not unexpected else f'{unexpected} UNEXPECTED difference(s)'}")
    return 1 if unexpected else 0


if __name__ == "__main__":
    sys.exit(main())
