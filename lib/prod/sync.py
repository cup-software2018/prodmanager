"""`prod sync` on a client.

Per vlist:
  1. fetch the vlist (and .withdrawn) into a temporary directory
  2. check the format and compare the vlist hash with the trusted record (first sync: record it)
  3. stop if withdrawn (unless --force)
  4. fetch manifests, buildinfo, fixed modulefiles and the bundle; check them against the vlist hashes
  5. per version: keep it if it already matches its manifest; otherwise mirror it into
     `<NAME>/.<VER>.partial` (reusing unchanged files with --link-dest), verify it completely, lock it,
     and swap it in with renames (running processes keep the old inodes)
  6. put the metadata in place, the vlist file last: once the vlist is visible, everything it needs is there
  7. record history
"""

import os
import tempfile
import time
from dataclasses import dataclass, field
from typing import List, Optional

from . import fsutil, manifest, state, transfer
from . import progress as progress_mod
from . import vlist as vlist_mod
from .errors import CheckFailed, UsageError
from .hashing import sha256_file
from .release import withdrawn_loads
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
    vlist_hash: str
    versions: List[VersionResult] = field(default_factory=list)
    first_sync: bool = False
    retrusted: bool = False
    withdrawn: Optional[dict] = None
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
    """Mirror one version from the server and swap it in (step 5)."""
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


def _sync_one(paths, cfg, name, *, tool, dry_run, force, retrust, done, progress):
    vlist_mod.check_name(name)
    started = time.monotonic()
    os.makedirs(paths.tmp, exist_ok=True)
    tmp = tempfile.mkdtemp(dir=paths.tmp, prefix=f"sync-{name}-")
    try:
        # 1-3: vlist, trust, withdrawal
        vrel, wrel = f"vlist/{name}{vlist_mod.SUFFIX}", f"vlist/{name}.withdrawn"
        progress.line(f"{name}: reading the vlist from {cfg.server.spec} ...")
        got = transfer.fetch_files(cfg, [vrel, wrel], tmp)
        if vrel not in got:
            raise CheckFailed(f"vlist '{name}' is not on the products server")
        data = _read(os.path.join(tmp, vrel))
        v = vlist_mod.loads(data, name=name)
        res = SyncResult(name=name, vlist_hash=vlist_mod.vlist_hash(data), dry_run=dry_run)
        trusted = state.get_trusted(paths, name)
        res.first_sync = trusted is None
        if trusted is not None and trusted != res.vlist_hash:
            if not retrust:
                raise CheckFailed(
                    f"the server's vlist '{name}' differs from the one first synced on this node\n"
                    f"  first synced: {trusted}\n  server now:   {res.vlist_hash}\n"
                    f"released vlists must never change; ask the products server admin. If the change is "
                    f"legitimate, run 'prod sync --retrust {name}'")
            res.retrusted = True
        if wrel in got:
            res.withdrawn = withdrawn_loads(_read(os.path.join(tmp, wrel)))
            if not force:
                hint = f"; use {res.withdrawn['replaced_by']} instead" if res.withdrawn.get("replaced_by") else ""
                raise CheckFailed(f"vlist '{name}' was withdrawn: {res.withdrawn['reason']}{hint} "
                                  f"(--force installs it anyway)")

        # 4: metadata
        meta = {f"modulefiles/vlist/{name}": v.bundle}
        buildinfos = []
        for p in v.products:
            meta[f"{p.name}/{p.version}.manifest"] = p.manifest
            meta[f"modulefiles/{p.module}"] = p.modulefile
            buildinfos.append(f"{p.name}/{p.version}.buildinfo")
        got = transfer.fetch_files(cfg, list(meta) + buildinfos, tmp)
        bad = []
        for rel, expected in meta.items():
            if rel not in got:
                bad.append(f"{rel} is missing")
            elif sha256_file(os.path.join(tmp, rel)) != expected:
                bad.append(f"{rel} does not match the vlist")
        if bad:
            raise CheckFailed(f"the products server is inconsistent for vlist '{name}':\n"
                              + "\n".join(f"  - {b}" for b in bad))
        res.warnings += [f"{b} is missing on the server" for b in buildinfos if b not in got]

        if not dry_run and (res.first_sync or res.retrusted):
            state.set_trusted(paths, name, res.vlist_hash)
            if res.retrusted:
                state.record(paths, tool, "retrust", vlist=name, old_hash=trusted, new_hash=res.vlist_hash)

        # 5: binaries
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

        # 6: metadata in place, vlist last
        if not dry_run:
            for rel in list(meta) + [b for b in buildinfos if b in got]:
                if rel == f"modulefiles/vlist/{name}":
                    continue
                state.atomic_write(os.path.join(paths.products, rel), _read(os.path.join(tmp, rel)), mode=0o444)
            state.atomic_write(paths.bundle_file(name), _read(os.path.join(tmp, f"modulefiles/vlist/{name}")),
                               mode=0o444)
            if res.withdrawn is not None:
                state.atomic_write(paths.withdrawn_file(name), _read(os.path.join(tmp, wrel)), mode=0o444)
            state.atomic_write(paths.vlist_file(name), data, mode=0o444)

        res.seconds = time.monotonic() - started
        if not dry_run:
            moved = [f"{x.name} {x.version}" for x in res.versions if x.action == "transferred"]
            state.record(paths, tool, "sync", vlist=name, vlist_hash=res.vlist_hash, result="ok",
                         transferred=moved, seconds=round(res.seconds, 1))
        return res
    finally:
        fsutil.remove_tree(tmp)


def sync(paths, cfg, names, *, tool, dry_run=False, force=False, retrust=False, installed=False,
         progress=progress_mod.SILENT):
    cfg.require_role("client", "sync")
    cfg.require_server()
    with state.lock(paths, exclusive=True):
        if installed:
            if names:
                raise UsageError("give vlist names or --installed, not both")
            names = installed_vlists(paths)
            if not names:
                raise UsageError("no vlists are installed on this node")
        if not names:
            raise UsageError("which vlist? (prod sync <vlist>... or prod sync --installed)")
        done = set()
        results = []
        for name in names:
            try:
                results.append(_sync_one(paths, cfg, name, tool=tool, dry_run=dry_run, force=force,
                                         retrust=retrust, done=done, progress=progress))
            except CheckFailed as e:
                if not dry_run:
                    state.record(paths, tool, "sync", vlist=name, result="error", detail=str(e))
                raise
        return results
