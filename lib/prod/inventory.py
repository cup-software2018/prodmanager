"""`prodman list`, `prodman remove`, `prodman gc` (on clients and on the server).

What prod manages under Products: `.prod/`, `packages/`, `vlist/<name>.yml`, `vlist/drafts/` (server),
`modulefiles/`, and per product `<VER>/`, `<VER>.manifest`, `<VER>.recipe.yml` for versions that have a
manifest. Everything else is "unmanaged" and is never deleted by prod. A product-named directory with
no version-like entry (V...) in it is listed as one entry, "<DIR>/", instead of entry by entry.
"""

import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from . import fsutil, manifest, state, transfer
from . import vlist as vlist_mod
from .errors import CheckFailed, FormatError
from .verify import frozen_versions, installed_vlists


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
        if top in (".prod", "modulefiles", "packages"):
            continue
        if top == "vlist":
            for n in sorted(os.listdir(full)):
                if not (n.endswith(vlist_mod.SUFFIX) or n == "drafts"):
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
            for suffix in (manifest.SUFFIX, ".recipe.yml"):
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


def not_released(paths):
    """[(name, version)] of version directories that have no manifest: built (staging), not released yet."""
    frozen = set(frozen_versions(paths))
    result = []
    for top in sorted(os.listdir(paths.products)):
        full = os.path.join(paths.products, top)
        if not (vlist_mod.PRODUCT_RE.match(top) and os.path.isdir(full) and not os.path.islink(full)):
            continue
        for n in sorted(os.listdir(full)):
            p = os.path.join(full, n)
            if (vlist_mod.VERSION_RE.match(n) and os.path.isdir(p) and not os.path.islink(p)
                    and (top, n) not in frozen):
                result.append((top, n))
    return result


@dataclass
class Listing:
    tool: str
    role: str
    server: Optional[str]
    vlists: List[dict] = field(default_factory=list)
    versions: List[dict] = field(default_factory=list)
    not_released: List[dict] = field(default_factory=list)
    leftovers: List[str] = field(default_factory=list)
    remote: Optional[List[dict]] = None

    def as_dict(self):
        return dict(self.__dict__)


def listing(paths, cfg, *, tool, remote=False):
    vlists = _local_vlists(paths)
    users, _ = _users(vlists)
    history = state.read_history(paths)
    lst = Listing(tool=tool, role=cfg.role, server=cfg.server.spec if cfg.server else None)
    for name in installed_vlists(paths):
        events = [e for e in history if e.get("vlist") == name or name in e.get("vlists", [])]
        syncs = [e for e in events if e.get("command") == "sync" and e.get("result") == "ok"]
        verifies = [e for e in events if e.get("command") == "verify"]
        lst.vlists.append({
            "name": name,
            "description": vlists[name].description if name in vlists else "(unreadable)",
            "first_sync": syncs[0]["time"] if syncs else None,
            "last_sync": syncs[-1]["time"] if syncs else None,
            "last_verify": (verifies[-1]["time"] + " " + verifies[-1].get("result", "")) if verifies else None,
        })
    for name, version in frozen_versions(paths):
        lst.versions.append({"name": name, "version": version, "used_by": sorted(users.get((name, version), []))})
    lst.not_released = [{"name": n, "version": v} for n, v in not_released(paths)]
    _, lst.leftovers = unmanaged(paths)
    if remote and cfg.server is not None:
        names = transfer.list_dir(cfg, "vlist")
        lst.remote = [{"name": n[: -len(vlist_mod.SUFFIX)]} for n in names if n.endswith(vlist_mod.SUFFIX)]
    return lst


def _vlist_files(paths, name):
    return [p for p in (paths.vlist_file(name), paths.bundle_file(name)) if os.path.lexists(p)]


def _version_files(paths, name, version):
    return [p for p in (paths.version_dir(name, version), paths.manifest_file(name, version),
                        paths.recipe_file(name, version), paths.module_file(f"{name}/{version}"))
            if os.path.lexists(p)]


def _parse_target(target):
    """'1.0' -> ('1.0', None); 'ROOT/V6.32.24_3.11.14' -> ('ROOT', 'V6.32.24_3.11.14')."""
    if "/" not in target:
        return vlist_mod.check_name(target), None
    name, _, version = target.partition("/")
    if not vlist_mod.PRODUCT_RE.match(name) or not vlist_mod.VERSION_RE.match(version):
        raise FormatError(f"'{target}' is not a vlist name or <NAME>/<VERSION>")
    return name, version


def remove(paths, cfg, targets, *, tool, yes=False):
    """Delete vlists (`1.0`) and product versions (`ROOT/V6.32.24_3.11.14`) from this node, only with yes.
    Returns the files that were (or, without yes, would be) deleted.

    vlist:   the vlist file and its bundle modulefile. The versions stay (prodman gc deletes those no vlist uses).
             On the server the draft stays.
    version: the version directory, its manifest, saved recipe and modulefile. Refused while a vlist that is
             not removed in the same command uses it.
    """
    with state.lock(paths, exclusive=True):
        parsed = [(t, *_parse_target(t)) for t in targets]
        vlist_names = {name for _, name, version in parsed if version is None}
        users, _ = _users(_local_vlists(paths))
        files = []
        for target, name, version in parsed:
            if version is None:
                if not os.path.exists(paths.vlist_file(name)):
                    raise FormatError(f"vlist '{name}' is not on this node")
                files += _vlist_files(paths, name)
                continue
            found = _version_files(paths, name, version)
            if not found:
                raise FormatError(f"{target} is not on this node")
            used = [v for v in users.get((name, version), []) if v not in vlist_names]
            if used:
                raise CheckFailed(f"{target} is used by vlist {', '.join(used)}; remove the vlist first")
            files += found
        if yes:
            with fsutil.opened(paths.modulefiles):  # the modulefiles are in it
                for path in files:
                    fsutil.remove_tree(path)
            state.record(paths, tool, "remove", targets=list(targets), result="ok")
        return files


def gc_candidates(paths):
    """What gc would delete: unused versions with their metadata, unused fixed modulefiles and bundles,
    leftover .partial/.old directories and temporary files. Unmanaged paths are never included."""
    vlists = _local_vlists(paths)
    users, modules = _users(vlists)
    out = []
    for name, version in frozen_versions(paths):
        if (name, version) not in users:
            for path in (paths.version_dir(name, version), paths.manifest_file(name, version),
                         paths.recipe_file(name, version)):
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
    """Delete what gc_candidates lists (only with yes). On the server this deletes released versions that no
    vlist uses any more, after `prodman remove`; staging versions (no manifest) are never touched."""
    with state.lock(paths, exclusive=True):
        candidates = gc_candidates(paths)
        if yes:
            with fsutil.opened(paths.modulefiles):
                for path in candidates:
                    fsutil.remove_tree(path)
            state.record(paths, tool, "gc", result="ok", removed=[os.path.relpath(p, paths.products)
                                                                   for p in candidates])
        return candidates
