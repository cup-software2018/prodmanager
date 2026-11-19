"""Locking released files: `chmod a-w,go+rX`, never following symlinks.

The lock prevents accidents, not attacks: the owner can always chmod it back, and verify reports that.
"""

import os
import re
import shutil
import stat

from .errors import EnvError

SUSPICIOUS_SUFFIXES = ("~", ".swp", ".orig", ".rej", ".tgz", ".tar", ".tar.gz", ".tar.bz2", ".tar.xz", ".zip")
_CORE_DUMP_RE = re.compile(r"^core(\.[0-9]+)?$")  # core, core.12345 (not core.mjs, core.py, ...)
_ELF_CORE = 4  # e_type ET_CORE


def _locked_mode(st_mode):
    mode = stat.S_IMODE(st_mode) & ~0o222  # a-w
    mode |= 0o044  # go+r
    if stat.S_ISDIR(st_mode) or mode & 0o100:
        mode |= 0o011  # go+X (directories, and files the owner can execute)
    return mode


def lock_path(path):
    """Lock one file or directory (not recursive)."""
    try:
        st = os.lstat(path)
        if not stat.S_ISLNK(st.st_mode):
            os.chmod(path, _locked_mode(st.st_mode))
    except PermissionError as e:
        raise EnvError(f"cannot lock {path}: {e.strerror}") from None


def lock_tree(root):
    """Lock a whole directory tree, children before parents."""
    for dirpath, dirnames, filenames in os.walk(root, topdown=False):
        for name in filenames + dirnames:
            lock_path(os.path.join(dirpath, name))
    lock_path(root)


def unlock_tree(root):
    """Give the owner write permission again (u+w) on a tree, parents before children."""
    for dirpath, dirnames, filenames in os.walk(root, topdown=True):
        os.chmod(dirpath, os.lstat(dirpath).st_mode | 0o200)
        for name in filenames:
            p = os.path.join(dirpath, name)
            st = os.lstat(p)
            if not stat.S_ISLNK(st.st_mode):
                os.chmod(p, st.st_mode | 0o200)


def remove_tree(root):
    """Delete a (locked) tree. Only directories get u+w: deleting a file needs write permission on its
    directory, not on the file. Files are left alone because they may be hard links shared with another
    version directory (rsync --link-dest), and changing their mode would unlock that copy too."""
    if not os.path.lexists(root):
        return
    if os.path.islink(root) or not os.path.isdir(root):
        os.unlink(root)
        return
    for dirpath, dirnames, _ in os.walk(root, topdown=True):
        os.chmod(dirpath, os.lstat(dirpath).st_mode | 0o700)
    shutil.rmtree(root)


def unlock_dirs(root):
    """u+w on the directories of a tree only (see remove_tree for why files are left alone)."""
    for dirpath, _, _ in os.walk(root, topdown=True):
        os.chmod(dirpath, os.lstat(dirpath).st_mode | 0o700)


def is_core_dump(path):
    """True for an ELF core file. A file merely named 'core' (e.g. a JSON schema) is not one."""
    try:
        with open(path, "rb") as f:
            head = f.read(18)
    except OSError:
        return False
    if len(head) < 18 or head[:4] != b"\x7fELF" or head[5] not in (1, 2):
        return False
    return int.from_bytes(head[16:18], "little" if head[5] == 1 else "big") == _ELF_CORE


def suspicious_names(root):
    """Paths under root that look like leftovers (editor backups, archives, core dumps)."""
    found = []
    for dirpath, dirnames, filenames in os.walk(root):
        for name in filenames:
            path = os.path.join(dirpath, name)
            if name.endswith(SUSPICIOUS_SUFFIXES) or (
                    _CORE_DUMP_RE.match(name) and not os.path.islink(path) and is_core_dump(path)):
                found.append(os.path.relpath(path, root))
    return sorted(found)
