"""Modulefiles of a recipe's dependencies, for build-recipes/<NAME>/build.sh (see build-common.sh: load_depends).

    python3 -I depmodules.py <recipe.yml> <Products> <outdir>

Prints, one per line:
    module<TAB><NAME>/<VERSION>       each dependency, in the recipe's order

A released dependency has its fixed modulefile in <Products>/modulefiles. A dependency that is built but not
released yet (staging: its version directory exists, without a manifest) gets a modulefile made from this
repository's template and its recipe (the one saved next to it by build.sh, else this repository's), exactly as
`prodman release` would make it, written to <outdir>/<NAME>/<VERSION>; the same is done for its own staging
dependencies. Anything else is an error. Notes go to stderr.
"""

import os
import socket
import sys
import time

RECIPES = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # the recipes directory of this script


def _tool_root():
    """The prod tool these recipes go with: the repository checkout (<repo>/build-recipes/_common), or the tool
    installed in the Products directory (<Products>/build-recipes/_common, tool in <Products>/.prod/current)."""
    base = os.path.dirname(RECIPES)
    for root in (base, os.path.join(base, ".prod", "current")):
        if os.path.isfile(os.path.join(root, "lib", "prod", "recipe.py")):
            return root
    sys.exit(f"{os.path.basename(__file__)}: no prod tool for {RECIPES}: neither a repository checkout nor "
             f"<Products>/build-recipes with the tool installed in <Products>/.prod")


TOOL = _tool_root()
sys.path.insert(0, os.path.join(TOOL, "lib"))

from prod import modulefile, recipe  # noqa: E402

# modulefile templates: <repo>/modulefiles in a checkout, <tool>/share/modulefiles in an installed tool
SOURCES = os.path.join(TOOL, "modulefiles")
if not os.path.isdir(SOURCES):
    SOURCES = os.path.join(TOOL, "share", "modulefiles")


def ensure_module(products, outdir, name, version, seen):
    """Make sure a modulefile for name/version can be loaded; generate it if the version is staging."""
    if (name, version) in seen:
        return
    seen.add((name, version))
    if os.path.isfile(os.path.join(products, "modulefiles", name, version)):
        return
    vdir = os.path.join(products, name, version)
    if not os.path.isdir(vdir) or os.path.exists(vdir + ".manifest"):
        sys.exit(f"depmodules: {name} {version} is neither released (no modulefile in {products}/modulefiles) "
                 f"nor built (no {vdir})")
    for dep, dver in recipe.find(products, RECIPES, name, version).depends:
        ensure_module(products, outdir, dep, dver, seen)
    text = modulefile.make_fixed(SOURCES, RECIPES, products, name, version, tool="build.sh (not released)",
                                 host=socket.gethostname(), time=time.strftime("%Y-%m-%dT%H:%M:%S%z"))
    os.makedirs(os.path.join(outdir, name), exist_ok=True)
    with open(os.path.join(outdir, name, version), "w", encoding="utf-8") as f:
        f.write(text)
    print(f"depmodules: {name} {version} is not released yet; using a modulefile made from the repository's "
          f"template (release them in the same vlist)", file=sys.stderr)


def main():
    if len(sys.argv) != 4:
        sys.exit(__doc__.strip().splitlines()[2].strip())
    path, products, outdir = sys.argv[1:]
    name = os.path.basename(os.path.dirname(os.path.abspath(path)))
    version = os.path.splitext(os.path.basename(path))[0]
    deps = recipe.load(RECIPES, name, version).depends
    seen = set()
    for dep, dver in deps:
        ensure_module(products, outdir, dep, dver, seen)
    for dep, dver in deps:
        print(f"module\t{dep}/{dver}")


if __name__ == "__main__":
    main()
