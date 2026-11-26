"""Build recipes `build-recipes/<NAME>/<VERSION>.yml`.

Fields:
  product, version   must match the file location
  depends            [{name, version}]: loaded by build.sh, and checked by `prodman release` within the vlist
  source             {url} to download a source archive, or {url, tag} to check out a git tag
  options            [str]: the configure (cmake or ./configure) options of build.sh
  built, buildinfo   added by save-recipe.py in the saved copy: where and when it was built, and what
                     build-recipes/<NAME>/buildinfo.sh printed for the built version

A recipe travels with the version it built: build-recipes/_common/save-recipe.py (run by build.sh) copies it to
`<Products>/<NAME>/<VERSION>.recipe.yml` with `built` and `buildinfo`. That copy is read first (find), so a new
version needs no new tool, and `prodman sync` takes it to the nodes.
"""

import os
from dataclasses import dataclass, field
from typing import List, Tuple

from .errors import FormatError
from .vlist import PRODUCT_RE, VERSION_RE
from .yamlutil import BAD_CHARS_RE, load_mapping

_KEYS = ("product", "version", "depends", "source", "options", "built", "buildinfo")
SAVED_SUFFIX = ".recipe.yml"


@dataclass
class Recipe:
    product: str
    version: str
    depends: List[Tuple[str, str]] = field(default_factory=list)
    url: str = ""  # source.url: an archive to download, or a git repository when tag is given
    tag: str = ""  # source.tag: the git tag to check out
    options: List[str] = field(default_factory=list)
    built: str = ""  # the saved copy's record: which file, where, when
    saved: bool = False  # read from <Products>/<NAME>/<VERSION>.recipe.yml, not from a build-recipes directory

    def origin(self, tool):
        """Where the recipe was read, for records (modulefile headers)."""
        if self.saved:
            return f"{self.product}/{self.version}{SAVED_SUFFIX}" + (f" ({self.built})" if self.built else "")
        return f"build-recipes/{self.product}/{self.version}.yml"


def path_of(recipes_root, product, version):
    return os.path.join(recipes_root, product, version + ".yml")


def saved_path(products_dir, product, version):
    return os.path.join(products_dir, product, version + SAVED_SUFFIX)


def loads(data, product, version):
    what = f"recipe {product}/{version}"
    doc = load_mapping(data, what)
    unknown = [k for k in doc if k not in _KEYS]
    if unknown:
        raise FormatError(f"{what}: unknown fields: {', '.join(map(str, unknown))}")
    if doc.get("product") != product or doc.get("version") != version:
        raise FormatError(f"{what}: 'product'/'version' do not match the file location")
    deps = doc.get("depends", [])
    if not isinstance(deps, list):
        raise FormatError(f"{what}: 'depends' must be a list")
    result = []
    for i, d in enumerate(deps, 1):
        if not isinstance(d, dict) or set(d) != {"name", "version"}:
            raise FormatError(f"{what}: depends[{i}] must have exactly 'name' and 'version'")
        if not PRODUCT_RE.match(d["name"]) or not VERSION_RE.match(d["version"]):
            raise FormatError(f"{what}: depends[{i}]: invalid product name or version")
        if d["name"] == product:
            raise FormatError(f"{what}: depends on itself")
        if any(n == d["name"] for n, _ in result):
            raise FormatError(f"{what}: depends on {d['name']} twice")
        result.append((d["name"], d["version"]))
    built = doc.get("built", "")
    # it goes into a Tcl comment of the modulefile header: one line, and no backslash (line continuation)
    if not isinstance(built, str) or BAD_CHARS_RE.search(built) or "\\" in built:
        raise FormatError(f"{what}: 'built' must be one line of text without backslashes")
    url, tag = _source(doc.get("source", {}), what)
    options = doc.get("options", [])
    if not isinstance(options, list) or not all(_one_line(o) for o in options):
        raise FormatError(f"{what}: 'options' must be a list of one-line strings")
    info = doc.get("buildinfo", {})
    if not isinstance(info, dict) or not all(_one_line(k) and isinstance(v, str) and not BAD_CHARS_RE.search(v)
                                             for k, v in info.items()):
        raise FormatError(f"{what}: 'buildinfo' must map one-line strings to one-line strings")
    return Recipe(product=product, version=version, depends=result, url=url, tag=tag, options=list(options),
                  built=built)


def _one_line(value):
    return isinstance(value, str) and value != "" and not BAD_CHARS_RE.search(value)


def _source(src, what):
    if not isinstance(src, dict) or not set(src) <= {"url", "tag"}:
        raise FormatError(f"{what}: 'source' must have 'url', and 'tag' for a git repository")
    if not src:
        return "", ""
    if not _one_line(src.get("url")):
        raise FormatError(f"{what}: source needs a 'url'")
    if "tag" in src and not _one_line(src["tag"]):
        raise FormatError(f"{what}: source.tag must be one line of text")
    return src["url"], src.get("tag", "")


def _load_file(path, product, version):
    try:
        with open(path, "rb") as f:
            data = f.read()
    except FileNotFoundError:
        raise FormatError(f"no recipe {path}") from None
    return loads(data, product, version)


def load(recipes_root, product, version):
    """The tool's (or the repository's) recipe."""
    return _load_file(path_of(recipes_root, product, version), product, version)


def find(products_dir, recipes_root, product, version):
    """The recipe of a version: the copy saved next to the version directory if there is one, else the one in
    recipes_root (a build-recipes directory). recipes_root is None for `prodman release`: saved copies only."""
    path = saved_path(products_dir, product, version)
    if os.path.lexists(path):
        try:
            rec = _load_file(path, product, version)
        except FormatError as e:
            raise FormatError(f"{path}: {e}") from None
        rec.saved = True
        return rec
    tool_path = path_of(recipes_root, product, version) if recipes_root else None
    if tool_path and os.path.exists(tool_path):
        return _load_file(tool_path, product, version)
    raise FormatError(f"no recipe: {path} does not exist" + (f", nor {tool_path}" if tool_path else "")
                      + " (build.sh writes it; for a version without build.sh, run "
                        "build-recipes/_common/save-recipe.py)")
