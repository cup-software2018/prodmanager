"""install.sh and shell/setup_prod.sh, run in a temporary HOME and Products (never the real ones)."""

import os
import shutil
import subprocess
import tempfile
import unittest

from prod import config, fsutil, state
from prod.errors import CheckFailed

from .test_sync import FakeClient

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
HAVE_MODULES = os.path.exists("/usr/share/Modules/init/bash")


class Installed(FakeClient):
    """A fake server and client (with 3.0 and 3.1 released); the tool is installed into the client."""

    def setUp(self):
        super().setUp()
        self.home = os.path.join(self.tmp, "home")
        os.makedirs(self.home)
        self.cprod = self.cpaths.products
        os.unlink(self.cpaths.conf)  # let install.sh create it

    def env(self, **extra):
        env = {"HOME": self.home, "PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"}
        env.update(extra)
        return env

    def install(self, *args, check=True, version="v0.1"):
        cmd = [os.path.join(REPO, "install.sh"), "--products_dir", self.cprod]
        if version:
            cmd += ["--version-name", version]
        out = subprocess.run(cmd + list(args), env=self.env(), capture_output=True, text=True,
                             stdin=subprocess.DEVNULL)
        if check and out.returncode != 0:
            self.fail(f"install.sh failed ({out.returncode}):\n{out.stdout}\n{out.stderr}")
        return out

    def bash(self, script, **env):
        """Run a script in a clean, non-interactive bash (no module function, no MODULEPATH)."""
        return subprocess.run(["bash", "--noprofile", "--norc", "-c", script], env=self.env(**env),
                              capture_output=True, text=True, stdin=subprocess.DEVNULL)


