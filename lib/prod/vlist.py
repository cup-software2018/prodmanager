"""vlist: the release definition file `Products/vlist/<name>.yml`.

- All values are handled as strings (see yamlutil), so `3.10` does not become the float 3.1 and dates do
  not become date objects.
- Files written by the tool are byte-deterministic (fixed key order, every value double-quoted), so the
  vlist hash depends only on the content.
- A draft is written by a person and has none of the fields the tool fills in.
"""

import os
import re
from dataclasses import dataclass, field
from typing import List, Optional

from .errors import FormatError
from .hashing import is_hash, sha256_bytes
from .yamlutil import check_format, check_text, load_mapping, quote

FORMAT = 1
SUFFIX = ".yml"
LEGACY_SUFFIX = ".vlist"

NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
PRODUCT_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")
VERSION_RE = re.compile(r"^V[A-Za-z0-9._-]+$")
TIME_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}[+-]\d{2}:\d{2}$")
MODULE_REV_RE = re.compile(r"^-m([2-9]|[1-9][0-9]+)$")

_TOP_KEYS = ("format", "description", "released", "released_by", "migrated_from", "bundle", "products")
_PRODUCT_KEYS = ("name", "version", "manifest", "module", "modulefile")
_TOOL_TOP_KEYS = ("released", "released_by", "migrated_from", "bundle")
_TOOL_PRODUCT_KEYS = ("manifest", "module", "modulefile")


@dataclass
class Product:
    name: str
    version: str
    manifest: Optional[str] = None
    module: Optional[str] = None
    modulefile: Optional[str] = None


@dataclass
class Vlist:
    description: str
    products: List[Product] = field(default_factory=list)
    released: Optional[str] = None
    released_by: Optional[str] = None
    migrated_from: Optional[str] = None
    bundle: Optional[str] = None
    format: str = str(FORMAT)

    @property
    def is_draft(self):
        return self.released is None


# --------------------------------------------------------------------------- names


def check_name(name):
    """vlist name rule."""
    if not isinstance(name, str) or not NAME_RE.match(name) or name in (".", ".."):
        raise FormatError(f"vlist name '{name}' does not match [A-Za-z0-9][A-Za-z0-9._-]*")
    return name


def name_from_path(path):
    base = os.path.basename(path)
    if not base.endswith(SUFFIX):
        raise FormatError(f"vlist file name must end with '{SUFFIX}': {base}")
    return check_name(base[: -len(SUFFIX)])


def module_name_ok(module, name, version):
    """Whether a fixed modulefile name is `<NAME>/<VERSION>` or `<NAME>/<VERSION>-m<N>` (N >= 2)."""
    base = f"{name}/{version}"
    if module == base:
        return True
    return module.startswith(base) and MODULE_REV_RE.match(module[len(base):]) is not None


# --------------------------------------------------------------------------- reading


def _check_hash(key, value):
    if not is_hash(value):
        raise FormatError(f"'{key}' value '{value}' is not sha256:<64 hex digits>")
    return value


