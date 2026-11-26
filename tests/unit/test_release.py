import io
import os
import shutil
import stat
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout

from prod import cli, config, fsutil, inventory, manifest, release, state
from prod import vlist as vlist_mod
from prod.errors import CheckFailed, EnvError, FormatError
from prod.hashing import sha256_file

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

TOOL = "v0.1-test"


def template(name, home):
    return (f"#%Module\n@HEADER@\nmodule-whatis \"{name} @VERSION@\"\nconflict {name}\n@DEPENDS@\n"
            f"setenv {home} @PREFIX@\nprepend-path PATH @PREFIX@/bin\n")


class FakeServer(unittest.TestCase):
    """A fake tool checkout and a fake server Products directory under a temp directory."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="prod-test-")
        self.tool_root = os.path.join(self.tmp, "tool")
        self.products = os.path.join(self.tmp, "Products")
        self.paths = config.Paths(self.products)
        with open(self.w(self.tool_root, "VERSION"), "w") as f:
            f.write(TOOL + "\n")
        for name, home in (("PYTHON", "PYTHON_HOME"), ("ROOT", "ROOTSYS"), ("HDF5", "HDF5SYS")):
            with open(self.w(self.tool_root, "modulefiles", name, "template.tcl"), "w") as f:
                f.write(template(name, home))
        self.recipe("PYTHON", "V3.11.14")
        self.recipe("ROOT", "V6.32.20_3.11.14", deps=[("PYTHON", "V3.11.14")])
        self.recipe("HDF5", "V1.14.2")

        with open(self.w(self.products, ".prod", "etc", "prod.conf"), "wb") as f:
            f.write(config.dumps("server"))
        shutil.copytree(os.path.join(REPO, "packages"), self.paths.packages,  # what install.sh puts on a server
                        ignore=shutil.ignore_patterns(".gitkeep"))
        self.version("PYTHON", "V3.11.14", {"bin/python3": b"#!/bin/sh\n", "lib/libpython.so": b"py"})
        self.version("ROOT", "V6.32.20_3.11.14", {"bin/root": b"#!/bin/sh\n", "lib/libCore.so": b"core"})
        self.version("HDF5", "V1.14.2", {"bin/h5dump": b"#!/bin/sh\n", "lib/libhdf5.so": b"h5"})
        os.makedirs(self.paths.drafts)
        self.cfg = config.load(self.paths, env={})

    def tearDown(self):
        fsutil.unlock_tree(self.tmp)
        shutil.rmtree(self.tmp)

    @staticmethod
    def w(*parts):
        path = os.path.join(*parts)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        return path

    def recipe(self, name, version, deps=()):
        """The recipe saved next to the version, as build.sh (save-recipe.py) leaves it; the only one release uses."""
        lines = [f'product: "{name}"', f'version: "{version}"']
        if deps:
            lines.append("depends:")
            lines += [f'  - {{name: "{d}", version: "{v}"}}' for d, v in deps]
        with open(self.w(self.paths.recipe_file(name, version)), "w") as f:
            f.write("\n".join(lines) + "\n")

    def saved_recipe(self, name, version, deps=()):
        """A recipe saved next to the version by build.sh (save-recipe.py), not in the tool."""
        lines = [f'product: "{name}"', f'version: "{version}"']
        if deps:
            lines.append("depends:")
            lines += [f'  - {{name: "{d}", version: "{v}"}}' for d, v in deps]
        lines.append(f'built: "build-recipes/{name}/{version}.yml at commit 0123456789ab on cupdev0 at T"')
        with open(self.w(self.paths.recipe_file(name, version)), "w") as f:
            f.write("\n".join(lines) + "\n")

    def version(self, name, version, files):
        for rel, data in files.items():
            path = self.w(self.products, name, version, rel)
            with open(path, "wb") as f:
                f.write(data)
            if rel.startswith("bin/"):
                os.chmod(path, 0o755)

    def draft(self, name, products, description="test vlist"):
        lines = [f'description: "{description}"', "products:"]
        lines += [f'  - {{name: "{n}", version: "{v}"}}' for n, v in products]
        with open(os.path.join(self.paths.drafts, name + ".yml"), "w") as f:
            f.write("\n".join(lines) + "\n")

    def release(self, name, **kw):
        return release.release(self.paths, self.cfg, name, tool_root=self.tool_root, tool=TOOL, **kw)

    def writable(self, path):
        return bool(os.lstat(path).st_mode & 0o222)


class TestRelease(FakeServer):
    def test_first_release(self):
        self.draft("3.0", [("PYTHON", "V3.11.14"), ("ROOT", "V6.32.20_3.11.14")])
        rp = self.release("3.0")
        p = self.paths
        v = vlist_mod.load(p.vlist_file("3.0"))
        self.assertEqual([(x.name, x.version, x.module) for x in v.products],
                         [("PYTHON", "V3.11.14", "PYTHON/V3.11.14"), ("ROOT", "V6.32.20_3.11.14", "ROOT/V6.32.20_3.11.14")])
        for x in v.products:
            self.assertFalse(manifest.has_errors(manifest.verify(manifest.load(p.manifest_file(x.name, x.version))[1],
                                                                  p.version_dir(x.name, x.version))))
        self.assertEqual(len(rp.products), 2)
        # everything released is locked, the draft stays, history has the event
        for path in (p.vlist_file("3.0"), p.bundle_file("3.0"), p.module_file("ROOT/V6.32.20_3.11.14"),
                     p.manifest_file("ROOT", "V6.32.20_3.11.14"), p.version_dir("ROOT", "V6.32.20_3.11.14"),
                     os.path.join(p.version_dir("ROOT", "V6.32.20_3.11.14"), "lib", "libCore.so")):
            self.assertFalse(self.writable(path), path)
        exe = os.lstat(os.path.join(p.version_dir("ROOT", "V6.32.20_3.11.14"), "bin", "root")).st_mode
        self.assertEqual(stat.S_IMODE(exe), 0o555)
        self.assertTrue(os.path.exists(os.path.join(p.drafts, "3.0.yml")))  # kept for the next release
        self.assertEqual(state.read_history(p)[-1]["command"], "release")
        # bundle loads in vlist order; ROOT's modulefile checks PYTHON by path
        with open(p.bundle_file("3.0")) as f:
            bundle = f.read()
        self.assertLess(bundle.index("module load PYTHON/V3.11.14"), bundle.index("module load ROOT/V6.32.20_3.11.14"))
        with open(p.module_file("ROOT/V6.32.20_3.11.14")) as f:
            self.assertIn(f'$::env(PYTHON_HOME) ne "{self.products}/PYTHON/V3.11.14"', f.read())
        self.assertFalse(os.path.exists(os.path.join(p.products, "ROOT", "V6.32.20_3.11.14.buildinfo")))

    def test_saved_recipe(self):
        # a new version whose recipe is only next to it (built on the server), not in the tool
        self.version("CUPDAQ", "V4.3.2", {"bin/daq": b"#!/bin/sh\n"})
        with open(self.w(self.tool_root, "modulefiles", "CUPDAQ", "template.tcl"), "w") as f:
            f.write(template("CUPDAQ", "CUPDAQ_DIR"))
        self.saved_recipe("CUPDAQ", "V4.3.2", deps=[("HDF5", "V1.14.2")])
        self.draft("daq_3.0", [("CUPDAQ", "V4.3.2")])
        with self.assertRaisesRegex(CheckFailed, "needs HDF5 V1.14.2, which is not in this vlist"):
            self.release("daq_3.0", check=True)  # the saved recipe's depends are checked
        self.draft("daq_3.0", [("CUPDAQ", "V4.3.2"), ("HDF5", "V1.14.2")])
        rp = self.release("daq_3.0")  # the saved recipe's depends put HDF5 first
        built = "build-recipes/CUPDAQ/V4.3.2.yml at commit 0123456789ab on cupdev0 at T"
        self.assertEqual(rp.products[1].notes, [f"recipe CUPDAQ/V4.3.2.recipe.yml ({built})"])
        self.assertEqual(rp.products[0].notes, ["recipe HDF5/V1.14.2.recipe.yml"])
        p = self.paths
        with open(p.module_file("CUPDAQ/V4.3.2")) as f:
            text = f.read()
        self.assertIn(f"# recipe: CUPDAQ/V4.3.2.recipe.yml ({built})\n", text)
        self.assertIn("module load HDF5/V1.14.2", text)
        self.assertFalse(self.writable(p.recipe_file("CUPDAQ", "V4.3.2")))

    def test_draft_version_picks_the_build_that_fits(self):
        # V1.4 in a draft means V1.4 or V1.4.<x>: the one whose depends are in the vlist
        with open(self.w(self.tool_root, "modulefiles", "CUPDATAMODEL", "template.tcl"), "w") as f:
            f.write(template("CUPDATAMODEL", "CUPDM_DIR"))
        self.version("HDF5", "V2.2.0", {"lib/libhdf5.so": b"h5 2"})
        self.recipe("HDF5", "V2.2.0")
        for v, h in (("V1.4.1", "V1.14.2"), ("V1.4.2", "V2.2.0"), ("V1.40", "V1.14.2")):
            self.version("CUPDATAMODEL", v, {"lib/libRawObjs.so": v.encode()})
            self.saved_recipe("CUPDATAMODEL", v, deps=[("HDF5", h)])
        self.draft("dm_a", [("HDF5", "V1.14.2"), ("CUPDATAMODEL", "V1.4")])
        rp = self.release("dm_a")
        self.assertEqual([(x.name, x.version) for x in rp.vlist.products],
                         [("HDF5", "V1.14.2"), ("CUPDATAMODEL", "V1.4.1")])  # never V1.40
        self.draft("dm_b", [("CUPDATAMODEL", "V1.4"), ("HDF5", "V2.2.0")])  # any order
        rp = self.release("dm_b", check=True)
        self.assertEqual([(x.name, x.version) for x in rp.vlist.products],
                         [("HDF5", "V2.2.0"), ("CUPDATAMODEL", "V1.4.2")])
        # none fits, more than one fits
        self.draft("dm_c", [("CUPDATAMODEL", "V1.4")])
        with self.assertRaisesRegex(CheckFailed, "none of the built versions V1.4.1, V1.4.2 has all its depends in"):
            self.release("dm_c", check=True)
        self.version("CUPDATAMODEL", "V1.4.3", {"lib/libRawObjs.so": b"3"})
        self.saved_recipe("CUPDATAMODEL", "V1.4.3", deps=[("HDF5", "V1.14.2")])
        self.draft("dm_d", [("HDF5", "V1.14.2"), ("CUPDATAMODEL", "V1.4")])
        with self.assertRaisesRegex(CheckFailed, "more than one built version fits this vlist: V1.4.1, V1.4.3"):
            self.release("dm_d", check=True)
        # an exact name still works as before
        self.draft("dm_e", [("HDF5", "V1.14.2"), ("CUPDATAMODEL", "V1.4.3")])
        self.assertEqual(self.release("dm_e", check=True).vlist.products[1].version, "V1.4.3")

    def test_saved_recipe_locked_for_frozen(self):
        # a frozen version's saved recipe that became writable is locked again by the next release
        self.saved_recipe("HDF5", "V1.14.2")
        self.draft("3.0", [("HDF5", "V1.14.2")])
        self.release("3.0")
        os.chmod(self.paths.recipe_file("HDF5", "V1.14.2"), 0o644)
        self.draft("3.1", [("HDF5", "V1.14.2")])
        self.release("3.1")
        self.assertFalse(self.writable(self.paths.recipe_file("HDF5", "V1.14.2")))

    def test_bad_saved_recipe(self):
        self.version("CUPDAQ", "V4.3.2", {"bin/daq": b"#!/bin/sh\n"})
        with open(self.w(self.paths.recipe_file("CUPDAQ", "V4.3.2")), "w") as f:
            f.write('product: "CUPDAQ"\nversion: "V4.3.1"\n')
        self.draft("daq_3.0", [("CUPDAQ", "V4.3.2")])
        with self.assertRaisesRegex(CheckFailed, "V4.3.2.recipe.yml: .*do not match the file location"):
            self.release("daq_3.0", check=True)

    def test_second_release_reuses_frozen_versions(self):
        self.draft("3.0", [("PYTHON", "V3.11.14"), ("ROOT", "V6.32.20_3.11.14")])
        self.release("3.0")
        before = sha256_file(self.paths.manifest_file("ROOT", "V6.32.20_3.11.14"))
        self.draft("3.1", [("PYTHON", "V3.11.14"), ("ROOT", "V6.32.20_3.11.14"), ("HDF5", "V1.14.2")])
        rp = self.release("3.1")
        self.assertEqual([pp.status for pp in rp.products], ["frozen", "frozen", "staging"])
        self.assertEqual([pp.module_text is None for pp in rp.products], [True, True, False])
        self.assertEqual(before, sha256_file(self.paths.manifest_file("ROOT", "V6.32.20_3.11.14")))

    def test_name_in_use(self):
        self.draft("3.0", [("PYTHON", "V3.11.14")])
        self.release("3.0")
        self.draft("3.0", [("PYTHON", "V3.11.14")])
        with self.assertRaisesRegex(CheckFailed, "already released"):
            self.release("3.0")

    def test_frozen_version_changed_on_server(self):
        self.draft("3.0", [("PYTHON", "V3.11.14")])
        self.release("3.0")
        f = os.path.join(self.paths.version_dir("PYTHON", "V3.11.14"), "lib", "libpython.so")
        fsutil.unlock_tree(self.paths.version_dir("PYTHON", "V3.11.14"))
        with open(f, "wb") as fh:
            fh.write(b"PY")  # same size, different content
        self.draft("3.1", [("PYTHON", "V3.11.14")])
        with self.assertRaisesRegex(CheckFailed, "differs from its manifest"):
            self.release("3.1")
        self.assertFalse(os.path.exists(self.paths.vlist_file("3.1")))

    def test_dependencies(self):
        self.draft("x", [("ROOT", "V6.32.20_3.11.14")])
        with self.assertRaisesRegex(CheckFailed, "needs PYTHON V3.11.14, which is not in this vlist"):
            self.release("x")

    def test_draft_order_does_not_matter(self):
        # dependencies first, products with no dependency relation (HDF5 here) last
        self.draft("x", [("HDF5", "V1.14.2"), ("ROOT", "V6.32.20_3.11.14"), ("PYTHON", "V3.11.14")])
        self.release("x")
        v = vlist_mod.load(self.paths.vlist_file("x"))
        self.assertEqual([p.name for p in v.products], ["PYTHON", "ROOT", "HDF5"])
        with open(self.paths.bundle_file("x")) as f:
            bundle = f.read()
        self.assertLess(bundle.index("module load PYTHON/"), bundle.index("module load ROOT/"))

    def snapshot(self):
        """Every path and mode under Products, except .prod/state (the lock file lives there)."""
        result = []
        for dirpath, dirnames, filenames in os.walk(self.products):
            if dirpath.startswith(self.paths.state):
                continue
            for n in dirnames + filenames:
                p = os.path.join(dirpath, n)
                if not p.startswith(self.paths.state):
                    result.append((p, os.lstat(p).st_mode))
        return sorted(result)

    def test_check_writes_nothing(self):
        self.draft("3.0", [("PYTHON", "V3.11.14"), ("ROOT", "V6.32.20_3.11.14")])
        before = self.snapshot()
        rp = self.release("3.0", check=True)
        self.assertEqual(len(rp.products), 2)
        self.assertEqual(before, self.snapshot())
        self.assertTrue(self.writable(self.paths.version_dir("ROOT", "V6.32.20_3.11.14")))

    def test_problems_are_collected_and_nothing_is_frozen(self):
        os.symlink("/etc/passwd", os.path.join(self.paths.version_dir("HDF5", "V1.14.2"), "abs"))
        self.draft("x", [("PYTHON", "V3.11.14"), ("HDF5", "V1.14.2"), ("ROOT", "V9.9")])
        with self.assertRaises(CheckFailed) as cm:
            self.release("x")
        msg = str(cm.exception)
        self.assertIn("HDF5 V1.14.2", msg)
        self.assertIn("absolute symlink", msg)
        self.assertIn("ROOT V9.9: no version directory", msg)
        self.assertFalse(os.path.exists(self.paths.manifest_file("PYTHON", "V3.11.14")))

    def test_suspicious_files_are_warnings(self):
        d = self.paths.version_dir("HDF5", "V1.14.2")
        elf_core = b"\x7fELF\x02\x01\x01" + bytes(9) + (4).to_bytes(2, "little")  # 64-bit LE, e_type ET_CORE
        elf_exec = b"\x7fELF\x02\x01\x01" + bytes(9) + (2).to_bytes(2, "little")  # e_type ET_EXEC
        os.makedirs(os.path.join(d, "schemas"))
        for sub in ("lib/python3.11/site-packages/pkg", "lib/python3.11/test", "share/jupyter/lab/schemas"):
            os.makedirs(os.path.join(d, sub))
        files = {"lib/old.tgz": b"x", "core.4711": elf_core, "lib/a~": b"x", "lib/b.orig": b"x",
                 # shipped by pip packages: not counted
                 "lib/python3.11/site-packages/pkg/data.tar.gz": b"x",
                 "lib/python3.11/site-packages/pkg/package.json.orig": b"x",
                 "lib/python3.11/test/testtar.tar": b"x", "share/jupyter/lab/schemas/x.json.orig": b"x",
                 "lib/core.mjs": b"x", "lib/core.py": b"x",
                 "schemas/core": b'{"$schema": "core vocabulary"}\n',  # named core, but not a core dump
                 "lib/core": elf_exec}
        for name, data in files.items():
            with open(os.path.join(d, name), "wb") as f:
                f.write(data)
        self.draft("x", [("HDF5", "V1.14.2")])
        rp = self.release("x", check=True)
        [w] = rp.warnings  # one summary line per version, not one per file
        self.assertIn("HDF5 V1.14.2: 4 suspicious file(s)", w)
        self.assertIn("(4 in all)", w)
        self.assertIn("core.4711", w)
        for name in ("core.mjs", "core.py", "schemas/core", "lib/core,", "site-packages", "python3.11/test",
                     "share/jupyter"):
            self.assertNotIn(name, w)

    def test_released_modulefile_is_kept(self):
        self.draft("3.0", [("PYTHON", "V3.11.14"), ("ROOT", "V6.32.20_3.11.14")])
        self.release("3.0")
        old_module = sha256_file(self.paths.module_file("ROOT/V6.32.20_3.11.14"))
        with open(os.path.join(self.tool_root, "modulefiles", "ROOT", "template.tcl"), "a") as f:
            f.write("setenv ROOT_FIXED 1\n")
        self.draft("3.1", [("PYTHON", "V3.11.14"), ("ROOT", "V6.32.20_3.11.14")])
        rp = self.release("3.1")
        self.assertEqual([pp.module_text for pp in rp.products], [None, None])
        self.assertEqual(old_module, sha256_file(self.paths.module_file("ROOT/V6.32.20_3.11.14")))

    def test_role(self):
        cfg = config.loads(config.dumps("client", "/x"), env={})
        self.draft("3.0", [("PYTHON", "V3.11.14")])
        with self.assertRaises(EnvError):
            release.release(self.paths, cfg, "3.0", tool_root=self.tool_root, tool=TOOL)


class TestRemoveOnServer(FakeServer):
    def setUp(self):
        super().setUp()
        self.draft("3.0", [("PYTHON", "V3.11.14"), ("ROOT", "V6.32.20_3.11.14")])
        self.release("3.0")
        self.draft("3.1", [("PYTHON", "V3.11.14"), ("HDF5", "V1.14.2")])
        self.release("3.1")

    def remove(self, *names, yes=True):
        return inventory.remove(self.paths, self.cfg, list(names), tool=TOOL, yes=yes)

    def rel(self, paths):
        return {os.path.relpath(p, self.products) for p in paths}

    def test_remove_and_gc(self):
        files = self.remove("3.0", yes=False)  # without yes: only shows
        self.assertEqual(self.rel(files), {"vlist/3.0.yml", "modulefiles/vlist/3.0"})
        self.assertTrue(os.path.exists(self.paths.vlist_file("3.0")))
        self.remove("3.0")
        for f in files:
            self.assertFalse(os.path.lexists(f))
        self.assertTrue(os.path.exists(self.paths.version_dir("ROOT", "V6.32.20_3.11.14")))  # versions stay
        # gc: only what 3.0 alone used; PYTHON stays for 3.1, and a staging version is never a candidate
        self.version("HDF5", "V2.2.0", {"lib/libhdf5.so": b"staging"})
        cands = self.rel(inventory.gc_candidates(self.paths))
        self.assertEqual(cands, {"ROOT/V6.32.20_3.11.14", "ROOT/V6.32.20_3.11.14.manifest",
                                 "ROOT/V6.32.20_3.11.14.recipe.yml", "modulefiles/ROOT/V6.32.20_3.11.14"})
        inventory.gc(self.paths, self.cfg, tool=TOOL, yes=True)
        self.assertFalse(os.path.exists(self.paths.version_dir("ROOT", "V6.32.20_3.11.14")))
        self.assertTrue(os.path.exists(self.paths.version_dir("HDF5", "V2.2.0")))
        self.assertEqual([e["command"] for e in state.read_history(self.paths)][-2:], ["remove", "gc"])
        # the name is free again
        self.draft("3.0", [("PYTHON", "V3.11.14")])
        self.release("3.0")

    def test_remove_versions(self):
        # a version a vlist uses is refused, unless that vlist goes in the same command
        with self.assertRaisesRegex(CheckFailed, "used by vlist 3.0; remove the vlist first"):
            self.remove("ROOT/V6.32.20_3.11.14")
        files = self.remove("3.0", "ROOT/V6.32.20_3.11.14", yes=False)
        self.assertEqual(self.rel(files), {"vlist/3.0.yml", "modulefiles/vlist/3.0", "ROOT/V6.32.20_3.11.14",
                                           "ROOT/V6.32.20_3.11.14.manifest", "ROOT/V6.32.20_3.11.14.recipe.yml",
                                           "modulefiles/ROOT/V6.32.20_3.11.14"})
        self.assertTrue(os.path.exists(self.paths.version_dir("ROOT", "V6.32.20_3.11.14")))
        self.remove("3.0", "ROOT/V6.32.20_3.11.14")
        for f in files:
            self.assertFalse(os.path.lexists(f), f)
        self.assertFalse(self.writable(self.paths.modulefiles))
        # a version that was never released: its directory and saved recipe
        self.version("HDF5", "V2.2.0", {"lib/libhdf5.so": b"staging"})
        self.recipe("HDF5", "V2.2.0")
        self.assertEqual(self.rel(self.remove("HDF5/V2.2.0")), {"HDF5/V2.2.0", "HDF5/V2.2.0.recipe.yml"})
        self.assertFalse(os.path.exists(self.paths.version_dir("HDF5", "V2.2.0")))

    def test_errors(self):
        with self.assertRaisesRegex(FormatError, "not on this node"):
            self.remove("3.0", "9.9")
        with self.assertRaisesRegex(FormatError, "not on this node"):
            self.remove("3.0", "ROOT/V9.9")
        with self.assertRaisesRegex(FormatError, "is not a vlist name or <NAME>/<VERSION>"):
            self.remove("root/V1")
        self.assertTrue(os.path.exists(self.paths.vlist_file("3.0")))  # nothing removed when one name is bad


class TestCli(FakeServer):
    def test_options_before_or_after_the_command(self):
        from prod.cli import _parser
        p = _parser()
        for argv in (["--json", "list"], ["list", "--json"], ["-q", "release", "1.0"], ["release", "1.0", "-q"]):
            ns = p.parse_args(argv)
            self.assertTrue(ns.json or ns.quiet, argv)
        ns = p.parse_args(["list"])
        self.assertEqual((ns.json, ns.quiet), (False, False))

    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = cli.main(list(argv), tool_root=self.tool_root, products=self.products)
        return code, out.getvalue(), err.getvalue()

    def test_release(self):
        self.draft("3.0", [("PYTHON", "V3.11.14"), ("ROOT", "V6.32.20_3.11.14")])
        code, out, _ = self.run_cli("release", "3.0", "--check")
        self.assertEqual(code, 0)
        self.assertIn("check only", out)
        code, out, _ = self.run_cli("release", "3.0")
        self.assertEqual(code, 0)
        self.assertNotIn("check only", out)
        code, _, err = self.run_cli("release", "3.0")
        self.assertEqual(code, 1)
        self.assertIn("already released", err)

    def test_remove(self):
        self.draft("3.0", [("PYTHON", "V3.11.14")])
        self.run_cli("release", "3.0")
        code, out, _ = self.run_cli("remove", "3.0")
        self.assertEqual(code, 0)
        self.assertIn("vlist/3.0.yml", out)
        self.assertIn("would be deleted; run 'prodman remove 3.0 --yes'", out)
        self.assertTrue(os.path.exists(self.paths.vlist_file("3.0")))
        code, out, _ = self.run_cli("remove", "3.0", "--yes")
        self.assertEqual(code, 0)
        self.assertIn("removed 3.0", out)
        self.assertFalse(os.path.exists(self.paths.vlist_file("3.0")))
        code, out, _ = self.run_cli("gc", "--yes")
        self.assertEqual(code, 0)
        self.assertFalse(os.path.exists(self.paths.version_dir("PYTHON", "V3.11.14")))
        code, _, err = self.run_cli("remove", "3.0", "--yes")
        self.assertEqual(code, 1)
        self.assertIn("not on this node", err)

    def test_usage(self):
        self.assertEqual(self.run_cli("release")[0], 2)
        self.assertEqual(self.run_cli("withdraw", "3.0")[0], 2)  # no such command
        self.assertEqual(self.run_cli("bogus")[0], 2)
        code, _, err = self.run_cli("sync", "3.1")
        self.assertEqual(code, 3)  # sync runs only on clients
        self.assertIn("role is 'client'", err)
        code, out, _ = self.run_cli("--version")
        self.assertEqual((code, out.strip()), (0, f"prodman {TOOL}"))


if __name__ == "__main__":
    unittest.main()
