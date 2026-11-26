"""manifest: file list and sha256 of one version directory.

One entry per line, tab-separated: `kind  exec  size  content  path`

    d  -  -     -              .
    f  x  1203  sha256:...     bin/thisroot.sh
    l  -  -     libCore.so.6   lib/libCore.so

- Every directory is listed. Only the executable bit is recorded; mtime, owner and hard links are not.
- Paths are relative to the version directory and sorted by bytes. Paths and link targets are kept as
  raw filesystem bytes (non-UTF-8 names are preserved).
"""

import os
import stat
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Optional

from .errors import FormatError, TreeError
from .hashing import is_hash, sha256_file

SUFFIX = ".manifest"
MAGIC = b"# prodmanager manifest"
_HEADER_KEYS = ("product", "version", "created", "entries")
_BAD_PATH_CHARS = ("\t", "\n", "\r")


@dataclass(frozen=True)
class Entry:
    kind: str  # 'f' file, 'l' symlink, 'd' directory
    path: str  # relative to the version directory; the root is '.'
    exe: bool = False  # files only: owner executable bit
    size: Optional[int] = None  # files only
    content: Optional[str] = None  # file: 'sha256:...', symlink: link target


@dataclass(frozen=True)
class Finding:
    level: str  # 'error' | 'warning'
    code: str  # missing, kind, exec, size, content, target, extra, writable
    path: str
    message: str


def _sort_key(path):
    return os.fsencode(path)


def _default_workers():
    return min(8, os.cpu_count() or 1)


# --------------------------------------------------------------------------- reading a tree


def _scan(root):
    """Return every entry under root as (relative path, lstat). The root itself is '.'."""
    items = [(".", os.lstat(root))]
    stack = [""]
    while stack:
        rel = stack.pop()
        absdir = os.path.join(root, rel) if rel else root
        with os.scandir(absdir) as it:
            for de in it:
                r = f"{rel}/{de.name}" if rel else de.name
                st = de.stat(follow_symlinks=False)
                items.append((r, st))
                if stat.S_ISDIR(st.st_mode):
                    stack.append(r)
    return items


def _within(path, root_real):
    return path == root_real or path.startswith(root_real + os.sep)


def _link_violation(root, root_real, rel, target):
    if target.startswith("/"):
        return f"absolute symlink ({target})"
    if any(c in target for c in _BAD_PATH_CHARS):
        return "link target contains a tab or newline"
    lexical = os.path.normpath(os.path.join(os.path.dirname(rel), target))
    if lexical == ".." or lexical.startswith("../"):
        return f"symlink points outside the version directory ({target})"
    abspath = os.path.join(root, rel)
    if not os.path.exists(abspath):
        return f"dangling symlink ({target})"
    if not _within(os.path.realpath(abspath), root_real):
        return f"symlink resolves outside the version directory ({target})"
    return None


def build(root, *, workers=None):
    """Build the manifest entries of version directory root. Raises TreeError on tree-rule violations."""
    if not os.path.isdir(root) or os.path.islink(root):
        raise FormatError(f"not a version directory: {root}")
    root_real = os.path.realpath(root)
    violations = []
    entries = []
    files = []
    for rel, st in _scan(root):
        if any(c in rel for c in _BAD_PATH_CHARS):
            violations.append((repr(rel), "path contains a tab or newline"))
            continue
        mode = st.st_mode
        if stat.S_ISDIR(mode):
            entries.append(Entry("d", rel))
        elif stat.S_ISLNK(mode):
            target = os.readlink(os.path.join(root, rel))
            reason = _link_violation(root, root_real, rel, target)
            if reason:
                violations.append((rel, reason))
            else:
                entries.append(Entry("l", rel, content=target))
        elif stat.S_ISREG(mode):
            files.append((rel, bool(mode & stat.S_IXUSR), st.st_size))
        else:
            violations.append((rel, "special file (socket, device, FIFO, ...)"))
    if violations:
        raise TreeError(sorted(violations))

    paths = [os.path.join(root, rel) for rel, _, _ in files]
    with ThreadPoolExecutor(max_workers=workers or _default_workers()) as pool:
        hashes = list(pool.map(sha256_file, paths))
    for (rel, exe, size), h in zip(files, hashes):
        entries.append(Entry("f", rel, exe=exe, size=size, content=h))
    entries.sort(key=lambda e: _sort_key(e.path))
    return entries


# --------------------------------------------------------------------------- writing, reading


def dumps(entries, *, product, version, created):
    """Serialize a manifest. created is a '<time> by <tool> on <host>' string."""
    entries = sorted(entries, key=lambda e: _sort_key(e.path))
    out = [
        MAGIC,
        b"# product: " + product.encode(),
        b"# version: " + version.encode(),
        b"# created: " + created.encode(),
        b"# entries: %d" % len(entries),
    ]
    for e in entries:
        path = os.fsencode(e.path)
        if e.kind == "f":
            fields = [b"f", b"x" if e.exe else b"-", b"%d" % e.size, e.content.encode()]
        elif e.kind == "l":
            fields = [b"l", b"-", b"-", os.fsencode(e.content)]
        else:
            fields = [b"d", b"-", b"-", b"-"]
        out.append(b"\t".join(fields + [path]))
    data = b"\n".join(out) + b"\n"
    loads(data)  # what we wrote must satisfy the rules
    return data


