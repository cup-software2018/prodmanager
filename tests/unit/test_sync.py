"""Client commands (sync, resync, verify, list, remove, gc) against a fake server, using real rsync on local paths."""

import io
import json
import os
import select
import stat
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

from prod import cli, config, fsutil, inventory, state, transfer
from prod import progress as progress_mod
from prod import sync as sync_mod
from prod import verify as verify_mod
from prod.errors import CheckFailed, EnvError, LockBusy, UsageError

from .test_release import TOOL, FakeServer


class FakeClient(FakeServer):
    def setUp(self):
        super().setUp()
        self.draft("3.0", [("PYTHON", "V3.11.14"), ("ROOT", "V6.32.20_3.11.14")], description="python and root")
        self.release("3.0")
        self.draft("3.1", [("PYTHON", "V3.11.14"), ("HDF5", "V1.14.2")])
        self.release("3.1")
        self.cpaths, self.ccfg = self.make_client("client")

    def make_client(self, label):
        products = os.path.join(self.tmp, label, "Products")
        paths = config.Paths(products)
        with open(self.w(paths.conf), "wb") as f:
            f.write(config.dumps("client", self.products))
        return paths, config.load(paths, env={})

    def sync(self, *names, paths=None, cfg=None, **kw):
        return sync_mod.sync(paths or self.cpaths, cfg or self.ccfg, list(names), tool=TOOL, **kw)

    def verify(self, *names, paths=None, cfg=None, **kw):
        return verify_mod.verify(paths or self.cpaths, cfg or self.ccfg, list(names), **kw)

    def cfile(self, *parts):
        return os.path.join(self.cpaths.products, *parts)

    def tamper(self, path, data):
        """Change a locked file the way a careless person would: unlock it and write."""
        os.chmod(os.path.dirname(path), 0o755)
        os.chmod(path, 0o644)
        with open(path, "wb") as f:
            f.write(data)