class TestInstall(Installed):
    def test_install(self):
        # a link left by a version that still called the tool 'prod' goes away
        os.makedirs(os.path.join(self.home, ".local", "bin"))
        os.symlink(os.path.join(self.cprod, ".prod", "current", "bin", "prod"),
                   os.path.join(self.home, ".local", "bin", "prod"))
        out = self.install("--role", "client", "--server", self.products)
        self.assertFalse(os.path.lexists(os.path.join(self.home, ".local", "bin", "prod")))
        self.assertIn("installed prodman v0.1", out.stdout)
        prod = os.path.join(self.cprod, ".prod")
        self.assertEqual(os.readlink(os.path.join(prod, "current")), "versions/v0.1")
        v = os.path.join(prod, "versions", "v0.1")
        for rel in ("bin/prodman", "lib/prod/cli.py", "share/setup_prod.sh", "share/setup_prod.csh",
                    "share/modulefiles", "VERSION"):
            self.assertTrue(os.path.exists(os.path.join(v, rel)), rel)
        for rel in ("share/recipes", "share/packages"):
            self.assertFalse(os.path.exists(os.path.join(v, rel)), rel)
        self.assertFalse(os.path.exists(os.path.join(self.cprod, "packages")))  # a client gets them by prodman sync
        self.assertFalse(os.path.exists(os.path.join(self.cprod, "build-recipes")))  # a client has none
        self.assertFalse(any("__pycache__" in d for d, _, _ in os.walk(v)))
        self.assertFalse(os.lstat(os.path.join(v, "lib", "prod", "cli.py")).st_mode & 0o222)  # read-only
        with open(os.path.join(v, "share", "setup_prod.sh")) as f:
            self.assertIn(f'local products="{self.cprod}"', f.read())
        cfg = config.load(config.Paths(self.cprod), env={})
        self.assertEqual((cfg.role, cfg.server.path), ("client", self.products))
        self.assertEqual(os.readlink(os.path.join(self.home, "setup_prod.sh")),
                         os.path.join(prod, "current", "share", "setup_prod.sh"))
        self.assertEqual(os.readlink(os.path.join(self.home, ".local", "bin", "prodman")),
                         os.path.join(prod, "current", "bin", "prodman"))
        self.assertEqual([e["command"] for e in state.read_history(self.cpaths)][-1:], ["install"])
        # the installed prod finds its Products from its own location
        out = subprocess.run([os.path.join(self.home, ".local", "bin", "prodman"), "list"], env=self.env(),
                             capture_output=True, text=True)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("tool    v0.1", out.stdout)
        self.assertIn(f"role    client, server {self.products}", out.stdout)

    def test_upgrade_keeps_config_and_only_the_new_version(self):
        self.install("--role", "client", "--server", self.products)
        with open(self.cpaths.conf, "ab") as f:
            f.write(b'ssh_options: ["-o", "BatchMode=yes"]\n')
        with open(self.cpaths.conf, "rb") as f:
            conf = f.read()
        for i in range(2, 6):
            self.install(version=f"v0.{i}")
        with open(self.cpaths.conf, "rb") as f:
            self.assertEqual(conf, f.read())
        self.assertEqual(os.readlink(os.path.join(self.cprod, ".prod", "current")), "versions/v0.5")
        self.assertEqual(os.listdir(os.path.join(self.cprod, ".prod", "versions")), ["v0.5"])
        # the same version again needs --force
        out = self.install(version="v0.5", check=False)
        self.assertNotEqual(out.returncode, 0)
        self.assertIn("already installed", out.stderr)
        self.install("--force", version="v0.5")

    def test_server_build_recipes(self):
        self.install("--role", "server")
        br = os.path.join(self.cprod, "build-recipes")
        for rel in ("_common/build-common.sh", "_common/save-recipe.py", "ROOT/build.sh", "ROOT/V6.32.20_3.11.14.yml",
                    "PYTHON/V3.11.14.yml"):
            self.assertTrue(os.path.exists(os.path.join(br, rel)), rel)
        self.assertTrue(os.access(os.path.join(br, "ROOT"), os.W_OK))  # people write recipes there
        self.assertTrue(os.path.isdir(os.path.join(self.cprod, "vlist", "drafts")))
        self.assertTrue(os.path.exists(os.path.join(self.cprod, "packages", "root.yml")))  # edited on the server
        with open(os.path.join(self.cprod, "packages", "root.yml"), "a") as f:
            f.write("# changed here\n")
        # an upgrade replaces the scripts, keeps the recipes (changed or new), removes nothing
        with open(os.path.join(br, "ROOT", "V6.32.20_3.11.14.yml"), "a") as f:
            f.write("# changed here\n")
        with open(os.path.join(br, "ROOT", "V9.9.9.yml"), "w") as f:
            f.write("# new here\n")
        with open(os.path.join(br, "ROOT", "build.sh"), "a") as f:
            f.write("# stale\n")
        self.install(version="v0.2")
        with open(os.path.join(br, "ROOT", "V6.32.20_3.11.14.yml")) as f:
            self.assertTrue(f.read().endswith("# changed here\n"))
        self.assertTrue(os.path.exists(os.path.join(br, "ROOT", "V9.9.9.yml")))
        with open(os.path.join(br, "ROOT", "build.sh")) as f:
            self.assertNotIn("# stale", f.read())
        with open(os.path.join(self.cprod, "packages", "root.yml")) as f:
            self.assertTrue(f.read().endswith("# changed here\n"))  # package lists are kept too
        # the scripts there find the installed tool
        out = subprocess.run(["/usr/bin/python3", "-I", os.path.join(br, "_common", "recipeinfo.py"),
                              os.path.join(br, "ROOT", "V6.32.20_3.11.14.yml")], capture_output=True, text=True)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("depends\tPYTHON\tV3.11.14", out.stdout)

    def test_server_products_through_a_symlink(self):
        # ~/Products is often a symlink to a local disk; rsync 3.2.7 refuses such a destination unless resolved
        link = os.path.join(self.tmp, "ProductsLink")
        os.symlink(self.cprod, link)
        out = subprocess.run([os.path.join(REPO, "install.sh"), "--products_dir", link, "--role", "server",
                              "--version-name", "v0.1"], env=self.env(), capture_output=True, text=True,
                             stdin=subprocess.DEVNULL)
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        self.assertTrue(os.path.exists(os.path.join(self.cprod, "build-recipes", "ROOT", "build.sh")))

    def test_real_file_is_kept_as_bak(self):
        path = os.path.join(self.home, "setup_prod.sh")
        with open(path, "w") as f:
            f.write("# my own\n")
        out = self.install("--role", "server")  # linked on a server too
        self.assertIn("setup_prod.sh.bak", out.stdout)
        with open(path + ".bak") as f:
            self.assertEqual(f.read(), "# my own\n")
        for name, target in (("setup_prod.sh", "share/setup_prod.sh"), ("setup_prod.csh", "share/setup_prod.csh"),
                             (".local/bin/prodman", "bin/prodman")):
            self.assertEqual(os.readlink(os.path.join(self.home, name)),
                             os.path.join(self.cprod, ".prod", "current", target))
        self.assertNotIn("does not point to", out.stdout)

    def test_errors(self):
        out = self.install(check=False)  # no prod.conf yet and no --role
        self.assertIn("give --role", out.stderr)
        out = self.install("--role", "client", check=False)
        self.assertIn("a client needs --server", out.stderr)
        out = self.install("--role", "boss", "--server", self.products, check=False)
        self.assertIn("invalid --role or --server", out.stderr)
        out = self.install("--role", "server", version="bad/name", check=False)
        self.assertIn("invalid version name", out.stderr)
        out = subprocess.run([os.path.join(REPO, "install.sh"), "--products_dir", os.path.join(self.tmp, "nope"),
                              "--version-name", "v1"], env=self.env(), capture_output=True, text=True)
        self.assertIn("does not exist", out.stderr)
        out = self.install("--role", "server", version="", check=False)  # without --version-name
        if not os.path.isdir(os.path.join(REPO, ".git")):
            self.assertIn("not a git checkout", out.stderr)


