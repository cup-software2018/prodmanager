"""Strict YAML reading shared by vlist, prod.conf and recipes.

Every scalar is read as a string (implicit typing is disabled), and duplicate keys are rejected.
"""

import json
import re

import yaml

from .errors import FormatError, FormatTooNew

# Newlines, control characters and Unicode line separators are not allowed in values.
BAD_CHARS_RE = re.compile("[\x00-\x1f\x7f\x85  ]")


class StrLoader(yaml.SafeLoader):
    """Loader that reads every scalar as a string and rejects duplicate keys."""

    def construct_mapping(self, node, deep=False):
        seen = set()
        for key_node, _ in node.value:
            key = self.construct_object(key_node, deep=deep)
            if key in seen:
                raise FormatError(f"duplicate key '{key}' (line {key_node.start_mark.line + 1})")
            seen.add(key)
        return super().construct_mapping(node, deep=deep)


# Disable all implicit typing (int, float, bool, null, timestamp).
StrLoader.yaml_implicit_resolvers = {}


def load_mapping(data, what):
    """Parse YAML bytes or text whose top level must be a mapping."""
    if isinstance(data, bytes):
        try:
            data = data.decode("utf-8")
        except UnicodeDecodeError as e:
            raise FormatError(f"{what}: not UTF-8: {e}") from None
    try:
        doc = yaml.load(data, Loader=StrLoader)
    except yaml.YAMLError as e:
        raise FormatError(f"{what}: YAML syntax error: {e}") from None
    if not isinstance(doc, dict):
        raise FormatError(f"{what}: the top level must be a mapping")
    return doc


def check_format(value, supported, what):
    """Check a `format` value. A newer format raises FormatTooNew (exit code 5)."""
    if not isinstance(value, str) or not value.isdigit():
        raise FormatError(f"{what}: format '{value}' is not a number")
    if int(value) > supported:
        raise FormatTooNew(f"{what}: format {value} is newer than this tool supports ({supported}); update the tool")
    if int(value) < 1:
        raise FormatError(f"{what}: invalid format '{value}'")


def check_text(key, value):
    if not isinstance(value, str):
        raise FormatError(f"'{key}' must be a string")
    if BAD_CHARS_RE.search(value):
        raise FormatError(f"'{key}' contains a newline or control character")
    return value


def quote(value):
    """Quote a string for YAML output. A JSON string is also a valid YAML double-quoted scalar
    (control characters are rejected by check_text)."""
    return json.dumps(value, ensure_ascii=False)
