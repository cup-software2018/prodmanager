"""`prodman release` on the products server.

release works in two phases:
  plan  - read and check everything, build manifests of staging versions, build modulefiles in memory.
          Nothing is written. `--check` stops here.
  write - per staging version: lock the tree and its saved recipe (<VER>.recipe.yml), quick re-check, write
          the manifest (the manifest makes the version frozen); write new fixed modulefiles and the bundle;
          finally write the vlist, which is the moment of release. Then record history. The draft stays
          (vlist/drafts/<name>.yml: a start for the next release).

A product's modulefile is `<NAME>/<VERSION>`. It is made at the first release of that version and is used as it
is by every later release: released files never change.

A draft lists products in any order: release orders them by their recipes' depends (dependencies first; products
with no dependency relation in the vlist last; the draft's order otherwise). A draft names a version as people think of it, e.g. CUPDATAMODEL V1.4. The version
released is the one built version among V1.4 and V1.4.<x> whose recipe's depends are all in the vlist
(resolve_versions), so V1.4.1 (built with ROOT 6.32) and V1.4.2 (built with ROOT 6.40) are both written V1.4 in
drafts.
"""

import os
import re
import tempfile
from dataclasses import dataclass, field
from typing import List, Optional

from . import envcheck, fsutil, manifest, modulefile, recipe, state
from . import progress as progress_mod
from . import vlist as vlist_mod
from .errors import CheckFailed, FormatError, TreeError, UsageError


@dataclass
class ProductPlan:
    name: str
    version: str
    status: str  # 'frozen' or 'staging'
    entries: Optional[list] = None  # staging: manifest entries
    manifest_data: Optional[bytes] = None
    module_text: Optional[str] = None  # set only if the modulefile is new
    notes: List[str] = field(default_factory=list)

    @property
    def module(self):
        return f"{self.name}/{self.version}"


@dataclass
class ReleasePlan:
    name: str
    vlist: vlist_mod.Vlist
    products: List[ProductPlan]
    bundle_text: str
    vlist_data: bytes
    warnings: List[str] = field(default_factory=list)


_LEFTOVER_SUFFIXES = (".partial", ".old")


def _candidates(paths, name, version):
    """Built versions that a draft's `version` may mean: the version itself and <version>.<x>."""
    pdir = os.path.join(paths.products, name)
    try:
        entries = os.listdir(pdir)
    except FileNotFoundError:
        return []
    found = [e for e in entries
             if (e == version or e.startswith(version + "."))
             and vlist_mod.VERSION_RE.match(e) and not e.endswith(_LEFTOVER_SUFFIXES)
             and os.path.isdir(os.path.join(pdir, e)) and not os.path.islink(os.path.join(pdir, e))]
    return sorted(found, key=lambda e: [int(x) if x.isdigit() else x for x in re.split(r"(\d+)", e)])


def _depends(paths, recipes_root, name, version):
    try:
        return recipe.find(paths.products, recipes_root, name, version).depends
    except FormatError:
        return None  # reported by the checks of plan()


def resolve_versions(paths, recipes_root, products):
    """[Product] of a draft -> ([Product] with the built versions to release, in load order; [problem]).

    Each draft version is matched against the built versions <version> and <version>.<x> (_candidates); the one
    whose recipe's depends are all among the products already placed is taken. Placing is repeated, the first
    product of the draft that can be placed first, so dependencies come before the products that need them and
    the draft's order is kept otherwise. Then the products with no dependency relation in the vlist (they need
    none and none needs them) move to the end. What cannot be placed goes last, in the draft's order: with one
    candidate (or none) as it is, so the usual checks report what is wrong with it."""
    cands = {p.name: _candidates(paths, p.name, p.version) for p in products}
    deps = {(p.name, c): _depends(paths, recipes_root, p.name, c) for p in products for c in cands[p.name]}
    placed, waiting = [], list(products)

    def fits(p):
        done = {(q.name, q.version) for q in placed}
        return [c for c in cands[p.name]
                if deps[(p.name, c)] is not None and all(d in done for d in deps[(p.name, c)])]

    while True:
        ready = next((p for p in waiting if len(fits(p)) == 1), None)
        if ready is None:
            break
        placed.append(vlist_mod.Product(ready.name, fits(ready)[0]))
        waiting.remove(ready)

    needed = {d for q in placed for d in deps[(q.name, q.version)]}
    alone = [q for q in placed if not deps[(q.name, q.version)] and (q.name, q.version) not in needed]
    placed = [q for q in placed if q not in alone] + alone

    problems = []
    for p in waiting:
        found, ok = cands[p.name], fits(p)
        if len(found) <= 1:
            placed.append(vlist_mod.Product(p.name, found[0] if found else p.version))
            continue
        placed.append(vlist_mod.Product(p.name, p.version))
        if ok:
            problems.append(f"{p.name} {p.version}: more than one built version fits this vlist: {', '.join(ok)}")
        else:
            problems.append(f"{p.name} {p.version}: none of the built versions {', '.join(found)} has all its "
                            f"depends in this vlist")
    return placed, problems


