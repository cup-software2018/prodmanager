"""vlist: the release definition file `Products/vlist/<name>.yml`.

- All values are handled as strings (see yamlutil), so `3.10` does not become the float 3.1 and dates do
  not become date objects.
- Files written by the tool are byte-deterministic (fixed key order, every value double-quoted).
- A draft is written by a person and has none of the fields the tool fills in (released, released_by).
- A product's modulefile is always `<NAME>/<VERSION>`.
"""

import os
import re
from dataclasses import dataclass, field
from typing import List, Optional

from .errors import FormatError
from .yamlutil import check_text, load_mapping, quote

SUFFIX = ".yml"

NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
PRODUCT_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")
VERSION_RE = re.compile(r"^V[A-Za-z0-9._-]+$")
TIME_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}[+-]\d{2}:\d{2}$")

_TOP_KEYS = ("description", "released", "released_by", "products")
_PRODUCT_KEYS = ("name", "version")
_TOOL_TOP_KEYS = ("released", "released_by")


@dataclass
class Product:
    name: str
    version: str

    @property
    def module(self):
        return f"{self.name}/{self.version}"


@dataclass
class Vlist:
    description: str
    products: List[Product] = field(default_factory=list)
    released: Optional[str] = None
    released_by: Optional[str] = None

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


# --------------------------------------------------------------------------- reading


def loads(data, *, draft=False, name=None):
    """Parse vlist bytes into a Vlist.

    draft=True applies the draft rules (no tool-filled fields); otherwise the released-vlist rules
    (all fields required).
    """
    doc = load_mapping(data, "vlist")

    unknown = [k for k in doc if k not in _TOP_KEYS]
    if unknown:
        raise FormatError(f"unknown fields: {', '.join(map(str, unknown))}")

    if draft:
        tool_keys = [k for k in _TOOL_TOP_KEYS if k in doc]
        if tool_keys:
            raise FormatError(f"a draft must not contain fields filled in by the tool: {', '.join(tool_keys)}")
        required = ("description", "products")
    else:
        required = _TOP_KEYS
    missing = [k for k in required if k not in doc]
    if missing:
        raise FormatError(f"missing required fields: {', '.join(missing)}")

    description = check_text("description", doc["description"])
    if not description.strip():
        raise FormatError("'description' is empty")

    v = Vlist(description=description)
    if not draft:
        v.released = check_text("released", doc["released"])
        if not TIME_RE.match(v.released):
            raise FormatError(f"'released' value '{v.released}' is not an ISO 8601 time with a time zone")
        v.released_by = check_text("released_by", doc["released_by"])

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
        missing = [k for k in _PRODUCT_KEYS if k not in p]
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
        v.products.append(Product(name=pname, version=pver))
    return v


def load(path, *, draft=False):
    with open(path, "rb") as f:
        data = f.read()
    name = check_name(os.path.basename(path)[: -len(SUFFIX)]) if path.endswith(SUFFIX) else None
    return loads(data, draft=draft, name=name)


# --------------------------------------------------------------------------- writing


def dumps(v):
    """Serialize a Vlist to deterministic bytes, and check that the result parses back."""
    lines = [f"description: {quote(v.description)}"]
    for key in ("released", "released_by"):
        value = getattr(v, key)
        if value is not None:
            lines.append(f"{key}: {quote(value)}")
    lines.append("products:")
    for p in v.products:
        lines.append(f"  - name: {quote(p.name)}")
        lines.append(f"    version: {quote(p.version)}")
    data = ("\n".join(lines) + "\n").encode("utf-8")
    loads(data, draft=v.is_draft)  # what we wrote must satisfy the rules
    return data