def loads(data):
    """Parse manifest bytes into (header dict, [Entry])."""
    lines = data.split(b"\n")
    if lines and lines[-1] == b"":
        lines.pop()
    else:
        raise FormatError("manifest does not end with a newline")
    if not lines or lines[0] != MAGIC:
        raise FormatError("manifest header is missing")

    header = {}
    i = 1
    while i < len(lines) and lines[i].startswith(b"# "):
        key, sep, value = lines[i][2:].partition(b": ")
        if not sep:
            raise FormatError(f"manifest header line {i + 1} is not 'key: value'")
        key = key.decode("utf-8", "replace")
        if key in header:
            raise FormatError(f"manifest header has '{key}' twice")
        header[key] = value.decode("utf-8", "surrogateescape")
        i += 1

    missing = [k for k in _HEADER_KEYS if k not in header]
    if missing:
        raise FormatError(f"manifest header is missing: {', '.join(missing)}")

    entries = []
    prev = None
    for lineno, line in enumerate(lines[i:], i + 1):
        parts = line.split(b"\t")
        if len(parts) != 5:
            raise FormatError(f"manifest line {lineno}: expected 5 columns")
        kind, exe, size, content, path_b = parts
        path = os.fsdecode(path_b)
        if not path_b or any(c in path for c in _BAD_PATH_CHARS):
            raise FormatError(f"manifest line {lineno}: invalid path")
        if prev is not None and path_b <= prev:
            raise FormatError(f"manifest line {lineno}: not sorted, or duplicate path")
        prev = path_b
        if kind == b"f":
            if exe not in (b"x", b"-") or not size.isdigit() or not is_hash(content.decode("ascii", "replace")):
                raise FormatError(f"manifest line {lineno}: invalid file entry")
            entries.append(Entry("f", path, exe=exe == b"x", size=int(size), content=content.decode()))
        elif kind == b"l":
            if exe != b"-" or size != b"-" or not content:
                raise FormatError(f"manifest line {lineno}: invalid symlink entry")
            entries.append(Entry("l", path, content=os.fsdecode(content)))
        elif kind == b"d":
            if (exe, size, content) != (b"-", b"-", b"-"):
                raise FormatError(f"manifest line {lineno}: invalid directory entry")
            entries.append(Entry("d", path))
        else:
            raise FormatError(f"manifest line {lineno}: unknown kind {kind!r}")

    if str(len(entries)) != header["entries"]:
        raise FormatError(f"manifest has {len(entries)} entries but the header says {header['entries']}")
    if not any(e.path == "." and e.kind == "d" for e in entries):
        raise FormatError("manifest has no root directory entry '.'")
    return header, entries


def load(path):
    with open(path, "rb") as f:
        return loads(f.read())


# --------------------------------------------------------------------------- verification


def verify(entries, root, *, quick=False, workers=None):
    """Compare version directory root with manifest entries and return Findings.

    quick=True skips content hashes (checks kind, executable bit and size only).
    """
    findings = []
    if not os.path.isdir(root) or os.path.islink(root):
        return [Finding("error", "missing", ".", "version directory does not exist")]
    actual = dict(_scan(root))
    expected = {e.path: e for e in entries}
    to_hash = []

    for path, e in expected.items():
        st = actual.get(path)
        if st is None:
            findings.append(Finding("error", "missing", path, "missing"))
            continue
        mode = st.st_mode
        kind = "d" if stat.S_ISDIR(mode) else "l" if stat.S_ISLNK(mode) else "f" if stat.S_ISREG(mode) else "?"
        if kind != e.kind:
            findings.append(Finding("error", "kind", path, f"kind differs (expected {e.kind}, found {kind})"))
            continue
        if kind in ("f", "d") and mode & 0o222:
            findings.append(Finding("warning", "writable", path, "write bit is set"))
        if kind == "f":
            exe = bool(mode & stat.S_IXUSR)
            if exe != e.exe:
                findings.append(Finding("error", "exec", path,
                                        f"executable bit differs (expected {'x' if e.exe else '-'})"))
            if st.st_size != e.size:
                findings.append(Finding("error", "size", path,
                                        f"size differs (expected {e.size}, found {st.st_size})"))
            elif not quick:
                to_hash.append(path)
        elif kind == "l":
            target = os.readlink(os.path.join(root, path))
            if target != e.content:
                findings.append(Finding("error", "target", path,
                                        f"link target differs (expected {e.content}, found {target})"))

    for path in actual.keys() - expected.keys():
        findings.append(Finding("error", "extra", path, "not in the manifest"))

    if to_hash:
        with ThreadPoolExecutor(max_workers=workers or _default_workers()) as pool:
            hashes = list(pool.map(sha256_file, [os.path.join(root, p) for p in to_hash]))
        for path, h in zip(to_hash, hashes):
            if h != expected[path].content:
                findings.append(Finding("error", "content", path, "content differs"))

    findings.sort(key=lambda f: (_sort_key(f.path), f.code))
    return findings


def has_errors(findings):
    return any(f.level == "error" for f in findings)