def modulefile_sources(tool_root):
    """The modulefile templates: `share/modulefiles` in an installed tool, `modulefiles` in a checkout."""
    for base in (os.path.join(tool_root, "share"), tool_root):
        mf = os.path.join(base, "modulefiles")
        if os.path.isdir(mf):
            return mf
    raise FormatError(f"no modulefiles/ under {tool_root}")


def _read_draft(paths, name):
    draft = os.path.join(paths.drafts, name + vlist_mod.SUFFIX)
    try:
        with open(draft, "rb") as f:
            return vlist_mod.loads(f.read(), draft=True)
    except FileNotFoundError:
        raise UsageError(f"no draft {draft}; write it first") from None


def _try_load(paths, name, plans, bundle_text, progress):
    """(errors, warnings): load the new vlist from its new modulefiles, written to a temporary directory in front
    of <Products>/modulefiles, before anything is released; then system packages and shared libraries."""
    os.makedirs(paths.tmp, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=paths.tmp, prefix=f"release-{name}-") as tmp:
        for pp in plans:
            if pp.module_text is not None:
                path = os.path.join(tmp, *pp.module.split("/"))
                os.makedirs(os.path.dirname(path), exist_ok=True)
                with open(path, "w", encoding="utf-8") as f:
                    f.write(pp.module_text)
        os.makedirs(os.path.join(tmp, "vlist"))
        with open(os.path.join(tmp, "vlist", name), "w", encoding="utf-8") as f:
            f.write(bundle_text)
        progress.line(f"{name}: loading the vlist with its new modulefiles ...")
        modulepath = [tmp] + ([paths.modulefiles] if os.path.isdir(paths.modulefiles) else [])
        env, err = envcheck.module_environment(modulepath, name)
        if env is None:
            return [f"the vlist does not load: " + " / ".join(err.splitlines()[-3:])], []
        versions = [(pp.name, pp.version) for pp in plans]
        warnings = envcheck.package_warnings(paths, {pp.name for pp in plans})
        warnings += envcheck.ldd_warnings(paths.products, versions, env, progress)
        return [], warnings


def plan(paths, cfg, name, *, tool_root, tool, now=None, host=None, check_environment=False,
         progress=progress_mod.SILENT):
    """Check everything and prepare a release without writing anything."""
    cfg.require_role("server", "release")
    vlist_mod.check_name(name)
    now = now or state.now_iso()
    host = host or state.hostname()
    problems = []
    warnings = []

    if os.path.exists(paths.vlist_file(name)):
        raise CheckFailed(f"vlist '{name}' is already released")

    draft = _read_draft(paths, name)
    src_root = modulefile_sources(tool_root)
    recipes_root = None  # only the recipes saved next to the versions (build.sh, save-recipe.py) are used
    asked = {p.name: p.version for p in draft.products}
    draft.products, problems = resolve_versions(paths, recipes_root, draft.products)
    for p in draft.products:
        if p.version != asked[p.name]:
            progress.line(f"  {p.name} {asked[p.name]} -> {p.version}")
    if problems:
        raise CheckFailed(f"cannot release '{name}':\n" + "\n".join(f"  - {x}" for x in problems))

    plans = []
    progress.line(f"{name}: {len(draft.products)} products")
    for p in draft.products:
        timer = progress_mod.Timer()
        pp = ProductPlan(name=p.name, version=p.version, status="staging")
        plans.append(pp)
        vdir = paths.version_dir(p.name, p.version)
        mpath = paths.manifest_file(p.name, p.version)
        if not os.path.isdir(vdir) or os.path.islink(vdir):
            problems.append(f"{p.name} {p.version}: no version directory {vdir}")
            continue
        if os.path.exists(mpath):
            pp.status = "frozen"
            with open(mpath, "rb") as f:
                data = f.read()
            _, entries = manifest.loads(data)
            progress.item(p.name, p.version, f"frozen: verifying {len(entries)} entries ...")
            findings = manifest.verify(entries, vdir)
            progress.item(p.name, p.version, f"frozen: {'differs' if manifest.has_errors(findings) else 'ok'} "
                                             f"({timer})")
            errors = [f for f in findings if f.level == "error"]
            if errors:
                shown = "; ".join(f"{f.path}: {f.message}" for f in errors[:5])
                more = f" (and {len(errors) - 5} more)" if len(errors) > 5 else ""
                problems.append(f"{p.name} {p.version} is frozen but differs from its manifest: {shown}{more}")
                continue
            if findings:
                pp.notes.append("frozen version had write bits set; it will be locked again")
        else:
            progress.item(p.name, p.version, "staging: hashing every file ...")
            try:
                pp.entries = manifest.build(vdir)
            except TreeError as e:
                problems.append(f"{p.name} {p.version}: {e}")
                continue
            progress.item(p.name, p.version, f"staging: {len(pp.entries)} entries, "
                                             f"{progress_mod.size(progress_mod.total_size(pp.entries))} ({timer})")
            susp = fsutil.suspicious_names(vdir)
            if susp:
                shown = ", ".join(susp[:3]) + (f", ... ({len(susp)} in all)" if len(susp) > 3 else "")
                warnings.append(f"{p.name} {p.version}: {len(susp)} suspicious file(s) such as editor backups, "
                                f"archives or core dumps; check them before release: {shown}")
            pp.manifest_data = manifest.dumps(pp.entries, product=p.name, version=p.version,
                                              created=f"{now} by prodman {tool} on {host}")
            try:
                rec = recipe.find(paths.products, recipes_root, p.name, p.version)
            except FormatError as e:
                problems.append(f"{p.name} {p.version}: {e}")
                continue
            pp.notes.append(f"recipe {rec.origin(tool)}")

        # modulefile: made at the first release of the version, then kept as it is
        if not os.path.exists(paths.module_file(pp.module)):
            try:
                pp.module_text = modulefile.make_fixed(src_root, recipes_root, paths.products, p.name, p.version,
                                                       tool=tool, host=host, time=now)
            except FormatError as e:
                problems.append(f"{p.name} {p.version}: {e}")

    # dependencies must be in this vlist, with the same version (resolve_versions put them earlier)
    for i, p in enumerate(draft.products):
        try:
            rec = recipe.find(paths.products, recipes_root, p.name, p.version)
        except FormatError:
            continue  # already reported
        earlier = {(q.name, q.version) for q in draft.products[:i]}
        names = {q.name: q.version for q in draft.products}
        for dname, dver in rec.depends:
            if (dname, dver) in earlier:
                continue
            if names.get(dname) == dver:
                problems.append(f"{p.name} {p.version} needs {dname} {dver}, which must come before it in the list")
            else:
                problems.append(f"{p.name} {p.version} needs {dname} {dver}, which is not in this vlist")

    if problems:
        raise CheckFailed(f"cannot release '{name}':\n" + "\n".join(f"  - {x}" for x in problems))

    modules = [pp.module for pp in plans]
    bundle_text = modulefile.make_bundle(name, draft.description, modules, tool=tool, host=host, time=now)
    if check_environment:
        errors, env_warnings = _try_load(paths, name, plans, bundle_text, progress)
        if errors:
            raise CheckFailed(f"cannot release '{name}':\n" + "\n".join(f"  - {x}" for x in errors))
        warnings += env_warnings
    v = vlist_mod.Vlist(
        description=draft.description,
        released=now,
        released_by=f"prodman {tool} on {host}",
        products=[vlist_mod.Product(pp.name, pp.version) for pp in plans],
    )
    return ReleasePlan(name=name, vlist=v, products=plans, bundle_text=bundle_text,
                       vlist_data=vlist_mod.dumps(v), warnings=warnings)


