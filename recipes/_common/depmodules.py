"""Modulefiles of a recipe's dependencies, for recipes/<NAME>/build.sh (see build-common.sh: load_depends).

    python3 -I depmodules.py <recipe.yml> <Products> <outdir>

Prints, one per line:
    source<TAB><url><TAB><commit>     the recipe's source.url and source.commit (empty if absent)
    module<TAB><NAME>/<VERSION>       each dependency, in the recipe's order

A released dependency has its fixed modulefile in <Products>/modulefiles. A dependency that is built but not
released yet (staging: its version directory exists, without a manifest) gets a modulefile made from this
repository's template, exactly as `prod release` would make it, written to <outdir>/<NAME>/<VERSION>; the same is
done for its own staging dependencies. Anything else is an error. Notes go to stderr.
"""

import os
import socket
import sys
import time

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(REPO, "lib"))

import yaml  # noqa: E402

from prod import modulefile, recipe  # noqa: E402

RECIPES = os.path.join(REPO, "recipes")
SOURCES = os.path.join(REPO, "modulefiles")


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
    for dep, dver in recipe.load(RECIPES, name, version).depends:
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
    with open(path, encoding="utf-8") as f:
        src = yaml.safe_load(f).get("source") or {}
    if not isinstance(src, dict):
        src = {}
    deps = recipe.load(RECIPES, name, version).depends
    seen = set()
    for dep, dver in deps:
        ensure_module(products, outdir, dep, dver, seen)
    print(f"source\t{src.get('url', '')}\t{src.get('commit', '')}")
    for dep, dver in deps:
        print(f"module\t{dep}/{dver}")


if __name__ == "__main__":
    main()
