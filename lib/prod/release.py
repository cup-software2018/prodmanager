"""`prod release` and `prod withdraw` on the products server.

release works in two phases:
  plan  - read and check everything, build manifests of staging versions, build modulefiles in memory.
          Nothing is written. `--check` stops here.
  write - per staging version: lock the tree, quick re-check, write buildinfo then manifest (the manifest
          makes the version frozen); write new fixed modulefiles and the bundle; finally write the vlist,
          which is the moment of release. Then remove the draft and record history.
"""

import os
import re
from dataclasses import dataclass, field
from typing import List, Optional

from . import buildinfo as buildinfo_mod
from . import fsutil, manifest, modulefile, recipe, state
from . import progress as progress_mod
from . import vlist as vlist_mod
from .errors import CheckFailed, FormatError, TreeError, UsageError
from .hashing import sha256_bytes, sha256_file
from .yamlutil import check_text, load_mapping, quote

WITHDRAWN_FORMAT = 1


@dataclass
class ProductPlan:
    name: str
    version: str
    status: str  # 'frozen' or 'staging'
    entries: Optional[list] = None  # staging: manifest entries
    manifest_data: Optional[bytes] = None
    manifest_hash: Optional[str] = None
    buildinfo_data: Optional[bytes] = None
    module: Optional[str] = None
    module_text: Optional[str] = None  # set only if the fixed modulefile is new
    module_hash: Optional[str] = None
    notes: List[str] = field(default_factory=list)


@dataclass
class ReleasePlan:
    name: str
    vlist: vlist_mod.Vlist
    products: List[ProductPlan]
    bundle_text: str
    vlist_data: bytes
    warnings: List[str] = field(default_factory=list)

    @property
    def vlist_hash(self):
        return vlist_mod.vlist_hash(self.vlist_data)


def source_roots(tool_root):
    """modulefile sources and recipes: `share/` in an installed tool, the repository root in a checkout."""
    for base in (os.path.join(tool_root, "share"), tool_root):
        mf, rc = os.path.join(base, "modulefiles"), os.path.join(base, "recipes")
        if os.path.isdir(mf) and os.path.isdir(rc):
            return mf, rc
    raise FormatError(f"no modulefiles/ and recipes/ under {tool_root}")


def _module_revisions(paths, name, version):
    """Existing fixed modulefiles of a version: [(revision number, module name)], lowest first."""
    d = os.path.join(paths.modulefiles, name)
    pat = re.compile(r"^" + re.escape(version) + r"(?:-m([0-9]+))?$")
    found = []
    try:
        entries = os.listdir(d)
    except FileNotFoundError:
        return []
    for entry in entries:
        m = pat.match(entry)
        if m:
            found.append((int(m.group(1) or 1), f"{name}/{entry}"))
    return sorted(found)


def _read_draft(paths, name, migrate):
    if migrate:
        legacy = paths.legacy_vlist_file(name)
        try:
            with open(legacy, encoding="utf-8", errors="replace") as f:
                pairs = vlist_mod.parse_legacy(f.read())
        except FileNotFoundError:
            raise UsageError(f"--migrate needs the legacy file {legacy}") from None
        except FormatError as e:
            raise FormatError(f"{legacy}: {e}") from None
        v = vlist_mod.Vlist(description=f"migrated from {name}{vlist_mod.LEGACY_SUFFIX}",
                            products=[vlist_mod.Product(n, ver) for n, ver in pairs])
        v.migrated_from = name + vlist_mod.LEGACY_SUFFIX
        return v
    draft = os.path.join(paths.drafts, name + vlist_mod.SUFFIX)
    try:
        with open(draft, "rb") as f:
            return vlist_mod.loads(f.read(), draft=True)
    except FileNotFoundError:
        raise UsageError(f"no draft {draft}; write it first") from None