class TestSync(FakeClient):
    def test_modulefiles_are_read_only(self):
        def writable_dirs(root):
            return [d for d, _, _ in os.walk(root) if os.lstat(d).st_mode & 0o222]
        self.assertEqual(writable_dirs(self.paths.modulefiles), [])  # on the server, after the releases
        self.sync("3.0", "3.1")
        self.assertEqual(writable_dirs(self.cpaths.modulefiles), [])  # on a client, after sync
        inventory.remove(self.cpaths, self.ccfg, ["3.0"], tool=TOOL, yes=True)
        inventory.gc(self.cpaths, self.ccfg, tool=TOOL, yes=True)
        self.assertEqual(writable_dirs(self.cpaths.modulefiles), [])  # after remove and gc
        self.assertFalse(os.path.exists(self.cpaths.bundle_file("3.0")))

    def test_package_lists_follow_the_server(self):
        self.sync("3.0")
        lists = os.path.join(self.cpaths.products, "packages")
        self.assertEqual(sorted(os.listdir(lists)), sorted(os.listdir(self.paths.packages)))
        self.assertFalse(os.lstat(os.path.join(lists, "root.yml")).st_mode & 0o222)  # a copy: not for editing
        # the server's lists change (one edited, one removed): the next sync mirrors them
        with open(os.path.join(self.paths.packages, "root.yml"), "a") as f:
            f.write("# changed on the server\n")
        os.unlink(os.path.join(self.paths.packages, "caen.yml"))
        self.sync("3.0")
        with open(os.path.join(lists, "root.yml")) as f:
            self.assertTrue(f.read().endswith("# changed on the server\n"))
        self.assertFalse(os.path.exists(os.path.join(lists, "caen.yml")))
        self.assertEqual(inventory.unmanaged(self.cpaths), ([], []))  # managed by prod

    def test_first_sync(self):
        [r] = self.sync("3.0")
        self.assertEqual([v.action for v in r.versions], ["transferred", "transferred"])
        p = self.cpaths
        for path in (p.vlist_file("3.0"), p.bundle_file("3.0"), p.module_file("ROOT/V6.32.20_3.11.14"),
                     p.manifest_file("ROOT", "V6.32.20_3.11.14"),
                     self.cfile("ROOT", "V6.32.20_3.11.14", "lib", "libCore.so"),
                     self.cfile("ROOT", "V6.32.20_3.11.14")):
            self.assertFalse(os.lstat(path).st_mode & 0o222, path)
        self.assertEqual(stat.S_IMODE(os.lstat(self.cfile("ROOT", "V6.32.20_3.11.14", "bin", "root")).st_mode), 0o555)
        self.assertEqual([x.ok for x in self.verify()], [True])
        self.assertEqual(state.read_history(p)[-1]["command"], "sync")
        self.assertFalse([n for n in os.listdir(self.cfile("ROOT")) if n.startswith(".")])  # no leftovers
        self.assertEqual(os.listdir(p.tmp), [])

        # a second vlist sharing PYTHON: PYTHON stays in place
        [r] = self.sync("3.1")
        self.assertEqual([v.action for v in r.versions], ["in place", "transferred"])
        [r0, r1] = self.sync("3.0", "3.1")
        self.assertEqual({v.action for v in r0.versions + r1.versions}, {"in place"})

    def test_repair_reuses_unchanged_files(self):
        self.sync("3.0")
        unchanged = self.cfile("ROOT", "V6.32.20_3.11.14", "bin", "root")
        inode = os.lstat(unchanged).st_ino
        self.tamper(self.cfile("ROOT", "V6.32.20_3.11.14", "lib", "libCore.so"), b"CORE")
        with open(self.cfile("ROOT", "V6.32.20_3.11.14", "lib", "extra.txt"), "wb") as f:
            f.write(b"x")
        [rep] = self.verify("3.0")
        self.assertFalse(rep.ok)
        self.assertTrue(any("libCore.so: content differs" in e for e in rep.errors))
        self.assertTrue(any("extra.txt: not in the manifest" in e for e in rep.errors))
        [r] = self.sync(resync=True)
        self.assertEqual([v.action for v in r.versions], ["in place", "transferred"])
        self.assertTrue(self.verify("3.0")[0].ok)
        # the unchanged file was hard-linked from the previous copy, and is still locked
        self.assertEqual(os.lstat(unchanged).st_ino, inode)
        self.assertEqual(stat.S_IMODE(os.lstat(unchanged).st_mode), 0o555)

    def test_server_bytes_changed_after_release(self):
        f = os.path.join(self.paths.version_dir("ROOT", "V6.32.20_3.11.14"), "lib", "libCore.so")
        self.tamper(f, b"CORE")  # same size: only a content check can see it
        with self.assertRaisesRegex(CheckFailed, "even after a full re-transfer"):
            self.sync("3.0")
        self.assertFalse(os.path.exists(self.cpaths.vlist_file("3.0")))  # nothing half-installed is visible
        self.assertFalse(os.path.exists(self.cfile("ROOT", "V6.32.20_3.11.14")))
        sreps = verify_mod.verify(self.paths, self.cfg)  # and the server's own verify sees it
        self.assertFalse(all(r.ok for r in sreps))

    def test_server_vlist_changed(self):
        # released files should never change; if they do, verify --remote shows it and resync takes the change
        self.sync("3.0", "3.1")
        vfile = self.paths.vlist_file("3.0")
        os.chmod(vfile, 0o644)
        with open(vfile, "ab") as f:
            f.write(b"# edited on the server\n")
        reps = self.verify("3.0", remote=True)
        self.assertTrue(any("server's vlist differs from this node's" in e for e in reps[0].errors))
        self.assertEqual([r.name for r in self.sync(resync=True)], ["3.0", "3.1"])
        self.assertTrue(self.verify("3.0", remote=True)[0].ok)

    def test_interrupted_transfer_is_resumed(self):
        real = transfer.fetch_tree

        def copy_then_fail(cfg, rel, dest, **kw):
            real(cfg, rel, dest, **kw)
            if rel != "packages":  # the connection is lost while a version is transferred
                raise EnvError("connection lost (simulated)")

        with mock.patch.object(transfer, "fetch_tree", side_effect=copy_then_fail):
            with self.assertRaises(EnvError):
                self.sync("3.0")
        self.assertTrue(os.path.isdir(self.cfile("PYTHON", ".V3.11.14.partial")))
        self.assertFalse(os.path.exists(self.cpaths.vlist_file("3.0")))
        self.sync("3.0")
        self.assertFalse(os.path.exists(self.cfile("PYTHON", ".V3.11.14.partial")))
        self.assertTrue(self.verify("3.0")[0].ok)

    def test_dry_run(self):
        [r] = self.sync("3.0", dry_run=True)
        self.assertEqual([v.action for v in r.versions], ["would transfer", "would transfer"])
        self.assertFalse(os.path.exists(self.cpaths.vlist_file("3.0")))
        self.assertFalse(os.path.exists(self.cfile("PYTHON")))

    def test_errors(self):
        with self.assertRaisesRegex(CheckFailed, "not on the products server"):
            self.sync("9.9")
        with self.assertRaises(EnvError):  # sync is for clients
            self.sync("3.0", paths=self.paths, cfg=self.cfg)
        with state.lock(self.cpaths, exclusive=True):
            with self.assertRaises(LockBusy):
                self.sync("3.0")
        with self.assertRaisesRegex(UsageError, "no vlists on this node"):
            self.sync(resync=True)

    def test_writable_files_are_locked_again(self):
        self.sync("3.0")
        f = self.cfile("PYTHON", "V3.11.14", "lib", "libpython.so")
        os.chmod(f, 0o644)
        os.chmod(self.cpaths.module_file("ROOT/V6.32.20_3.11.14"), 0o644)
        [rep] = self.verify("3.0")
        self.assertTrue(rep.ok)
        self.assertIn("PYTHON V3.11.14: 1 entries: was writable, locked again", rep.warnings)
        self.assertIn("modulefile ROOT/V6.32.20_3.11.14: was writable, locked again", rep.warnings)
        self.assertFalse(os.lstat(f).st_mode & 0o222)
        self.assertFalse(os.lstat(self.cpaths.module_file("ROOT/V6.32.20_3.11.14")).st_mode & 0o222)
        self.assertEqual(self.verify("3.0")[0].warnings, [])
        # a version that differs from its manifest is not locked
        self.tamper(f, b"PY")
        [rep] = self.verify("3.0")
        self.assertFalse(rep.ok)
        self.assertTrue(any("not locked" in w for w in rep.warnings))
        self.assertTrue(os.lstat(f).st_mode & 0o200)


