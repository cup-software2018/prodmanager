"""`prodman sync` and `prodman resync` on a client.

sync installs the vlists named; resync syncs every vlist already on this node again.

Per vlist:
  1. fetch the vlist into a temporary directory and parse it
  2. fetch manifests, saved recipes (if any), modulefiles and the bundle
  3. per version: keep it if it already matches its manifest; otherwise mirror it into
     `<NAME>/.<VER>.partial` (reusing unchanged files with --link-dest), verify it completely, lock it,
     and swap it in with renames (running processes keep the old inodes)
  4. put the metadata in place, the vlist file last: once the vlist is visible, everything it needs is there
  5. record history
"""

import os
import tempfile
import time
from dataclasses import dataclass, field
from typing import List

from . import envcheck, fsutil, manifest, state, transfer
from . import progress as progress_mod
from . import vlist as vlist_mod
from .errors import CheckFailed, UsageError
from .verify import installed_vlists


@dataclass
class VersionResult:
    name: str
    version: str
    action: str  # 'in place', 'transferred', 'would transfer'
    entries: int = 0


@dataclass
class SyncResult:
    name: str
    versions: List[VersionResult] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    dry_run: bool = False
    seconds: float = 0.0


def _read(path):
    with open(path, "rb") as f:
        return f.read()


def _first(findings, n=3):
    errors = [f for f in findings if f.level == "error"]
    shown = "; ".join(f"{f.path}: {f.message}" for f in errors[:n])
    return shown + (f" (and {len(errors) - n} more)" if len(errors) > n else "")


def _transfer(paths, cfg, name, version, entries, progress):
    """Mirror one version from the server and swap it in (step 3)."""
    timer = progress_mod.Timer()
    nbytes = progress_mod.size(progress_mod.total_size(entries))
    vdir = paths.version_dir(name, version)
    parent = os.path.dirname(vdir)
    partial = os.path.join(parent, f".{version}.partial")
    old = os.path.join(parent, f".{version}.old")
    os.makedirs(parent, exist_ok=True)
    if os.path.lexists(partial):
        fsutil.unlock_dirs(partial)  # resume an interrupted transfer
    link = vdir if os.path.isdir(vdir) and not os.path.islink(vdir) else None
    rel = f"{name}/{version}"
    progress.item(name, version, f"transferring {len(entries)} entries, {nbytes} ...")
    transfer.fetch_tree(cfg, rel, partial, link_dest=link, live=progress.live)
    progress.item(name, version, "verifying ...")
    findings = manifest.verify(entries, partial)
    if manifest.has_errors(findings):
        progress.item(name, version, f"differs from the manifest ({_first(findings, 1)}); "
                                     "transferring again with full checksums ...")
        fsutil.unlock_dirs(partial)
        transfer.fetch_tree(cfg, rel, partial, link_dest=link, checksum=True, live=progress.live)
        progress.item(name, version, "verifying ...")
        findings = manifest.verify(entries, partial)
        if manifest.has_errors(findings):
            raise CheckFailed(f"{name} {version}: the server's files differ from the manifest even after a full "
                              f"re-transfer ({_first(findings)}); the products server needs to be checked")
    fsutil.lock_tree(partial)
    if os.path.lexists(vdir):
        fsutil.remove_tree(old)
        os.rename(vdir, old)
    os.rename(partial, vdir)
    fsutil.remove_tree(old)
    progress.item(name, version, f"transferred and verified ({nbytes}, {timer})")