def plan(paths, cfg, name, *, tool_root, tool, migrate=False, module_revs=(), now=None, host=None,
         progress=progress_mod.SILENT):
    """Check everything and prepare a release without writing anything."""
    cfg.require_role("server", "release")
    vlist_mod.check_name(name)
    now = now or state.now_iso()
    host = host or state.hostname()
    problems = []
    warnings = []

    if os.path.exists(paths.vlist_file(name)):
        raise CheckFailed(f"vlist '{name}' is already released; names are never reused")
    if os.path.exists(paths.withdrawn_file(name)):
        raise CheckFailed(f"vlist '{name}' was withdrawn; names are never reused")
    if not migrate and os.path.exists(paths.legacy_vlist_file(name)):
        raise CheckFailed(f"'{name}' is a legacy vlist name; use --migrate to register it")

    draft = _read_draft(paths, name, migrate)
    src_root, recipes_root = source_roots(tool_root)
    revs = set(module_revs)
    for r in revs:
        if r not in {f"{p.name}/{p.version}" for p in draft.products}:
            raise UsageError(f"--module-rev {r}: not a product of this vlist")

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
            pp.manifest_hash = sha256_bytes(data)
            if not os.path.exists(paths.buildinfo_file(p.name, p.version)):
                warnings.append(f"{p.name} {p.version}: frozen but has no buildinfo")
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
                                              created=f"{now} by prod {tool} on {host}")
            pp.manifest_hash = sha256_bytes(pp.manifest_data)
            try:
                rec = recipe.load(recipes_root, p.name, p.version)
                script = buildinfo_mod.script_path(recipes_root, p.name)
                info = buildinfo_mod.extract(script, vdir) if script else []
            except FormatError as e:
                problems.append(f"{p.name} {p.version}: {e}")
                continue
            pp.buildinfo_data = buildinfo_mod.dumps(
                p.name, p.version, "recipe" if rec.has_build else "extracted",
                f"recipes/{p.name}/{p.version}.yml @ {tool}",
                f"{now} by prod {tool} on {host} ({buildinfo_mod.os_name()})", info)

        # fixed modulefile
        existing = _module_revisions(paths, p.name, p.version)
        key = f"{p.name}/{p.version}"
        try:
            if key in revs or not existing:
                if key in revs and not existing:
                    raise UsageError(f"--module-rev {key}: there is no fixed modulefile to revise yet")
                n = existing[-1][0] + 1 if existing else 1
                pp.module = key if n == 1 else f"{key}-m{n}"
                pp.module_text = modulefile.make_fixed(src_root, recipes_root, paths.products, p.name, p.version,
                                                       tool=tool, host=host, time=now)
                pp.module_hash = sha256_bytes(pp.module_text.encode("utf-8"))
            else:
                pp.module = existing[-1][1]
                pp.module_hash = sha256_file(paths.module_file(pp.module))
        except FormatError as e:
            problems.append(f"{p.name} {p.version}: {e}")

    # dependencies must be in this vlist, with the same version, earlier in the list
    for i, p in enumerate(draft.products):
        try:
            rec = recipe.load(recipes_root, p.name, p.version)
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
    v = vlist_mod.Vlist(
        description=draft.description,
        released=now,
        released_by=f"prod {tool} on {host}",
        migrated_from=draft.migrated_from,
        bundle=sha256_bytes(bundle_text.encode("utf-8")),
        products=[vlist_mod.Product(pp.name, pp.version, pp.manifest_hash, pp.module, pp.module_hash)
                  for pp in plans],
    )
    return ReleasePlan(name=name, vlist=v, products=plans, bundle_text=bundle_text,
                       vlist_data=vlist_mod.dumps(v), warnings=warnings)