@unittest.skipUnless(HAVE_MODULES, "environment-modules is not installed")
class TestSetupProd(Installed):
    def setUp(self):
        super().setUp()
        self.install("--role", "client", "--server", self.products)
        self.sync("3.0", "3.1")
        self.setup = os.path.join(self.home, "setup_prod.sh")
        # Fixed modulefiles hold the path of the server where they were released. On real nodes that is
        # the same /home/cupsoft/Products everywhere; in this sandbox it is the fake server's directory.
        self.sp = self.products

    def test_load_and_switch(self):
        r = self.bash(f"""set -eu
. {self.setup} 3.0
echo "VLIST=$PROD_VLIST"
echo "PYTHON_HOME=$PYTHON_HOME"
echo "ROOTSYS=$ROOTSYS"
. {self.setup} 3.1
echo "VLIST=$PROD_VLIST"
echo "ROOTSYS=${{ROOTSYS:-unset}}"
echo "HDF5SYS=$HDF5SYS"
echo "PATH=$PATH"
. {self.setup} 3.1
echo "PATH=$PATH"
""")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stderr, "")  # quiet when not interactive
        lines = r.stdout.splitlines()
        self.assertIn("VLIST=3.0", lines)
        self.assertIn(f"PYTHON_HOME={self.sp}/PYTHON/V3.11.14", lines)
        self.assertIn(f"ROOTSYS={self.sp}/ROOT/V6.32.20_3.11.14", lines)
        self.assertIn("VLIST=3.1", lines)
        self.assertIn("ROOTSYS=unset", lines)  # switching vlists unloads ROOT
        self.assertIn(f"HDF5SYS={self.sp}/HDF5/V1.14.2", lines)
        paths = [l for l in lines if l.startswith("PATH=")]
        self.assertEqual(paths[0], paths[1])  # sourcing the same vlist twice changes nothing
        entries = paths[0][5:].split(":")
        self.assertEqual(len(entries), len(set(entries)))  # no duplicates
        self.assertNotIn(f"{self.sp}/ROOT/V6.32.20_3.11.14/bin", entries)
        self.assertIn(f"{self.sp}/HDF5/V1.14.2/bin", entries)

    def test_errors_return_nonzero(self):
        r = self.bash(f". {self.setup} 9.9; echo rc=$?")
        self.assertIn("rc=1", r.stdout)
        self.assertIn("run 'prodman sync 9.9'", r.stderr)
        r = self.bash(f"set -e; . {self.setup} 9.9; echo not reached")
        self.assertNotEqual(r.returncode, 0)
        self.assertNotIn("not reached", r.stdout)

    def test_without_vlist_only_module_is_set_up(self):
        script = f"""set -eu
. {self.setup}
echo "rc=$? VLIST=${{PROD_VLIST:-none}}"
MODULES_VERBOSITY=concise module load ROOT/V6.32.20_3.11.14
echo "ROOTSYS=$ROOTSYS"
"""
        r = self.bash(script)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stderr, "")  # quiet when not interactive
        self.assertIn("rc=0 VLIST=none", r.stdout)
        self.assertIn(f"ROOTSYS={self.sp}/ROOT/V6.32.20_3.11.14", r.stdout)
        r = subprocess.run(["bash", "--noprofile", "--norc", "-i", "-c", f". {self.setup}; echo rc=$?"],
                           env=self.env(), capture_output=True, text=True, stdin=subprocess.DEVNULL)
        self.assertIn("rc=0", r.stdout)
        self.assertIn("releases on this node:", r.stderr)
        self.assertIn("  3.0          PYTHON/V3.11.14 ROOT/V6.32.20_3.11.14", r.stderr)
        self.assertIn("module load <NAME>/<VERSION>", r.stderr)

    def test_interactive_summary(self):
        r = subprocess.run(["bash", "--noprofile", "--norc", "-i", "-c", f". {self.setup} 3.0"],
                           env=self.env(), capture_output=True, text=True, stdin=subprocess.DEVNULL)
        self.assertIn("prod: vlist 3.0: PYTHON/V3.11.14 ROOT/V6.32.20_3.11.14", r.stderr)

    def test_root_alone_loads_its_dependency(self):
        r = self.bash(f"""set -eu
. /usr/share/Modules/init/bash
module use {self.cprod}/modulefiles
module load ROOT/V6.32.20_3.11.14
echo "PYTHON_HOME=$PYTHON_HOME"
module unload ROOT
echo "after=${{PYTHON_HOME:-unset}}"
module load PYTHON/V3.11.14
module load HDF5/V1.14.2
""")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn(f"PYTHON_HOME={self.sp}/PYTHON/V3.11.14", r.stdout)
        # Modules 5 also unloads the PYTHON that was loaded only as ROOT's requirement
        self.assertIn("after=unset", r.stdout)

    def test_wrong_dependency_version_is_refused(self):
        other = os.path.join(self.cprod, "modulefiles", "PYTHON", "V9.9")
        with fsutil.opened(os.path.join(self.cprod, "modulefiles")):  # read-only after sync
            with open(other, "w") as f:
                f.write("#%Module\nconflict PYTHON\nsetenv PYTHON_HOME /somewhere/else\n")
        r = self.bash(f""". /usr/share/Modules/init/bash
module use {self.cprod}/modulefiles
module load PYTHON/V9.9
module load ROOT/V6.32.20_3.11.14; echo rc=$?
echo "ROOTSYS=${{ROOTSYS:-unset}}"
""")
        self.assertIn("needs PYTHON V3.11.14, but another PYTHON is loaded", " ".join(r.stderr.split()))  # Modules wraps lines
        self.assertIn("ROOTSYS=unset", r.stdout)