class TestSavedRecipe(FakeClient):
    def test_sync_and_gc(self):
        # a version released with a saved recipe: the client gets it, and gc removes it with the version
        self.version("HDF5", "V2.2.0", {"lib/libhdf5.so": b"h5 2"})
        self.saved_recipe("HDF5", "V2.2.0")
        self.draft("3.2", [("HDF5", "V2.2.0")])
        self.release("3.2")
        [r] = self.sync("3.2")
        self.assertEqual(r.warnings, [])
        path = self.cpaths.recipe_file("HDF5", "V2.2.0")
        with open(path, "rb") as f, open(self.paths.recipe_file("HDF5", "V2.2.0"), "rb") as g:
            self.assertEqual(f.read(), g.read())
        self.assertFalse(os.lstat(path).st_mode & 0o222)
        self.assertFalse(os.path.exists(self.cpaths.recipe_file("HDF5", "V1.14.2")))  # none on the server: fine
        self.assertEqual(inventory.unmanaged(self.cpaths), ([], []))
        inventory.remove(self.cpaths, self.ccfg, ["3.2"], tool=TOOL, yes=True)
        cands = {os.path.relpath(c, self.cpaths.products) for c in inventory.gc_candidates(self.cpaths)}
        self.assertEqual(cands, {"HDF5/V2.2.0", "HDF5/V2.2.0.manifest", "HDF5/V2.2.0.recipe.yml",
                                 "modulefiles/HDF5/V2.2.0"})


