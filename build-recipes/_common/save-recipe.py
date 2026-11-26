#!/usr/bin/python3 -I
"""Keep a recipe next to the version it built, as <Products>/<NAME>/<VERSION>.recipe.yml.

    build-recipes/_common/save-recipe.py build-recipes/<NAME>/<VERSION>.yml [--products_dir DIR]

Every build.sh runs this at the end, after its checks pass. For a version without build.sh (for example a
vendor binary unpacked by hand), run it yourself once the version directory is in place.

`prodman release` uses this copy before the tool's own recipes, so releasing a new version needs no tool upgrade,
and `prodman sync` takes it to the nodes. The copy is the recipe plus

    built: "build-recipes/CUPDAQ/V4.3.2.yml at commit 4b61587c1a2b on cupdev0 at 2026-11-23T10:12:00+0900"
    buildinfo:
      "key": "value"     (what buildinfo.sh next to the recipe prints for the version directory, if there is one)

("with uncommitted changes" is added when build-recipes/<NAME>/ differs from that commit; outside a git checkout the
line names the file.) If buildinfo.sh fails, the recipe is saved without buildinfo and a warning is printed.
Refuses a version that is already released (it has a manifest) or that is not built (no version directory).
"""

import os
import socket
import subprocess
import sys
import time

DEFAULT_PRODUCTS = "/home/cupsoft/Products"
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


sys.path.insert(0, os.path.join(_tool_root(), "lib"))

from prod import buildinfo, recipe  # noqa: E402
from prod.errors import FormatError  # noqa: E402
from prod.yamlutil import load_mapping, quote  # noqa: E402


def die(msg):
    sys.exit(f"save-recipe: {msg}")


def git_record(path):
    """'<path in the repository> at commit <hash>[, with uncommitted changes]', or the plain path."""
    d = os.path.dirname(os.path.abspath(path))

    def git(*args):
        return subprocess.run(["git", "-C", d, *args], capture_output=True, text=True, check=False)

    top = git("rev-parse", "--show-toplevel")
    if top.returncode != 0:
        return f"{os.path.abspath(path)} (not in a git checkout)"
    rel = os.path.relpath(os.path.realpath(path), os.path.realpath(top.stdout.strip()))
    rev = git("rev-parse", "--short=12", "HEAD").stdout.strip() or "(no commit)"
    dirty = git("status", "--porcelain", "--", ".").stdout.strip()  # build-recipes/<NAME>/
    return f"{rel} at commit {rev}" + (f", with uncommitted changes in {os.path.dirname(rel)}/" if dirty else "")


def main(argv):
    args, products = [], DEFAULT_PRODUCTS
    while argv:
        a = argv.pop(0)
        if a == "--products_dir" and argv:
            products = argv.pop(0)
        elif a in ("-h", "--help"):
            print(__doc__.strip())
            return
        elif a.startswith("-"):
            die(f"unknown option {a}")
        else:
            args.append(a)
    if len(args) != 1 or not args[0].endswith(".yml"):
        die("usage: save-recipe.py build-recipes/<NAME>/<VERSION>.yml [--products_dir DIR]")
    path = args[0]
    if path.endswith(recipe.SAVED_SUFFIX):
        die(f"{path} is a saved copy; give the recipe in the repository (build-recipes/<NAME>/<VERSION>.yml)")
    name = os.path.basename(os.path.dirname(os.path.abspath(path)))
    version = os.path.basename(path)[: -len(".yml")]

    try:
        with open(path, "rb") as f:
            data = f.read()
        recipe.loads(data, name, version)
        doc = load_mapping(data, path)
        if "built" in doc or "buildinfo" in doc:
            die(f"{path} already has 'built' or 'buildinfo': give the recipe, not a saved copy")
    except OSError as e:
        die(f"cannot read {path}: {e.strerror}")
    except FormatError as e:
        die(str(e))

    products = products.rstrip("/") or "/"
    vdir = os.path.join(products, name, version)
    if os.path.exists(vdir + ".manifest"):
        die(f"{name} {version} is already released (it has a manifest); its recipe must not change")
    if not os.path.isdir(vdir) or os.path.islink(vdir):
        die(f"no version directory {vdir}: build or unpack the version first")

    built = f"{git_record(path)} on {socket.gethostname().split('.')[0]} at {time.strftime('%Y-%m-%dT%H:%M:%S%z')}"
    text = data.decode("utf-8")
    text = (text if text.endswith("\n") else text + "\n") + f"built: {quote(built)}\n"
    script = os.path.join(os.path.dirname(os.path.abspath(path)), "buildinfo.sh")  # next to the recipe
    if os.path.exists(script):
        try:
            text += buildinfo.yaml_block(buildinfo.extract(script, vdir))
        except FormatError as e:
            print(f"save-recipe: warning: no buildinfo: {e}", file=sys.stderr)
    try:
        recipe.loads(text, name, version)  # the copy must load as the tool will read it
    except FormatError as e:
        die(str(e))

    out = recipe.saved_path(products, name, version)
    tmp = f"{out}.tmp{os.getpid()}"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(text)
        os.chmod(tmp, 0o644)
        os.replace(tmp, out)
    except OSError as e:
        if os.path.exists(tmp):
            os.unlink(tmp)
        die(f"cannot write {out}: {e.strerror}")
    print(f"save-recipe: {out}")
    print(f"save-recipe:   built: {built}")


if __name__ == "__main__":
    main(sys.argv[1:])
