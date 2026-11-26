"""buildinfo: a human-readable record of what was built, kept in the saved recipe.

What to extract is defined per product by `build-recipes/<NAME>/buildinfo.sh <version dir>`, which prints
`key<TAB>value` lines. save-recipe.py runs it when build.sh has built the version and writes the lines into
`<VERSION>.recipe.yml` as `buildinfo`. The tool does not verify it; it is a record, not a reference.
"""

import os
import subprocess

from .errors import FormatError
from .yamlutil import BAD_CHARS_RE, quote

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


def yaml_block(info):
    """The `buildinfo:` mapping for the saved recipe. A key printed twice gets ' (2)', ' (3)', ..."""
    if not info:
        return "buildinfo: {}\n"
    seen = {}
    lines = ["buildinfo:"]
    for k, v in info:
        seen[k] = seen.get(k, 0) + 1
        key = k if seen[k] == 1 else f"{k} ({seen[k]})"
        lines.append(f"  {quote(key)}: {quote(v)}")
    return "\n".join(lines) + "\n"


def script_path(recipes_root, product):
    path = os.path.join(recipes_root, product, "buildinfo.sh")
    return path if os.path.exists(path) else None