class TestInventory(FakeClient):
    def test_remove_and_gc(self):
        self.sync("3.0", "3.1")
        unmanaged = self.cfile("ROOT", "setup_ROOT.sh")  # a file prod does not manage: never touched by gc
        with open(unmanaged, "w") as f:
            f.write("not managed\n")
        inventory.remove(self.cpaths, self.ccfg, ["3.0"], tool=TOOL)  # without yes: only shows
        self.assertTrue(os.path.exists(self.cpaths.vlist_file("3.0")))
        inventory.remove(self.cpaths, self.ccfg, ["3.0"], tool=TOOL, yes=True)
        self.assertFalse(os.path.exists(self.cpaths.vlist_file("3.0")))
        cands = {os.path.relpath(c, self.cpaths.products) for c in inventory.gc_candidates(self.cpaths)}
        self.assertEqual(cands, {"ROOT/V6.32.20_3.11.14", "ROOT/V6.32.20_3.11.14.manifest",
                                 "ROOT/V6.32.20_3.11.14.recipe.yml", "modulefiles/ROOT/V6.32.20_3.11.14"})
        inventory.gc(self.cpaths, self.ccfg, tool=TOOL)  # without --yes: nothing deleted
        self.assertTrue(os.path.exists(self.cfile("ROOT", "V6.32.20_3.11.14")))
        inventory.gc(self.cpaths, self.ccfg, tool=TOOL, yes=True)
        self.assertFalse(os.path.exists(self.cfile("ROOT", "V6.32.20_3.11.14")))
        self.assertTrue(os.path.exists(unmanaged))
        self.assertTrue(os.path.exists(self.cfile("PYTHON", "V3.11.14")))
        self.assertTrue(self.verify()[0].ok)
        # a version a vlist uses cannot be removed; one no vlist uses can
        with self.assertRaisesRegex(CheckFailed, "used by vlist 3.1"):
            inventory.remove(self.cpaths, self.ccfg, ["PYTHON/V3.11.14"], tool=TOOL, yes=True)
        [r] = self.sync("3.0")
        self.assertEqual([v.action for v in r.versions], ["in place", "transferred"])

    def test_listing(self):
        self.sync("3.0")
        with open(self.cfile("CERNLIB.tgz"), "w") as f:
            f.write("x")
        os.makedirs(self.cfile("ROOT", ".V9.partial"))
        # a directory without versions (a pyenv checkout) is one entry; one with versions is listed per entry
        for rel in (("PYENV", ".git"), ("PYENV", "bin"), ("PYENV", "versions")):
            os.makedirs(self.cfile(*rel))
        with open(self.cfile("PYENV", "README.md"), "w") as f:
            f.write("x")
        os.makedirs(self.cfile("GEANT4", "V11.1.3"))
        with open(self.cfile("GEANT4", "setup_GEANT4.sh"), "w") as f:
            f.write("x")
        os.makedirs(self.cfile("NEWPROD", ".V1.0.partial"))  # an interrupted first sync: only a leftover
        lst = inventory.listing(self.cpaths, self.ccfg, tool=TOOL, remote=True)
        self.assertEqual([v["name"] for v in lst.vlists], ["3.0"])
        self.assertIsNotNone(lst.vlists[0]["first_sync"])
        self.assertEqual({(x["name"], tuple(x["used_by"])) for x in lst.versions},
                         {("PYTHON", ("3.0",)), ("ROOT", ("3.0",))})
        self.assertEqual(lst.not_released, [{"name": "GEANT4", "version": "V11.1.3"}])  # PYENV/, CERNLIB.tgz: not shown
        self.assertEqual(lst.leftovers, ["NEWPROD/.V1.0.partial", "ROOT/.V9.partial"])
        self.assertEqual([v["name"] for v in lst.remote], ["3.0", "3.1"])

    def test_server_verify_includes_versions_in_no_vlist(self):
        os.makedirs(self.paths.drafts, exist_ok=True)
        self.version("HDF5", "V1.14.3", {"lib/libhdf5.so": b"h5"})
        self.recipe("HDF5", "V1.14.3")
        self.draft("tmp", [("HDF5", "V1.14.3")])
        self.release("tmp")
        os.unlink(self.paths.vlist_file("tmp"))  # leaves a frozen version that no vlist uses
        self.tamper(os.path.join(self.paths.version_dir("HDF5", "V1.14.3"), "lib", "libhdf5.so"), b"H5")
        reps = verify_mod.verify(self.paths, self.cfg)
        self.assertEqual(reps[-1].name, "(frozen versions in no vlist)")
        self.assertFalse(reps[-1].ok)


