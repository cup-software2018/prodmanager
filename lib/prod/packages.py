"""System package lists `<Products>/packages/<group>.yml`.

postinstall.sh (run as root) uses this module through a small command line:

    python3 -I -m prod.packages plan <packages dir> [<group>...]
        prints one line per group: <group>|<repos>|<required packages>|<optional packages>
        (lists are space-separated; a space inside a name, as in "@Development Tools", is written %20;
        "|" rather than TAB because the shell's read merges consecutive TABs and loses empty fields)
"""

import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from typing import List

from .errors import FormatError
from .yamlutil import load_mapping

_KEYS = ("group", "description", "products", "repos", "packages", "optional")
_GROUP_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
_PACKAGE_RE = re.compile(r"^(@[A-Za-z0-9 ._+-]+|[A-Za-z0-9][A-Za-z0-9._+-]*)$")
KNOWN_REPOS = ("crb", "epel")


@dataclass
class Group:
    name: str
    description: str
    products: List[str] = field(default_factory=list)
    repos: List[str] = field(default_factory=list)
    packages: List[str] = field(default_factory=list)
    optional: List[str] = field(default_factory=list)


def _list(doc, key, what, pattern=None):
    value = doc.get(key, [])
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise FormatError(f"{what}: '{key}' must be a list of strings")
    for v in value:
        if pattern is not None and not pattern.match(v):
            raise FormatError(f"{what}: invalid entry '{v}' in '{key}'")
    return value


def loads(data, name):
    what = f"package group {name}"
    doc = load_mapping(data, what)
    unknown = [k for k in doc if k not in _KEYS]
    if unknown:
        raise FormatError(f"{what}: unknown fields: {', '.join(map(str, unknown))}")
    if doc.get("group") != name:
        raise FormatError(f"{what}: 'group' must be '{name}' (the file name)")
    g = Group(name=name, description=str(doc.get("description", "")),
              products=_list(doc, "products", what), repos=_list(doc, "repos", what),
              packages=_list(doc, "packages", what, _PACKAGE_RE), optional=_list(doc, "optional", what, _PACKAGE_RE))
    bad = [r for r in g.repos if r not in KNOWN_REPOS]
    if bad:
        raise FormatError(f"{what}: unknown repositories {', '.join(bad)} (known: {', '.join(KNOWN_REPOS)})")
    if not g.packages and not g.optional:
        raise FormatError(f"{what}: no packages")
    return g


def group_files(pkg_dir):
    return sorted(f for f in os.listdir(pkg_dir) if f.endswith(".yml"))


def load_all(pkg_dir):
    groups = []
    for f in group_files(pkg_dir):
        name = f[:-4]
        if not _GROUP_RE.match(name):
            raise FormatError(f"package group file name '{f}' is invalid")
        with open(os.path.join(pkg_dir, f), "rb") as fh:
            groups.append(loads(fh.read(), name))
    if not any(g.name == "base" for g in groups):
        raise FormatError(f"{pkg_dir}: there is no 'base' group")
    return groups


def groups_for_products(groups, products):
    """'base' plus every group serving one of the products."""
    products = set(products)
    return [g for g in groups if g.name == "base" or products & set(g.products)]


def rpm_missing(names, rpm="rpm"):
    """Package names (not @groups) that `rpm -q` does not find."""
    plain = [n for n in names if not n.startswith("@")]
    if not plain:
        return []
    out = subprocess.run([rpm, "-q", "--qf", "%{NAME}\n"] + plain, capture_output=True, text=True, check=False)
    # For a missing package rpm prints "package X is not installed" on stdout: keep whole-name lines only.
    installed = {line for line in out.stdout.splitlines() if line and " " not in line}
    return [n for n in plain if n not in installed]


# --------------------------------------------------------------------------- command line for postinstall.sh


def _main(argv):
    if len(argv) >= 2 and argv[0] == "plan":
        groups = load_all(argv[1])
        wanted = argv[2:]
        unknown = [w for w in wanted if w not in {g.name for g in groups}]
        if unknown:
            raise FormatError(f"unknown package groups: {', '.join(unknown)}")
        for g in groups:
            if not wanted or g.name in wanted or g.name == "base":
                print("|".join([g.name, " ".join(g.repos), " ".join(p.replace(" ", "%20") for p in g.packages),
                                " ".join(g.optional)]))
        return 0
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    try:
        sys.exit(_main(sys.argv[1:]))
    except FormatError as e:
        print(f"prod.packages: {e}", file=sys.stderr)
        sys.exit(1)