def loads(data, *, draft=False, name=None):
    """Parse vlist bytes into a Vlist.

    draft=True applies the draft rules (no tool-filled fields); otherwise the released-vlist rules
    (all fields required). If name is given, migrated_from must match it.
    """
    doc = load_mapping(data, "vlist")

    # Check the format version first: for a newer format, "update the tool" is the right answer
    # even if there are unknown fields.
    if "format" not in doc:
        raise FormatError("vlist: missing field 'format'")
    check_format(doc["format"], FORMAT, "vlist")

    unknown = [k for k in doc if k not in _TOP_KEYS]
    if unknown:
        raise FormatError(f"unknown fields: {', '.join(map(str, unknown))}")

    if draft:
        tool_keys = [k for k in _TOOL_TOP_KEYS if k in doc]
        if tool_keys:
            raise FormatError(f"a draft must not contain fields filled in by the tool: {', '.join(tool_keys)}")
        required = ("description", "products")
    else:
        required = ("description", "released", "released_by", "bundle", "products")
    missing = [k for k in required if k not in doc]
    if missing:
        raise FormatError(f"missing required fields: {', '.join(missing)}")

    description = check_text("description", doc["description"])
    if not description.strip():
        raise FormatError("'description' is empty")

    v = Vlist(description=description, format=doc["format"])
    if not draft:
        v.released = check_text("released", doc["released"])
        if not TIME_RE.match(v.released):
            raise FormatError(f"'released' value '{v.released}' is not an ISO 8601 time with a time zone")
        v.released_by = check_text("released_by", doc["released_by"])
        v.bundle = _check_hash("bundle", doc["bundle"])
        if "migrated_from" in doc:
            v.migrated_from = check_text("migrated_from", doc["migrated_from"])
            expected = f"{name}{LEGACY_SUFFIX}" if name else None
            if not v.migrated_from.endswith(LEGACY_SUFFIX) or (expected and v.migrated_from != expected):
                raise FormatError(f"invalid 'migrated_from' value '{v.migrated_from}'")

    products = doc["products"]
    if not isinstance(products, list) or not products:
        raise FormatError("'products' must be a non-empty list")
    seen = set()
    for i, p in enumerate(products, 1):
        where = f"products[{i}]"
        if not isinstance(p, dict):
            raise FormatError(f"{where} is not a mapping")
        unknown = [k for k in p if k not in _PRODUCT_KEYS]
        if unknown:
            raise FormatError(f"{where}: unknown fields: {', '.join(map(str, unknown))}")
        if draft:
            tool_keys = [k for k in _TOOL_PRODUCT_KEYS if k in p]
            if tool_keys:
                raise FormatError(
                    f"{where}: a draft must not contain fields filled in by the tool: {', '.join(tool_keys)}")
            required = ("name", "version")
        else:
            required = _PRODUCT_KEYS
        missing = [k for k in required if k not in p]
        if missing:
            raise FormatError(f"{where}: missing required fields: {', '.join(missing)}")
        pname, pver = p["name"], p["version"]
        if not isinstance(pname, str) or not PRODUCT_RE.match(pname):
            raise FormatError(f"{where}: product name '{pname}' does not match [A-Z][A-Z0-9_]*")
        if not isinstance(pver, str) or not VERSION_RE.match(pver):
            raise FormatError(f"{where}: version '{pver}' does not match V[A-Za-z0-9._-]+")
        if pname in seen:
            raise FormatError(f"{where}: product '{pname}' appears twice")
        seen.add(pname)
        prod = Product(name=pname, version=pver)
        if not draft:
            prod.manifest = _check_hash(f"{pname} manifest", p["manifest"])
            prod.modulefile = _check_hash(f"{pname} modulefile", p["modulefile"])
            prod.module = check_text(f"{pname} module", p["module"])
            if not module_name_ok(prod.module, pname, pver):
                raise FormatError(f"{where}: module '{prod.module}' is not '{pname}/{pver}[-m<N>]'")
        v.products.append(prod)
    return v


def load(path, *, draft=False):
    with open(path, "rb") as f:
        data = f.read()
    name = check_name(os.path.basename(path)[: -len(SUFFIX)]) if path.endswith(SUFFIX) else None
    return loads(data, draft=draft, name=name)


# --------------------------------------------------------------------------- writing


def dumps(v):
    """Serialize a Vlist to deterministic bytes, and check that the result parses back."""
    lines = [f"format: {quote(str(v.format))}", f"description: {quote(v.description)}"]
    for key in ("released", "released_by", "migrated_from", "bundle"):
        value = getattr(v, key)
        if value is not None:
            lines.append(f"{key}: {quote(value)}")
    lines.append("products:")
    for p in v.products:
        lines.append(f"  - name: {quote(p.name)}")
        for key in ("version", "manifest", "module", "modulefile"):
            value = getattr(p, key)
            if value is not None:
                lines.append(f"    {key}: {quote(value)}")
    data = ("\n".join(lines) + "\n").encode("utf-8")
    loads(data, draft=v.is_draft)  # what we wrote must satisfy the rules
    return data


def vlist_hash(data):
    """vlist hash = sha256 of the file bytes."""
    return sha256_bytes(data)


# --------------------------------------------------------------------------- legacy format


def parse_legacy(text):
    """Parse a legacy `<name>.vlist` ("NAME VERSION" lines, `#` comments) into [(name, version), ...]."""
    result = []
    seen = set()
    for lineno, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) != 2:
            raise FormatError(f"line {lineno}: not 'NAME VERSION': {raw!r}")
        pname, pver = parts
        if not PRODUCT_RE.match(pname) or not VERSION_RE.match(pver):
            raise FormatError(f"line {lineno}: invalid product name or version: {raw!r}")
        if pname in seen:
            raise FormatError(f"line {lineno}: product '{pname}' appears twice")
        seen.add(pname)
        result.append((pname, pver))
    if not result:
        raise FormatError("no products")
    return result
