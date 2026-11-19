"""rsync from the products server. The server is only ever read.

The server's Products is either remote (`[user@]host:/path`, over ssh with the configured options)
or a local path (sandbox tests).
"""

import os
import shlex
import subprocess
import tempfile

from .errors import EnvError

# rsync exit codes that mean "could not talk to the server" rather than "a file is missing".
_CONNECTION_CODES = {5, 10, 12, 30, 35, 255}
# Base options for version directories: recursive, symlinks as symlinks, permissions, times, hard links.
# No --chmod: the server's locked permissions are copied as they are, so that --link-dest can reuse
# identical files of the previous local copy (it only hard-links files whose attributes match).
TREE_OPTIONS = ["-rlptH", "--delete"]


def _base(cfg):
    cmd = ["rsync"]
    if not cfg.server.is_local:
        cmd += ["-e", shlex.join(["ssh"] + list(cfg.ssh_options))]
    return cmd


def _run(cmd):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, check=False)
    except FileNotFoundError:
        raise EnvError("rsync is not installed") from None


def _connection_error(cfg, out):
    return EnvError(f"cannot read from the products server {cfg.server.spec}: {out.stderr.strip()}")


def fetch_files(cfg, rel_paths, dest):
    """Copy server files (paths relative to the server's Products) into dest, keeping the relative paths.

    Returns the set of relative paths that were fetched; missing files are simply absent.
    """
    rel_paths = sorted(set(rel_paths))
    os.makedirs(dest, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", dir=dest, prefix=".files-from.", delete=False) as f:
        f.write("\n".join(rel_paths) + "\n")
        listfile = f.name
    try:
        out = _run(_base(cfg) + ["-lpt", f"--files-from={listfile}", cfg.server.rsync_url("") + "/", dest + "/"])
    finally:
        os.unlink(listfile)
    if out.returncode in _CONNECTION_CODES:
        raise _connection_error(cfg, out)
    if out.returncode not in (0, 23):  # 23: some files were missing
        raise EnvError(f"rsync failed ({out.returncode}): {out.stderr.strip()}")
    return {r for r in rel_paths if os.path.lexists(os.path.join(dest, r))}


def fetch_tree(cfg, rel_dir, dest, *, link_dest=None, checksum=False):
    """Mirror a server directory into dest."""
    cmd = _base(cfg) + list(TREE_OPTIONS)
    if checksum:
        cmd.append("--checksum")
    if link_dest:
        cmd.append(f"--link-dest={os.path.abspath(link_dest)}")
    cmd += [cfg.server.rsync_url(rel_dir) + "/", dest + "/"]
    out = _run(cmd)
    if out.returncode in _CONNECTION_CODES:
        raise _connection_error(cfg, out)
    if out.returncode != 0:
        raise EnvError(f"rsync of {rel_dir} failed ({out.returncode}): {out.stderr.strip()}")


def list_dir(cfg, rel_dir):
    """Names in a server directory (empty if it does not exist)."""
    out = _run(_base(cfg) + ["--list-only", cfg.server.rsync_url(rel_dir) + "/"])
    if out.returncode in _CONNECTION_CODES:
        raise _connection_error(cfg, out)
    if out.returncode == 23:
        return []
    if out.returncode != 0:
        raise EnvError(f"rsync --list-only failed ({out.returncode}): {out.stderr.strip()}")
    names = []
    for line in out.stdout.splitlines():
        parts = line.split(None, 4)
        if len(parts) == 5 and parts[4] not in (".", ".."):
            names.append(parts[4])
    return sorted(names)
