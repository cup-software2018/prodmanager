"""buildinfo: a human-readable build record next to each version.

What to extract is defined per product by `recipes/<NAME>/buildinfo.sh <version dir>`, which prints
`key<TAB>value` lines. The tool does not verify buildinfo; it is a record, not a reference.
"""

import os
import subprocess

from .errors import FormatError
from .yamlutil import BAD_CHARS_RE, quote

FORMAT = 1
SUFFIX = ".buildinfo"


def os_name():
    try:
        with open("/etc/os-release", encoding="utf-8") as f:
            for line in f:
                if line.startswith("PRETTY_NAME="):
                    return line.split("=", 1)[1].strip().strip('"')
    except OSError:
        pass
    return "unknown OS"


def extract(script, prefix, timeout=600):
    """Run buildinfo.sh with the version directory and parse its `key<TAB>value` lines."""
    try:
        out = subprocess.run([script, prefix], capture_output=True, text=True, timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise FormatError(f"{script} failed: {e}") from None
    if out.returncode != 0:
        raise FormatError(f"{script} exited with {out.returncode}: {out.stderr.strip()}")
    info = []
    for lineno, line in enumerate(out.stdout.splitlines(), 1):
        if not line.strip():
            continue
        key, sep, value = line.partition("\t")
        if not sep or not key:
            raise FormatError(f"{script} output line {lineno} is not 'key<TAB>value'")
        # Values are records for people; replace control characters instead of failing.
        info.append((BAD_CHARS_RE.sub(" ", key), BAD_CHARS_RE.sub(" ", value)))
    return info


def dumps(product, version, source, recipe, collected, info):
    lines = [
        f"format: {quote(str(FORMAT))}",
        f"product: {quote(product)}",
        f"version: {quote(version)}",
        f"source: {quote(source)}",
        f"recipe: {quote(recipe)}",
        f"collected: {quote(collected)}",
    ]
    if info:
        lines.append("info:")
        lines += [f"  {quote(k)}: {quote(v)}" for k, v in info]
    else:
        lines.append("info: {}")
    return ("\n".join(lines) + "\n").encode("utf-8")


def script_path(recipes_root, product):
    path = os.path.join(recipes_root, product, "buildinfo.sh")
    return path if os.path.exists(path) else None