class TestClientCli(FakeClient):
    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = cli.main(list(argv), tool_root=self.tool_root, products=self.cpaths.products)
        return code, out.getvalue(), err.getvalue()

    def test_commands(self):
        code, out, err = self.run_cli("sync", "3.0")
        self.assertEqual(code, 0)
        self.assertIn("sync 3.0", out)
        # progress lines go to stderr, the summary to stdout
        self.assertIn("3.0: 2 products", err)
        self.assertRegex(err, r"PYTHON +V3.11.14 +transferring \d+ entries")
        self.assertRegex(err, r"PYTHON +V3.11.14 +transferred and verified")
        self.assertNotIn("transferring", out)
        code, out, err = self.run_cli("sync", "3.0", "3.1")
        self.assertRegex(err, r"PYTHON +V3.11.14 +in place, verified")
        self.assertRegex(err, r"HDF5 +V1.14.2 +transferred and verified")
        code, out, err = self.run_cli("verify")
        self.assertEqual(code, 0)
        self.assertIn("3.0", out)
        self.assertRegex(err, r"ROOT +V6.32.20_3.11.14 +ok \(")
        self.assertRegex(err, r"PYTHON +V3.11.14 +checked above")
        code, out, err = self.run_cli("-q", "verify")
        self.assertEqual((code, out, err), (0, "", ""))
        code, out, _ = self.run_cli("--json", "verify")
        self.assertTrue(json.loads(out)[0]["ok"])
        code, out, _ = self.run_cli("--json", "list")
        self.assertEqual(json.loads(out)["vlists"][0]["name"], "3.0")
        code, out, _ = self.run_cli("list", "--remote")
        self.assertIn("on the products server:", out)
        self.tamper(self.cfile("PYTHON", "V3.11.14", "lib", "libpython.so"), b"PY")
        code, out, _ = self.run_cli("verify")
        self.assertEqual(code, 1)
        self.assertIn("content differs", out)
        self.assertEqual(self.run_cli("sync")[0], 2)
        self.assertEqual(self.run_cli("sync", "--installed")[0], 2)  # replaced by resync
        code, out, _ = self.run_cli("resync")
        self.assertEqual(code, 0)
        self.assertIn("sync 3.0", out)
        self.assertIn("sync 3.1", out)
        self.assertEqual(self.run_cli("gc")[0], 0)
        self.assertEqual(self.run_cli("remove", "9.9")[0], 1)
        code, out, _ = self.run_cli("remove", "3.1")
        self.assertEqual(code, 0)
        self.assertIn("would be deleted; run 'prodman remove 3.1 --yes'", out)
        self.assertTrue(os.path.exists(self.cpaths.vlist_file("3.1")))



class TestLiveProgress(FakeClient):
    """On a terminal, sync shows rsync's own progress line; elsewhere it does not."""

    def test_live_only_on_a_terminal(self):
        self.assertIsNone(progress_mod.Progress(io.StringIO()).live)
        self.assertIsNone(progress_mod.SILENT.live)

        master, slave = os.openpty()
        try:
            with os.fdopen(slave, "w", closefd=True) as term:
                prog = progress_mod.Progress(term)
                self.assertIs(prog.live, term)
                self.assertIsNone(progress_mod.Progress(term, quiet=True).live)
                sync_mod.sync(self.cpaths, self.ccfg, ["3.0"], tool=TOOL, progress=prog)
                shown = b""
                while True:  # drain what the pseudo terminal received
                    if not select.select([master], [], [], 0.2)[0]:
                        break
                    chunk = os.read(master, 65536)
                    if not chunk:
                        break
                    shown += chunk
        finally:
            os.close(master)
        text = shown.decode("utf-8", "replace")
        self.assertIn("transferring", text)
        self.assertIn("100%", text)  # rsync --info=progress2
        self.assertTrue(self.verify("3.0")[0].ok)


if __name__ == "__main__":
    unittest.main()
