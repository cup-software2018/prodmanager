"""`prod verify`. Reads only local files unless --remote.

Checks, per vlist:
  1. vlist hash == trusted record (clients only)
  2. bundle, manifest and fixed modulefile hashes == the values in the vlist
  3. each version directory == its manifest (shared versions are checked once)
  4. withdrawn -> warning
"""

import glob
import os
import tempfile
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from . import fsutil, manifest, state, transfer
from . import progress as progress_mod
from . import vlist as vlist_mod
from .errors import FormatError
from .hashing import sha256_file
from .release import withdrawn_loads

MAX_SHOWN = 10


@dataclass
class Report:
    name: str
    vlist_hash: Optional[str] = None
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    withdrawn: Optional[dict] = None

    @property
    def ok(self):
        return not self.errors

    def as_dict(self):
        return {"vlist": self.name, "vlist_hash": self.vlist_hash, "ok": self.ok, "errors": self.errors,
                "warnings": self.warnings, "withdrawn": self.withdrawn}


def installed_vlists(paths):
    return sorted(os.path.basename(p)[: -len(vlist_mod.SUFFIX)]
                  for p in glob.glob(os.path.join(paths.vlist, "*" + vlist_mod.SUFFIX)))


def read_withdrawn(path):
    try:
        with open(path, "rb") as f:
            return withdrawn_loads(f.read())
    except FileNotFoundError:
        return None


def check_version(paths, name, version, *, quick=False, fix_perms=False, progress=progress_mod.SILENT):
    """Compare one version directory with its manifest. Returns (errors, warnings) as strings."""
    vdir = paths.version_dir(name, version)
    try:
        _, entries = manifest.load(paths.manifest_file(name, version))
    except FileNotFoundError:
        return [f"{name} {version}: manifest is missing"], []
    timer = progress_mod.Timer()
    progress.item(name, version, f"verifying {len(entries)} entries{' (quick)' if quick else ''} ...")
    findings = manifest.verify(entries, vdir, quick=quick)
    errors = [f for f in findings if f.level == "error"]
    warnings = [f for f in findings if f.level == "warning"]
    progress.item(name, version, (f"{len(errors)} difference(s)" if errors else "ok") + f" ({timer})")
    err = [f"{name} {version}: {f.path}: {f.message}" for f in errors[:MAX_SHOWN]]
    if len(errors) > MAX_SHOWN:
        err.append(f"{name} {version}: ... and {len(errors) - MAX_SHOWN} more differences")
    warn = []
    if warnings:
        if fix_perms and not errors:
            fsutil.lock_tree(vdir)
            warn.append(f"{name} {version}: {len(warnings)} writable entries were locked again")
        else:
            warn.append(f"{name} {version}: {len(warnings)} writable entries (prod verify --fix-perms locks them)")
    return err, warn


def _check_file(path, expected, label, fix_perms, rep):
    if not os.path.exists(path):
        rep.errors.append(f"{label}: missing ({path})")
        return
    if sha256_file(path) != expected:
        rep.errors.append(f"{label}: hash differs from the vlist")
    elif os.lstat(path).st_mode & 0o222:
        if fix_perms:
            fsutil.lock_path(path)
            rep.warnings.append(f"{label}: was writable, locked again")
        else:
            rep.warnings.append(f"{label}: writable")


