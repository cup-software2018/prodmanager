"""System package lists `packages/<group>.yml` and the applied record.

postinstall.sh (run as root) uses this module through a small command line:

    python3 -I -m prod.packages plan <packages dir> [<group>...]
        prints one line per group: <group>|<repos>|<required packages>|<optional packages>
        (lists are space-separated; a space inside a name, as in "@Development Tools", is written %20;
        "|" rather than TAB because the shell's read merges consecutive TABs and loses empty fields)
    python3 -I -m prod.packages record <packages dir> <record file> <tool version> <results file>
        writes the applied record from a results file with lines: <group> TAB <ok|failed> TAB <failed> TAB <optional failed>
"""

import hashlib
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from typing import List

from .errors import FormatError
from .yamlutil import check_format, load_mapping, quote

FORMAT = 1
RECORD_FORMAT = 1
_KEYS = ("format", "group", "description", "products", "repos", "packages", "optional")
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
    if "format" not in doc:
        raise FormatError(f"{what}: missing field 'format'")
    check_format(doc["format"], FORMAT, what)
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


def lists_hash(pkg_dir):
    """One hash over all group files (name and bytes), to tell whether a node has applied this version."""
    h = hashlib.sha256()
    for f in group_files(pkg_dir):
        with open(os.path.join(pkg_dir, f), "rb") as fh:
            data = fh.read()
        h.update(f.encode() + b"\0" + str(len(data)).encode() + b"\0" + data)
    return "sha256:" + h.hexdigest()


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


# --------------------------------------------------------------------------- applied record


def record_dumps(applied, tool, pkg_hash, results):
    """results: {group: {"result": "ok"|"failed", "failed": [...], "optional_failed": [...]}}"""
    lines = [f"format: {quote(str(RECORD_FORMAT))}", f"applied: {quote(applied)}", f"tool: {quote(tool)}",
             f"packages_hash: {quote(pkg_hash)}", "groups:"]
    for name in sorted(results):
        r = results[name]
        failed = ", ".join(quote(x) for x in r["failed"])
        opt = ", ".join(quote(x) for x in r["optional_failed"])
        lines.append(f"  {quote(name)}: {{result: {quote(r['result'])}, failed: [{failed}], "
                     f"optional_failed: [{opt}]}}")
    return ("\n".join(lines) + "\n").encode("utf-8")


def record_loads(data):
    doc = load_mapping(data, "packages record")
    check_format(doc.get("format", ""), RECORD_FORMAT, "packages record")
    return doc


# --------------------------------------------------------------------------- command line for postinstall.sh


def _main(argv):
    from . import state  # only needed for the record command

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
    if len(argv) == 5 and argv[0] == "record":
        pkg_dir, record_file, tool, results_file = argv[1:]
        results = {}
        with open(results_file, encoding="utf-8") as f:
            for line in f:
                name, result, failed, opt = (line.rstrip("\n").split("\t") + ["", "", ""])[:4]
                results[name] = {"result": result, "failed": failed.split(), "optional_failed": opt.split()}
        data = record_dumps(state.now_iso(), tool, lists_hash(pkg_dir), results)
        state.atomic_write(record_file, data)
        return 0
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    try:
        sys.exit(_main(sys.argv[1:]))
    except FormatError as e:
        print(f"prod.packages: {e}", file=sys.stderr)
        sys.exit(1)