def _sync_one(paths, cfg, name, *, tool, dry_run, done, progress):
    vlist_mod.check_name(name)
    started = time.monotonic()
    os.makedirs(paths.tmp, exist_ok=True)
    tmp = tempfile.mkdtemp(dir=paths.tmp, prefix=f"sync-{name}-")
    try:
        # 1: vlist
        vrel = f"vlist/{name}{vlist_mod.SUFFIX}"
        progress.line(f"{name}: reading the vlist from {cfg.server.spec} ...")
        got = transfer.fetch_files(cfg, [vrel], tmp)
        if vrel not in got:
            raise CheckFailed(f"vlist '{name}' is not on the products server")
        data = _read(os.path.join(tmp, vrel))
        v = vlist_mod.loads(data, name=name)
        res = SyncResult(name=name, dry_run=dry_run)

        # 2: metadata
        meta = [f"modulefiles/vlist/{name}"]
        recipes = []
        for p in v.products:
            meta += [f"{p.name}/{p.version}.manifest", f"modulefiles/{p.module}"]
            recipes.append(f"{p.name}/{p.version}.recipe.yml")  # only versions built with save-recipe.py have one
        got = transfer.fetch_files(cfg, meta + recipes, tmp)
        missing = [rel for rel in meta if rel not in got]
        if missing:
            raise CheckFailed(f"the products server is missing files of vlist '{name}':\n"
                              + "\n".join(f"  - {rel}" for rel in missing))

        # 3: binaries
        progress.line(f"{name}: {len(v.products)} products")
        for p in v.products:
            key = (p.name, p.version)
            _, entries = manifest.loads(_read(os.path.join(tmp, f"{p.name}/{p.version}.manifest")))
            vr = VersionResult(p.name, p.version, "in place", len(entries))
            res.versions.append(vr)
            if key in done:
                progress.item(p.name, p.version, "in place (checked above)")
                continue
            vdir = paths.version_dir(p.name, p.version)
            if os.path.isdir(vdir) and not os.path.islink(vdir):
                timer = progress_mod.Timer()
                progress.item(p.name, p.version, f"checking the installed copy ({len(entries)} entries) ...")
                findings = manifest.verify(entries, vdir)
                if not manifest.has_errors(findings):
                    if findings and not dry_run:
                        fsutil.lock_tree(vdir)
                    progress.item(p.name, p.version, f"in place, verified ({timer})")
                    done.add(key)
                    continue
                progress.item(p.name, p.version, f"installed copy differs ({_first(findings, 1)})")
            if dry_run:
                vr.action = "would transfer"
                progress.item(p.name, p.version, "would transfer "
                              f"{progress_mod.size(progress_mod.total_size(entries))}")
                continue
            _transfer(paths, cfg, p.name, p.version, entries, progress)
            vr.action = "transferred"
            done.add(key)

        # 4: metadata in place, vlist last (<Products>/modulefiles is read-only again afterwards)
        if not dry_run:
            with fsutil.opened(paths.modulefiles):
                for rel in meta + [r for r in recipes if r in got]:
                    if rel == f"modulefiles/vlist/{name}":
                        continue
                    state.atomic_write(os.path.join(paths.products, rel), _read(os.path.join(tmp, rel)),
                                       mode=0o444)
                state.atomic_write(paths.bundle_file(name), _read(os.path.join(tmp, f"modulefiles/vlist/{name}")),
                                   mode=0o444)
            state.atomic_write(paths.vlist_file(name), data, mode=0o444)

        res.seconds = time.monotonic() - started
        if not dry_run:
            moved = [f"{x.name} {x.version}" for x in res.versions if x.action == "transferred"]
            state.record(paths, tool, "sync", vlist=name, result="ok",
                         transferred=moved, seconds=round(res.seconds, 1))
        return res
    finally:
        fsutil.remove_tree(tmp)


def _sync_packages(paths, cfg, progress):
    """Mirror the server's <Products>/packages (the system package lists) into this node's, read-only."""
    if not transfer.list_dir(cfg, "packages"):
        progress.line("packages: the server has no package lists")
        return
    os.makedirs(paths.tmp, exist_ok=True)
    tmp = tempfile.mkdtemp(dir=paths.tmp, prefix="sync-packages-")
    try:
        transfer.fetch_tree(cfg, "packages", tmp)
        old = os.path.join(paths.products, ".packages.old")
        fsutil.remove_tree(old)
        if os.path.lexists(paths.packages):
            os.rename(paths.packages, old)
        os.rename(tmp, paths.packages)  # before locking: a read-only directory cannot move to another parent
        fsutil.lock_tree(paths.packages)
        fsutil.remove_tree(old)
    finally:
        if os.path.lexists(tmp):
            fsutil.remove_tree(tmp)
    progress.line("packages: the server's package lists are in place")


def sync(paths, cfg, names, *, tool, dry_run=False, resync=False, check_environment=False,
         progress=progress_mod.SILENT):
    """resync: sync every vlist already on this node (names must be empty).
    check_environment: after each vlist, load it with setup_prod.sh and check the system packages and shared
    libraries (warnings in the result)."""
    cfg.require_role("client", "resync" if resync else "sync")
    cfg.require_server()
    with state.lock(paths, exclusive=True):
        if resync:
            names = installed_vlists(paths)
            if not names:
                raise UsageError("no vlists on this node; install one with 'prodman sync <vlist>'")
        if not names:
            raise UsageError("which vlist? (prodman sync <vlist>...)")
        if not dry_run:
            _sync_packages(paths, cfg, progress)
        done = set()
        results = []
        for name in names:
            try:
                res = _sync_one(paths, cfg, name, tool=tool, dry_run=dry_run, done=done, progress=progress)
                if check_environment and not dry_run:
                    progress.line(f"{name}: checking that it loads on this node ...")
                    errors, warnings = envcheck.environment_check(
                        paths, name, [(v.name, v.version) for v in res.versions], home=os.environ.get("HOME", ""),
                        run_ldd=True, progress=progress)
                    res.warnings += errors + warnings
                results.append(res)
            except CheckFailed as e:
                if not dry_run:
                    state.record(paths, tool, "sync", vlist=name, result="error", detail=str(e))
                raise
        return results