def verify_vlist(paths, cfg, name, *, quick=False, fix_perms=False, remote=False, cache=None,
                 progress=progress_mod.SILENT):
    cache = {} if cache is None else cache
    rep = Report(name)
    vfile = paths.vlist_file(name)
    try:
        with open(vfile, "rb") as f:
            data = f.read()
    except FileNotFoundError:
        rep.errors.append(f"vlist {name} is not on this node")
        return rep
    rep.vlist_hash = vlist_mod.vlist_hash(data)
    try:
        v = vlist_mod.loads(data, name=name)
    except FormatError as e:
        rep.errors.append(f"vlist file is broken: {e}")
        return rep

    if cfg.role == "client":
        trusted = state.get_trusted(paths, name)
        if trusted is None:
            rep.errors.append("no trusted hash record for this vlist (was it installed by prod sync?)")
        elif trusted != rep.vlist_hash:
            rep.errors.append(f"vlist file differs from the one first synced ({trusted})")
        if remote:
            with tempfile.TemporaryDirectory(dir=_tmp_dir(paths)) as tmp:
                rel = f"vlist/{name}{vlist_mod.SUFFIX}"
                if rel not in transfer.fetch_files(cfg, [rel], tmp):
                    rep.errors.append("the server no longer has this vlist")
                else:
                    with open(os.path.join(tmp, rel), "rb") as f:
                        if vlist_mod.vlist_hash(f.read()) != trusted:
                            rep.errors.append("the server's vlist differs from the one first synced")
    if os.lstat(vfile).st_mode & 0o222:
        if fix_perms:
            fsutil.lock_path(vfile)
        else:
            rep.warnings.append("vlist file is writable")

    rep.withdrawn = read_withdrawn(paths.withdrawn_file(name))
    if rep.withdrawn:
        msg = f"withdrawn: {rep.withdrawn['reason']}"
        if rep.withdrawn.get("replaced_by"):
            msg += f" (use {rep.withdrawn['replaced_by']} instead)"
        rep.warnings.append(msg)

    progress.line(f"{name}: {len(v.products)} products")
    _check_file(paths.bundle_file(name), v.bundle, "bundle modulefile", fix_perms, rep)
    for p in v.products:
        _check_file(paths.manifest_file(p.name, p.version), p.manifest, f"{p.name} {p.version} manifest",
                    fix_perms, rep)
        _check_file(paths.module_file(p.module), p.modulefile, f"modulefile {p.module}", fix_perms, rep)
        key = (p.name, p.version)
        if key not in cache:
            cache[key] = check_version(paths, p.name, p.version, quick=quick, fix_perms=fix_perms,
                                       progress=progress)
        else:
            progress.item(p.name, p.version, "checked above")
        err, warn = cache[key]
        rep.errors += err
        rep.warnings += warn
    return rep


def _tmp_dir(paths):
    os.makedirs(paths.tmp, exist_ok=True)
    return paths.tmp


def frozen_versions(paths):
    """All (name, version) with a manifest under Products (used on the server)."""
    result = []
    for m in glob.glob(os.path.join(paths.products, "*", "*" + manifest.SUFFIX)):
        name = os.path.basename(os.path.dirname(m))
        if name.startswith("."):
            continue
        result.append((name, os.path.basename(m)[: -len(manifest.SUFFIX)]))
    return sorted(result)


def verify(paths, cfg, names=None, *, quick=False, fix_perms=False, remote=False,
           progress=progress_mod.SILENT):
    """Verify vlists (all installed ones if names is empty). On the server, also every frozen version that
    is in no vlist. Returns a list of Reports."""
    if remote and cfg.role != "client":
        remote = False
    exclusive = fix_perms
    with state.lock(paths, exclusive=exclusive):
        all_names = installed_vlists(paths)
        names = list(names) if names else all_names
        cache: Dict = {}
        reports = [verify_vlist(paths, cfg, n, quick=quick, fix_perms=fix_perms, remote=remote, cache=cache,
                                progress=progress)
                   for n in names]
        if cfg.role == "server" and names == all_names:
            orphans = Report("(frozen versions in no vlist)")
            rest = [key for key in frozen_versions(paths) if key not in cache]
            if rest:
                progress.line(f"frozen versions in no vlist: {len(rest)}")
            for key in rest:
                if key not in cache:
                    err, warn = check_version(paths, *key, quick=quick, fix_perms=fix_perms, progress=progress)
                    orphans.errors += err
                    orphans.warnings += warn
            if orphans.errors or orphans.warnings:
                reports.append(orphans)
    return reports
