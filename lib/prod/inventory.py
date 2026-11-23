"""`prod list`, `prod remove`, `prod gc`.

What prod manages under Products: `.prod/`, `vlist/<name>.yml|.withdrawn`, `vlist/drafts/` (server),
`modulefiles/`, and per product `<VER>/`, `<VER>.manifest`, `<VER>.buildinfo`, `<VER>.recipe.yml` for versions
that have a manifest. Everything else is "unmanaged" and is never deleted by prod. A product-named directory with
no version-like entry (V...) in it is listed as one entry, "<DIR>/", instead of entry by entry.
"""

import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from . import fsutil, manifest, state, transfer
from . import vlist as vlist_mod
from .errors import FormatError
from .verify import frozen_versions, installed_vlists, read_withdrawn


def _local_vlists(paths):
    """{name: Vlist} for every readable local vlist; broken ones are skipped (verify reports them)."""
    result = {}
    for name in installed_vlists(paths):
        try:
            result[name] = vlist_mod.load(paths.vlist_file(name))
        except FormatError:
            continue
    return result


def _users(vlists):
    """{(name, version): [vlist names]} and the set of referenced fixed modulefiles."""
    users: Dict = {}
    modules = set()
    for vname, v in vlists.items():
        for p in v.products:
            users.setdefault((p.name, p.version), []).append(vname)
            modules.add(p.module)
    return users, modules


def unmanaged(paths):
    """Paths under Products (relative) that prod does not manage, and leftover .partial/.old directories."""
    found, leftovers = [], []
    frozen = set(frozen_versions(paths))
    for top in sorted(os.listdir(paths.products)):
        full = os.path.join(paths.products, top)
        if top in (".prod", "modulefiles"):
            continue
        if top == "vlist":
            for n in sorted(os.listdir(full)):
                if not (n.endswith(vlist_mod.SUFFIX) or n.endswith(".withdrawn") or n == "drafts"):
                    found.append(f"vlist/{n}")
            continue
        if not (os.path.isdir(full) and not os.path.islink(full) and vlist_mod.PRODUCT_RE.match(top)):
            found.append(top)
            continue
        rest, versionish = [], False
        for n in sorted(os.listdir(full)):
            if n.startswith(".") and (n.endswith(".partial") or n.endswith(".old")):
                leftovers.append(f"{top}/{n}")
                continue
            base = n
            for suffix in (manifest.SUFFIX, ".buildinfo", ".recipe.yml"):
                if n.endswith(suffix):
                    base = n[: -len(suffix)]
            versionish = versionish or bool(vlist_mod.VERSION_RE.match(base))
            if (top, base) not in frozen:
                rest.append(f"{top}/{n}")
        if rest and not versionish:
            # No version directories at all (e.g. a git checkout of pyenv): one line for the whole directory.
            found.append(f"{top}/")
        else:
            found += rest
    return found, leftovers


@dataclass
class Listing:
    tool: str
    role: str
    server: Optional[str]
    vlists: List[dict] = field(default_factory=list)
    versions: List[dict] = field(default_factory=list)
    unmanaged: List[str] = field(default_factory=list)
    leftovers: List[str] = field(default_factory=list)
    packages: Optional[str] = None
    remote: Optional[List[dict]] = None

    def as_dict(self):
        return dict(self.__dict__)


def listing(paths, cfg, *, tool, remote=False):
    vlists = _local_vlists(paths)
    users, _ = _users(vlists)
    history = state.read_history(paths)
    lst = Listing(tool=tool, role=cfg.role, server=cfg.server.spec if cfg.server else None)
    for name in installed_vlists(paths):
        with open(paths.vlist_file(name), "rb") as f:
            h = vlist_mod.vlist_hash(f.read())
        trusted = state.get_trusted(paths, name) if cfg.role == "client" else None
        events = [e for e in history if e.get("vlist") == name or name in e.get("vlists", [])]
        syncs = [e for e in events if e.get("command") == "sync" and e.get("result") == "ok"]
        verifies = [e for e in events if e.get("command") == "verify"]
        withdrawn = read_withdrawn(paths.withdrawn_file(name))
        lst.vlists.append({
            "name": name,
            "hash": h,
            "trusted": None if cfg.role != "client" else (trusted == h),
            "withdrawn": withdrawn,
            "description": vlists[name].description if name in vlists else "(unreadable)",
            "first_sync": syncs[0]["time"] if syncs else None,
            "last_sync": syncs[-1]["time"] if syncs else None,
            "last_verify": (verifies[-1]["time"] + " " + verifies[-1].get("result", "")) if verifies else None,
        })
    for name, version in frozen_versions(paths):
        lst.versions.append({"name": name, "version": version, "used_by": sorted(users.get((name, version), []))})
    lst.unmanaged, lst.leftovers = unmanaged(paths)
    if os.path.exists(paths.packages_record):
        lst.packages = paths.packages_record
    if remote and cfg.server is not None:
        names = transfer.list_dir(cfg, "vlist")
        lst.remote = [{"name": n[: -len(vlist_mod.SUFFIX)],
                       "withdrawn": n[: -len(vlist_mod.SUFFIX)] + ".withdrawn" in names}
                      for n in names if n.endswith(vlist_mod.SUFFIX)]
    return lst


def remove(paths, cfg, names, *, tool):
    """Take vlists off this node. The trusted records stay."""
    cfg.require_role("client", "remove")
    with state.lock(paths, exclusive=True):
        for name in names:
            vlist_mod.check_name(name)
            if not os.path.exists(paths.vlist_file(name)):
                raise FormatError(f"vlist '{name}' is not on this node")
        for name in names:
            for path in (paths.vlist_file(name), paths.withdrawn_file(name), paths.bundle_file(name)):
                if os.path.lexists(path):
                    os.unlink(path)
            state.record(paths, tool, "remove", vlist=name, result="ok")


def gc_candidates(paths):
    """What gc would delete: unused versions with their metadata, unused fixed modulefiles and bundles,
    leftover .partial/.old directories and temporary files. Unmanaged paths are never included."""
    vlists = _local_vlists(paths)
    users, modules = _users(vlists)
    out = []
    for name, version in frozen_versions(paths):
        if (name, version) not in users:
            for path in (paths.version_dir(name, version), paths.manifest_file(name, version),
                         paths.buildinfo_file(name, version), paths.recipe_file(name, version)):
                if os.path.lexists(path):
                    out.append(path)
    if os.path.isdir(paths.modulefiles):
        for name in sorted(os.listdir(paths.modulefiles)):
            d = os.path.join(paths.modulefiles, name)
            if not os.path.isdir(d):
                continue
            for f in sorted(os.listdir(d)):
                used = (f in vlists) if name == "vlist" else (f"{name}/{f}" in modules)
                if not used:
                    out.append(os.path.join(d, f))
    _, leftovers = unmanaged(paths)
    out += [os.path.join(paths.products, x) for x in leftovers]
    if os.path.isdir(paths.tmp):
        out += [os.path.join(paths.tmp, x) for x in sorted(os.listdir(paths.tmp))]
    return out


def gc(paths, cfg, *, tool, yes=False):
    cfg.require_role("client", "gc")
    with state.lock(paths, exclusive=True):
        candidates = gc_candidates(paths)
        if yes:
            for path in candidates:
                fsutil.remove_tree(path)
            state.record(paths, tool, "gc", result="ok", removed=[os.path.relpath(p, paths.products)
                                                                   for p in candidates])
        return candidates
