"""sha256 helpers. Hashes are always written as `sha256:<hex>`."""

import hashlib
import re

HASH_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_CHUNK = 1 << 20


def sha256_bytes(data):
    return "sha256:" + hashlib.sha256(data).hexdigest()


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(_CHUNK)
            if not chunk:
                break
            h.update(chunk)
    return "sha256:" + h.hexdigest()


def is_hash(value):
    return isinstance(value, str) and HASH_RE.match(value) is not None
