"""What build.sh needs from a recipe (see build-common.sh: read_recipe).

    python3 -I recipeinfo.py <recipe.yml>

Checks the recipe with the tool's rules and prints, one per line (tab-separated):
    url<TAB><source.url>              empty if the recipe has no source
    tag<TAB><source.tag>              empty for an archive to download
    option<TAB><option>               each entry of options, in order
    depends<TAB><NAME><TAB><VERSION>  each dependency, in order
"""

import os
import sys

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


if len(sys.argv) != 2:
    sys.exit(__doc__.strip().splitlines()[2].strip())
sys.path.insert(0, os.path.join(_tool_root(), "lib"))

from prod import recipe  # noqa: E402
from prod.errors import FormatError  # noqa: E402


def main(path):
    name = os.path.basename(os.path.dirname(os.path.abspath(path)))
    version = os.path.basename(path)[: -len(".yml")]
    try:
        rec = recipe.load(os.path.dirname(os.path.dirname(os.path.abspath(path))), name, version)
    except FormatError as e:
        sys.exit(f"recipeinfo: {e}")
    print(f"url\t{rec.url}")
    print(f"tag\t{rec.tag}")
    for o in rec.options:
        print(f"option\t{o}")
    for dname, dver in rec.depends:
        print(f"depends\t{dname}\t{dver}")


if __name__ == "__main__":
    main(sys.argv[1])
