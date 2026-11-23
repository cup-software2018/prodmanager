"""Build recipes `recipes/<NAME>/<VERSION>.yml` in the git repository.

Only the fields the tool uses are validated (format, product, version, depends, ldd_ignore). Other fields
(source, build, notes, built) are free-form records for people.

A recipe also travels with the version it built: recipes/_common/save-recipe.py (run by build.sh) copies it to
`<Products>/<NAME>/<VERSION>.recipe.yml` with a `built` line (which file, which commit, where, when). That copy is
read first (find), so a new version needs no new tool, and `prod sync` takes it to the nodes.

ldd_ignore lists files of the version (glob patterns relative to the version directory) whose missing shared
libraries `prod doctor` does not count, each with the reason: optional parts that are loaded only on demand.
"""

import fnmatch
import os
import re
from dataclasses import dataclass, field
from typing import List, Tuple

from .errors import FormatError
from .vlist import PRODUCT_RE, VERSION_RE
from .yamlutil import BAD_CHARS_RE, check_format, load_mapping

FORMAT = 1
_KEYS = ("format", "product", "version", "depends", "source", "build", "notes", "ldd_ignore", "built")
SAVED_SUFFIX = ".recipe.yml"

# A suffix of an existing version name that names a dependency:
#   ROOT V6.32.20_3.11.14     -> PYTHON V3.11.14
# (Versions built with Anaconda, such as ROOT V6.32.08_2024.10-1, are not registered: Anaconda is no
# longer used.)
_PYTHON_SUFFIX_RE = re.compile(r"_(\d+\.\d+\.\d+)$")


@dataclass
class Recipe:
    product: str
    version: str
    depends: List[Tuple[str, str]] = field(default_factory=list)
    has_build: bool = False  # True if the version was built from this recipe (it has a 'build' field)
    ldd_ignore: List[Tuple[str, str]] = field(default_factory=list)  # (glob pattern, reason)
    built: str = ""  # the saved copy's record: which file, which commit, where, when
    saved: bool = False  # read from <Products>/<NAME>/<VERSION>.recipe.yml, not from the tool

    def origin(self, tool):
        """Where the recipe was read, for records (buildinfo, modulefile headers)."""
        if self.saved:
            return f"{self.product}/{self.version}{SAVED_SUFFIX}" + (f" ({self.built})" if self.built else "")
        return f"recipes/{self.product}/{self.version}.yml @ {tool}"

    def ldd_ignored(self, rel):
        """The reason if the file `rel` (relative to the version directory) is in ldd_ignore, else None."""
        return next((reason for pat, reason in self.ldd_ignore if fnmatch.fnmatchcase(rel, pat)), None)


def path_of(recipes_root, product, version):
    return os.path.join(recipes_root, product, version + ".yml")


def saved_path(products_dir, product, version):
    return os.path.join(products_dir, product, version + SAVED_SUFFIX)


def loads(data, product, version):
    what = f"recipe {product}/{version}"
    doc = load_mapping(data, what)
    if "format" not in doc:
        raise FormatError(f"{what}: missing field 'format'")
    check_format(doc["format"], FORMAT, what)
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
    return Recipe(product=product, version=version, depends=result, has_build="build" in doc,
                  ldd_ignore=_ldd_ignore(doc.get("ldd_ignore", []), what), built=built)


def _ldd_ignore(items, what):
    if not isinstance(items, list):
        raise FormatError(f"{what}: 'ldd_ignore' must be a list")
    result = []
    for i, d in enumerate(items, 1):
        if not isinstance(d, dict) or set(d) != {"path", "reason"}:
            raise FormatError(f"{what}: ldd_ignore[{i}] must have exactly 'path' and 'reason'")
        pat, reason = d["path"], d["reason"]
        if not isinstance(pat, str) or not pat or pat.startswith("/") or ".." in pat.split("/"):
            raise FormatError(f"{what}: ldd_ignore[{i}]: 'path' must be a pattern relative to the version directory")
        if not isinstance(reason, str) or not reason.strip():
            raise FormatError(f"{what}: ldd_ignore[{i}]: 'reason' must say why")
        result.append((pat, reason))
    return result


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
    """The recipe of a version: the copy saved next to the version directory if there is one, else the tool's.
    recipes_root may be None (no recipes in this tool)."""
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
    raise FormatError(f"no recipe: neither {path}" + (f" nor {tool_path}" if tool_path else "")
                      + " exists (build.sh writes the first; for a version without build.sh, run "
                        "recipes/_common/save-recipe.py)")


def depends_from_suffix(version):
    """Dependencies implied by the suffix of an existing version name (for first registration)."""
    m = _PYTHON_SUFFIX_RE.search(version)
    if m:
        return [("PYTHON", "V" + m.group(1))]
    return []