def _lock_saved_recipe(paths, pp):
    path = paths.recipe_file(pp.name, pp.version)
    if os.path.lexists(path):
        fsutil.lock_path(path)


def write(paths, rp, *, tool, progress=progress_mod.SILENT):
    """Carry out a ReleasePlan. <Products>/modulefiles is read-only again when it returns."""
    with fsutil.opened(paths.modulefiles):
        _write(paths, rp, tool=tool, progress=progress)


def _write(paths, rp, *, tool, progress):
    for pp in rp.products:
        vdir = paths.version_dir(pp.name, pp.version)
        if pp.status == "staging":
            progress.item(pp.name, pp.version, "locking and writing the manifest ...")
            fsutil.lock_tree(vdir)
            changed = manifest.verify(pp.entries, vdir, quick=True)
            if manifest.has_errors(changed):
                raise CheckFailed(f"{pp.name} {pp.version} changed while releasing; nothing was frozen for it")
            _lock_saved_recipe(paths, pp)
            state.atomic_write(paths.manifest_file(pp.name, pp.version), pp.manifest_data, mode=0o444)
        else:
            fsutil.lock_tree(vdir)
            _lock_saved_recipe(paths, pp)
        if pp.module_text is not None:
            os.makedirs(os.path.join(paths.modulefiles, pp.name), exist_ok=True)
            state.atomic_write(paths.module_file(pp.module), pp.module_text.encode("utf-8"), mode=0o444)
        else:
            fsutil.lock_path(paths.module_file(pp.module))
    os.makedirs(os.path.join(paths.modulefiles, "vlist"), exist_ok=True)
    state.atomic_write(paths.bundle_file(rp.name), rp.bundle_text.encode("utf-8"), mode=0o444)
    state.atomic_write(paths.vlist_file(rp.name), rp.vlist_data, mode=0o444)  # the release happens here
    state.record(paths, tool, "release", vlist=rp.name, result="ok",
                 detail=", ".join(f"{p.name} {p.version} ({p.status})" for p in rp.products))


def release(paths, cfg, name, *, tool_root, tool, check=False, check_environment=False,
            progress=progress_mod.SILENT):
    with state.lock(paths, exclusive=True):
        rp = plan(paths, cfg, name, tool_root=tool_root, tool=tool, check_environment=check_environment,
                  progress=progress)
        if not check:
            write(paths, rp, tool=tool, progress=progress)
        return rp