def write(paths, rp, *, tool, progress=progress_mod.SILENT):
    """Carry out a ReleasePlan."""
    for pp in rp.products:
        vdir = paths.version_dir(pp.name, pp.version)
        if pp.status == "staging":
            progress.item(pp.name, pp.version, "locking and writing the manifest ...")
            fsutil.lock_tree(vdir)
            changed = manifest.verify(pp.entries, vdir, quick=True)
            if manifest.has_errors(changed):
                raise CheckFailed(f"{pp.name} {pp.version} changed while releasing; nothing was frozen for it")
            state.atomic_write(paths.buildinfo_file(pp.name, pp.version), pp.buildinfo_data, mode=0o444)
            state.atomic_write(paths.manifest_file(pp.name, pp.version), pp.manifest_data, mode=0o444)
        else:
            fsutil.lock_tree(vdir)
        if pp.module_text is not None:
            os.makedirs(os.path.join(paths.modulefiles, pp.name), exist_ok=True)
            state.atomic_write(paths.module_file(pp.module), pp.module_text.encode("utf-8"), mode=0o444)
        else:
            fsutil.lock_path(paths.module_file(pp.module))
    os.makedirs(os.path.join(paths.modulefiles, "vlist"), exist_ok=True)
    state.atomic_write(paths.bundle_file(rp.name), rp.bundle_text.encode("utf-8"), mode=0o444)
    state.atomic_write(paths.vlist_file(rp.name), rp.vlist_data, mode=0o444)  # the release happens here
    draft = os.path.join(paths.drafts, rp.name + vlist_mod.SUFFIX)
    if os.path.exists(draft):
        os.unlink(draft)
    state.record(paths, tool, "release", vlist=rp.name, vlist_hash=rp.vlist_hash, result="ok",
                 detail=", ".join(f"{p.name} {p.version} ({p.status})" for p in rp.products))


def release(paths, cfg, name, *, tool_root, tool, check=False, migrate=False, module_revs=(),
            progress=progress_mod.SILENT):
    with state.lock(paths, exclusive=True):
        rp = plan(paths, cfg, name, tool_root=tool_root, tool=tool, migrate=migrate, module_revs=module_revs,
                  progress=progress)
        if not check:
            write(paths, rp, tool=tool, progress=progress)
        return rp


# --------------------------------------------------------------------------- withdraw


def withdrawn_dumps(time, by, reason, replaced_by=None):
    lines = [f"format: {quote(str(WITHDRAWN_FORMAT))}", f"withdrawn: {quote(time)}", f"withdrawn_by: {quote(by)}",
             f"reason: {quote(reason)}"]
    if replaced_by:
        lines.append(f"replaced_by: {quote(replaced_by)}")
    return ("\n".join(lines) + "\n").encode("utf-8")


def withdrawn_loads(data):
    doc = load_mapping(data, "withdrawn record")
    return {k: check_text(k, doc.get(k, "")) for k in ("withdrawn", "withdrawn_by", "reason", "replaced_by")}


def withdraw(paths, cfg, name, *, tool, reason, replaced_by=None):
    """Mark a released vlist as withdrawn. The vlist itself is not touched."""
    cfg.require_role("server", "withdraw")
    vlist_mod.check_name(name)
    check_text("reason", reason)
    if not reason.strip():
        raise UsageError("--reason must not be empty")
    with state.lock(paths, exclusive=True):
        if not os.path.exists(paths.vlist_file(name)):
            raise CheckFailed(f"vlist '{name}' is not released")
        if os.path.exists(paths.withdrawn_file(name)):
            raise CheckFailed(f"vlist '{name}' is already withdrawn")
        if replaced_by is not None:
            vlist_mod.check_name(replaced_by)
            if replaced_by == name or not os.path.exists(paths.vlist_file(replaced_by)):
                raise CheckFailed(f"--replaced-by '{replaced_by}' is not another released vlist")
        data = withdrawn_dumps(state.now_iso(), f"prod {tool} on {state.hostname()}", reason, replaced_by)
        state.atomic_write(paths.withdrawn_file(name), data, mode=0o444)
        state.record(paths, tool, "withdraw", vlist=name, result="ok", reason=reason,
                     replaced_by=replaced_by or "")