if __name__ == "__main__":
    unittest.main()


FAKE_RPM = """#!/bin/bash
# fake rpm: every package is installed except those in $FAKE_MISSING
rc=0
for a in "$@"; do
    case "$a" in -q|--qf|%*) continue ;; esac
    if [[ " ${FAKE_MISSING:-} " == *" $a "* ]]; then echo "package $a is not installed"; rc=1; else echo "$a"; fi
done
exit $rc
"""

FAKE_DNF = """#!/bin/bash
# fake dnf: logs its arguments; 'install' fails if any package is in $FAKE_FAIL; repoquery hides $FAKE_FAIL
echo "dnf $*" >> "$FAKE_LOG"
args=("$@")
for a in "${args[@]}"; do
    if [[ " ${FAKE_FAIL:-} " == *" $a "* ]]; then exit 1; fi
done
exit 0
"""


class TestEnvironmentAndPostinstall(Installed):
    def setUp(self):
        super().setUp()
        self.install("--role", "client", "--server", self.products)
        self.sync("3.0")
        self.bin = os.path.join(self.tmp, "fakebin")
        os.makedirs(self.bin)
        for name, text in (("rpm", FAKE_RPM), ("dnf", FAKE_DNF)):
            with open(os.path.join(self.bin, name), "w") as f:
                f.write(text)
            os.chmod(os.path.join(self.bin, name), 0o755)
        self.prod = os.path.join(self.home, ".local", "bin", "prodman")
        self.dnf_log = os.path.join(self.tmp, "dnf.log")

    def run_prod(self, *args, missing=""):
        return subprocess.run([self.prod] + list(args), capture_output=True, text=True, stdin=subprocess.DEVNULL,
                              env=self.env(PROD_RPM=os.path.join(self.bin, "rpm"), FAKE_MISSING=missing))

    def postinstall(self, *args, fail=""):
        script = os.path.join(self.cprod, ".prod", "current", "share", "postinstall.sh")
        return subprocess.run([script] + list(args), capture_output=True, text=True, stdin=subprocess.DEVNULL,
                              env=self.env(PROD_ALLOW_NONROOT="1", PROD_DNF=os.path.join(self.bin, "dnf"),
                                           PROD_RPM=os.path.join(self.bin, "rpm"), FAKE_FAIL=fail,
                                           FAKE_LOG=self.dnf_log,
                                           PROD_POSTINSTALL_LOG=os.path.join(self.tmp, "postinstall.log")))

    @unittest.skipUnless(HAVE_MODULES, "environment-modules is not installed")
    def test_verify_checks_the_environment(self):
        r = self.run_prod("verify")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertNotIn("warning", r.stdout)  # loads; packages installed; no missing libraries
        r = self.run_prod("verify", "--quick", missing="gdbm-devel")
        self.assertEqual(r.returncode, 0, r.stdout)  # a warning, not an error
        self.assertIn("1 required system package(s) not installed: gdbm-devel", r.stdout)
        # a vlist that no longer loads is an error
        fsutil.unlock_tree(os.path.join(self.cprod, "modulefiles"))
        with open(os.path.join(self.cprod, "modulefiles", "vlist", "3.0"), "a") as f:
            f.write('error "broken on purpose"\n')
        r = self.run_prod("verify", "--quick")
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("'. setup_prod.sh 3.0' fails", r.stdout)

    @unittest.skipUnless(HAVE_MODULES, "environment-modules is not installed")
    def test_sync_checks_the_environment(self):
        r = self.run_prod("sync", "3.1", missing="gdbm-devel")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("warning: 1 required system package(s) not installed: gdbm-devel", r.stdout)

    @unittest.skipUnless(HAVE_MODULES, "environment-modules is not installed")
    def test_release_refuses_a_vlist_that_does_not_load(self):
        from prod import release
        with open(os.path.join(self.tool_root, "modulefiles", "HDF5", "template.tcl"), "a") as f:
            f.write('error "broken on purpose"\n')
        self.version("HDF5", "V1.14.8", {"bin/h5dump": b"#!/bin/sh\n"})
        self.recipe("HDF5", "V1.14.8")
        self.draft("broken", [("HDF5", "V1.14.8")])
        with self.assertRaisesRegex(CheckFailed, "the vlist does not load"):
            release.release(self.paths, self.cfg, "broken", tool_root=self.tool_root, tool="t",
                            check_environment=True)
        self.assertFalse(os.path.exists(self.paths.vlist_file("broken")))  # nothing was released
        self.assertFalse(os.path.exists(self.paths.manifest_file("HDF5", "V1.14.8")))

    @unittest.skipUnless(HAVE_MODULES and shutil.which("gcc"), "needs environment-modules and gcc")
    def test_ldd_finds_missing_library(self):
        build = os.path.join(self.tmp, "build")
        os.makedirs(build)
        with open(os.path.join(build, "foo.c"), "w") as f:
            f.write("int foo(void) { return 1; }\n")
        with open(os.path.join(build, "main.c"), "w") as f:
            f.write("int foo(void);\nint main(void) { return foo(); }\n")
        subprocess.run(["gcc", "-shared", "-fPIC", "-o", os.path.join(build, "libprodfoo.so"),
                        os.path.join(build, "foo.c")], check=True)
        self.version("HDF5", "V1.14.9", {"bin/h5dump": b"#!/bin/sh\n"})
        vdir = os.path.join(self.products, "HDF5", "V1.14.9")
        # the same program in places where its missing library is not listed: a Qt plugin (on demand), and next
        # to a bundled copy of the library (as pip wheels do)
        for rel in ("bin/needsfoo", "lib/Qt6/plugins/sqldrivers/libqsqlfoo.so", "lib/bundled/needsfoo"):
            os.makedirs(os.path.dirname(os.path.join(vdir, rel)), exist_ok=True)
            subprocess.run(["gcc", "-o", os.path.join(vdir, rel), os.path.join(build, "main.c"), "-L" + build,
                            "-lprodfoo"], check=True)
        shutil.copy(os.path.join(build, "libprodfoo.so"), os.path.join(vdir, "lib", "bundled"))
        self.recipe("HDF5", "V1.14.9")
        self.draft("ldd", [("HDF5", "V1.14.9")])
        from prod import release
        rp = release.release(self.paths, self.cfg, "ldd", tool_root=self.tool_root, tool="t", check_environment=True)
        [w] = [x for x in rp.warnings if "shared libraries" in x]  # a warning to look at, not a failure
        self.assertIn("1 missing shared libraries in 4 ELF files", w)
        self.sync("ldd")
        r = self.run_prod("verify", "ldd")
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn("1 missing shared libraries in 4 ELF files", r.stdout)
        self.assertIn("HDF5/V1.14.9/bin/needsfoo: libprodfoo.so", r.stdout)
        self.assertNotIn("bundled", r.stdout)

    def test_postinstall(self):
        r = self.postinstall("--check")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("check only", r.stdout)
        self.assertFalse(os.path.exists(self.dnf_log) and "install" in open(self.dnf_log).read())

        r = self.postinstall(fail="gdbm-devel R-Rcpp-devel")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("group install failed; retrying the packages one by one", r.stdout)
        self.assertIn("python   failed  failed: gdbm-devel", r.stdout)
        self.assertIn("root     ok      failed: -   optional failed: R-Rcpp-devel", r.stdout)
        with open(self.dnf_log) as f:
            log = f.read()
        self.assertIn("dnf -y config-manager --set-enabled crb", log)
        self.assertIn("dnf -y install gdbm-devel", log)  # retried alone
        self.assertNotIn("update", log)
        self.assertFalse(os.path.exists(os.path.join(self.cprod, ".prod", "state", "packages.yml")))  # no record

        r = self.postinstall()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_postinstall_needs_root(self):
        script = os.path.join(self.cprod, ".prod", "current", "share", "postinstall.sh")
        r = subprocess.run([script, "--check"], capture_output=True, text=True, env=self.env())
        if os.geteuid() != 0:
            self.assertEqual(r.returncode, 1)
            self.assertIn("run as root", r.stderr)


