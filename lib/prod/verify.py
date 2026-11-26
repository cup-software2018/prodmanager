"""`prodman verify`. Reads only local files unless --remote.

Checks, per vlist:
  1. the vlist, its bundle modulefile, and each product's manifest and modulefile are there
  2. each version directory == its manifest (shared versions are checked once)
  3. writable files: a warning, and they are locked again (not a version that differs from its manifest)
  4. with --remote (clients): the server still has the vlist, with the same content
  5. with check_environment (the prodman command): the vlist loads with setup_prod.sh (else an error); missing
     system packages and, unless quick, missing shared libraries -> warnings (see envcheck). For the Products
     owner, the links install.sh makes in $HOME are checked too ("(tool)").
"""

import glob
import os
import tempfile
from dataclasses import dataclass, field
from typing import Dict, List

from . import envcheck, fsutil, manifest, state, transfer
from . import progress as progress_mod
from . import vlist as vlist_mod
from .errors import EnvError, FormatError

MAX_SHOWN = 10


@dataclass
class Report:
    name: str
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    @property
    def ok(self):
        return not self.errors

    def as_dict(self):
        return {"vlist": self.name, "ok": self.ok, "errors": self.errors, "warnings": self.warnings}


def installed_vlists(paths):
    return sorted(os.path.basename(p)[: -len(vlist_mod.SUFFIX)]
                  for p in glob.glob(os.path.join(paths.vlist, "*" + vlist_mod.SUFFIX)))


def _lock_again(lock, path, label):
    """Lock a writable path again; the warning to report."""
    try:
        lock(path)
    except EnvError as e:
        return f"{label}: writable ({e})"
    return f"{label}: was writable, locked again"


def check_version(paths, name, version, *, quick=False, progress=progress_mod.SILENT):
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
        label = f"{name} {version}: {len(warnings)} entries"
        if errors:
            warn.append(f"{label} writable (not locked: the version differs from its manifest)")
        else:
            warn.append(_lock_again(fsutil.lock_tree, vdir, label))
    return err, warn


def _check_file(path, label, rep):
    if not os.path.exists(path):
        rep.errors.append(f"{label}: missing ({path})")
    elif os.lstat(path).st_mode & 0o222:
        rep.warnings.append(_lock_again(fsutil.lock_path, path, label))


def verify_vlist(paths, cfg, name, *, quick=False, remote=False, cache=None, check_environment=False,
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
    try:
        v = vlist_mod.loads(data, name=name)
    except FormatError as e:
        rep.errors.append(f"vlist file is broken: {e}")
        return rep

    if remote and cfg.role == "client":
        with tempfile.TemporaryDirectory(dir=_tmp_dir(paths)) as tmp:
            rel = f"vlist/{name}{vlist_mod.SUFFIX}"
            if rel not in transfer.fetch_files(cfg, [rel], tmp):
                rep.errors.append("the server no longer has this vlist")
            else:
                with open(os.path.join(tmp, rel), "rb") as f:
                    if f.read() != data:
                        rep.errors.append("the server's vlist differs from this node's (prodman resync)")
    _check_file(vfile, "vlist file", rep)

    progress.line(f"{name}: {len(v.products)} products")
    _check_file(paths.bundle_file(name), "bundle modulefile", rep)
    for p in v.products:
        _check_file(paths.manifest_file(p.name, p.version), f"{p.name} {p.version} manifest", rep)
        _check_file(paths.module_file(p.module), f"modulefile {p.module}", rep)
        key = (p.name, p.version)
        if key not in cache:
            cache[key] = check_version(paths, p.name, p.version, quick=quick, progress=progress)
        else:
            progress.item(p.name, p.version, "checked above")
        err, warn = cache[key]
        rep.errors += err
        rep.warnings += warn
    if check_environment:
        errors, warnings = envcheck.environment_check(
            paths, name, [(p.name, p.version) for p in v.products], home=os.environ.get("HOME", ""),
            run_ldd=not quick, progress=progress)
        rep.errors += errors
        rep.warnings += warnings
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


def verify(paths, cfg, names=None, *, quick=False, remote=False, check_environment=False,
           progress=progress_mod.SILENT):
    """Verify vlists (all installed ones if names is empty). On the server, also every frozen version that
    is in no vlist. Returns a list of Reports."""
    if remote and cfg.role != "client":
        remote = False
    with state.lock(paths, exclusive=False):
        all_names = installed_vlists(paths)
        names = list(names) if names else all_names
        cache: Dict = {}
        reports = []
        if check_environment:
            tool = Report("(tool)")
            tool.warnings = envcheck.tool_problems(paths, os.environ.get("HOME", ""))
            if tool.warnings:
                reports.append(tool)
        reports += [verify_vlist(paths, cfg, n, quick=quick, remote=remote, cache=cache,
                                 check_environment=check_environment, progress=progress)
                    for n in names]
        if cfg.role == "server" and names == all_names:
            orphans = Report("(frozen versions in no vlist)")
            rest = [key for key in frozen_versions(paths) if key not in cache]
            if rest:
                progress.line(f"frozen versions in no vlist: {len(rest)}")
            for key in rest:
                if key not in cache:
                    err, warn = check_version(paths, *key, quick=quick, progress=progress)
                    orphans.errors += err
                    orphans.warnings += warn
            if orphans.errors or orphans.warnings:
                reports.append(orphans)
    return reports