class TestClassifyMissing(unittest.TestCase):
    def test_classify(self):
        from prod.envcheck import classify_missing
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp)
        v = os.path.join(tmp, "PYTHON", "V3")
        sp = os.path.join(v, "lib", "site-packages")
        os.makedirs(os.path.join(sp, "h5py.libs"))
        open(os.path.join(sp, "h5py.libs", "libsz-1.so"), "w").close()
        files = {
            "bundled": (os.path.join(sp, "h5py.libs", "libhdf5-1.so"), "libsz-1.so"),
            "sql plugin": (os.path.join(sp, "PySide6", "Qt", "plugins", "sqldrivers", "libqsqloci.so"), "libclntsh.so"),
            "qml": (os.path.join(sp, "PyQt5", "Qt5", "qml", "QtGamepad", "libplugin.so"), "libQt5Gamepad.so.5"),
            "platform plugin": (os.path.join(sp, "PyQt6", "Qt6", "plugins", "platforms", "libqxcb.so"),
                                "libxcb-cursor.so.0"),
            "optional": (os.path.join(sp, "numba", "tbbpool.so"), "libtbb.so.12"),
            "real": (os.path.join(sp, "pyodbc.so"), "libodbc.so.2"),
        }
        missing = list(files.values())
        owner = {f: v for f, _ in missing}
        real, on_demand = classify_missing(missing, owner)
        self.assertEqual(real, [files["platform plugin"], files["optional"], files["real"]])
        self.assertEqual(on_demand, {files["sql plugin"][0], files["qml"][0]})


class TestParseLdd(unittest.TestCase):
    def test_parse(self):
        from prod.envcheck import parse_ldd
        one = "\tlinux-vdso.so.1 (0x00007ffd)\n\tlibfoo.so => not found\n\tlibc.so.6 => /lib64/libc.so.6 (0x7f)\n"
        self.assertEqual(parse_ldd(one, ["/p/bin/a"]), [("/p/bin/a", "libfoo.so")])
        many = ("/p/bin/a:\n\tlibc.so.6 => /lib64/libc.so.6 (0x7f)\n"
                "/p/lib/b.so:\n\tlibbar.so.1 => not found\n\tlibbaz.so => not found\n"
                "/p/bin/static:\n\tnot a dynamic executable\n")
        self.assertEqual(parse_ldd(many, ["/p/bin/a", "/p/lib/b.so", "/p/bin/static"]),
                         [("/p/lib/b.so", "libbar.so.1"), ("/p/lib/b.so", "libbaz.so")])
