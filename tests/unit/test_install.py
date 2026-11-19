"""install.sh and shell/setup_prod.sh, run in a temporary HOME and Products (never the real ones)."""

import os
import shutil
import subprocess
import unittest

from prod import config, fsutil, state

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

    def install(self, *args, check=True, version="v0.1.0"):
        cmd = [os.path.join(REPO, "install.sh"), "--products", self.cprod]
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
        out = self.install("--role", "client", "--server", self.products)
        self.assertIn("installed prod v0.1.0", out.stdout)
        prod = os.path.join(self.cprod, ".prod")
        self.assertEqual(os.readlink(os.path.join(prod, "current")), "versions/v0.1.0")
        v = os.path.join(prod, "versions", "v0.1.0")
        for rel in ("bin/prod", "lib/prod/cli.py", "share/setup_prod.sh", "share/setup_prod.csh",
                    "share/modulefiles", "share/recipes", "share/packages", "VERSION"):
            self.assertTrue(os.path.exists(os.path.join(v, rel)), rel)
        self.assertFalse(any("__pycache__" in d for d, _, _ in os.walk(v)))
        self.assertFalse(os.lstat(os.path.join(v, "lib", "prod", "cli.py")).st_mode & 0o222)  # read-only
        with open(os.path.join(v, "share", "setup_prod.sh")) as f:
            self.assertIn(f'local products="{self.cprod}"', f.read())
        cfg = config.load(config.Paths(self.cprod), env={})
        self.assertEqual((cfg.role, cfg.server.path), ("client", self.products))
        self.assertEqual(os.readlink(os.path.join(self.home, "setup_prod.sh")),
                         os.path.join(prod, "current", "share", "setup_prod.sh"))
        self.assertEqual(os.readlink(os.path.join(self.home, ".local", "bin", "prod")),
                         os.path.join(prod, "current", "bin", "prod"))
        self.assertEqual([e["command"] for e in state.read_history(self.cpaths)][-2:], ["install", "doctor"])
        # the installed prod finds its Products from its own location
        out = subprocess.run([os.path.join(self.home, ".local", "bin", "prod"), "list"], env=self.env(),
                             capture_output=True, text=True)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("tool    v0.1.0", out.stdout)
        self.assertIn(f"role    client, server {self.products}", out.stdout)

    def test_upgrade_keeps_config_and_old_versions(self):
        self.install("--role", "client", "--server", self.products)
        with open(self.cpaths.conf, "ab") as f:
            f.write(b'ssh_options: ["-o", "BatchMode=yes"]\n')
        with open(self.cpaths.conf, "rb") as f:
            conf = f.read()
        for i in range(2, 6):
            self.install(version=f"v0.{i}.0")
        with open(self.cpaths.conf, "rb") as f:
            self.assertEqual(conf, f.read())
        self.assertEqual(os.readlink(os.path.join(self.cprod, ".prod", "current")), "versions/v0.5.0")
        self.assertEqual(sorted(os.listdir(os.path.join(self.cprod, ".prod", "versions"))),
                         ["v0.3.0", "v0.4.0", "v0.5.0"])
        # the same version again needs --force
        out = self.install(version="v0.5.0", check=False)
        self.assertNotEqual(out.returncode, 0)
        self.assertIn("already installed", out.stderr)
        self.install("--force", version="v0.5.0")

    def test_legacy_setup_prod_is_kept(self):
        legacy = os.path.join(self.home, "setup_prod.sh")
        with open(legacy, "w") as f:
            f.write("# legacy\n")
        out = self.install("--role", "client", "--server", self.products)
        self.assertIn("setup_prod.sh.pre-prod", out.stdout)
        with open(legacy + ".pre-prod") as f:
            self.assertEqual(f.read(), "# legacy\n")
        self.assertTrue(os.path.islink(legacy))

    def test_keep_legacy_setup(self):
        legacy = os.path.join(self.home, "setup_prod.sh")
        with open(legacy, "w") as f:
            f.write("# legacy\n")
        out = self.install("--role", "server")  # keeping it is the default on a server
        self.assertIn("~/setup_prod.sh is left as it is", out.stdout)
        self.assertFalse(os.path.islink(legacy))
        with open(legacy) as f:
            self.assertEqual(f.read(), "# legacy\n")
        # nothing new that the legacy install_products.sh would copy with 'setup_prod*'
        self.assertEqual(sorted(n for n in os.listdir(self.home) if n.startswith("setup_prod")), ["setup_prod.sh"])
        self.assertTrue(os.path.islink(os.path.join(self.home, ".local", "bin", "prod")))
        self.assertIn("ok    tool", out.stdout)  # doctor accepts the kept legacy script
        self.assertNotIn("does not point to", out.stdout)
        # an upgrade keeps it too; only --replace-legacy-setup switches
        self.install(version="v0.2.0")
        self.assertFalse(os.path.islink(legacy))
        out = self.install("--replace-legacy-setup", version="v0.3.0")
        self.assertTrue(os.path.islink(legacy))
        with open(legacy + ".pre-prod") as f:
            self.assertEqual(f.read(), "# legacy\n")

    def test_client_can_keep_legacy_setup_too(self):
        legacy = os.path.join(self.home, "setup_prod.sh")
        with open(legacy, "w") as f:
            f.write("# legacy\n")
        self.install("--role", "client", "--server", self.products, "--keep-legacy-setup")
        self.assertFalse(os.path.islink(legacy))

    def test_errors(self):
        out = self.install(check=False)  # no prod.conf yet and no --role
        self.assertIn("give --role", out.stderr)
        out = self.install("--role", "client", check=False)
        self.assertIn("a client needs --server", out.stderr)
        out = self.install("--role", "boss", "--server", self.products, check=False)
        self.assertIn("invalid --role or --server", out.stderr)
        out = self.install("--role", "server", version="bad/name", check=False)
        self.assertIn("invalid version name", out.stderr)
        out = subprocess.run([os.path.join(REPO, "install.sh"), "--products", os.path.join(self.tmp, "nope"),
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
echo "PRODUCTS_DIR=$PRODUCTS_DIR"
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
        self.assertIn(f"PRODUCTS_DIR={self.cprod}", lines)
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
        r = self.bash(f". {self.setup}; echo rc=$?")
        self.assertIn("rc=1", r.stdout)
        self.assertIn("usage:", r.stderr)
        self.assertIn("  3.0", r.stderr)
        r = self.bash(f". {self.setup} 9.9; echo rc=$?")
        self.assertIn("rc=1", r.stdout)
        self.assertIn("run 'prod sync 9.9'", r.stderr)
        r = self.bash(f"set -e; . {self.setup} 9.9; echo not reached")
        self.assertNotEqual(r.returncode, 0)
        self.assertNotIn("not reached", r.stdout)

    def test_interactive_summary_and_withdrawn_warning(self):
        from prod import release
        release.withdraw(self.paths, self.cfg, "3.0", tool="t", reason="bad ROOT", replaced_by="3.1")
        self.sync("3.0", force=True)
        r = subprocess.run(["bash", "--noprofile", "--norc", "-i", "-c", f". {self.setup} 3.0"],
                           env=self.env(), capture_output=True, text=True, stdin=subprocess.DEVNULL)
        self.assertIn('vlist 3.0 was withdrawn: "bad ROOT"', r.stderr)
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


class TestDoctorAndPostinstall(Installed):
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
        self.prod = os.path.join(self.home, ".local", "bin", "prod")
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
    def test_doctor_ok(self):
        r = self.run_prod("doctor")
        self.assertIn("ok    tool", r.stdout)
        self.assertIn("ok    server           reachable, 2 released vlists", r.stdout)
        self.assertIn("ok    packages", r.stdout)
        self.assertIn("groups: base, python, root", r.stdout)  # groups follow the vlist's products
        self.assertIn("WARN  record", r.stdout)  # postinstall has not run here
        self.assertIn("ok    modules 3.0", r.stdout)
        self.assertIn("ok    ldd 3.0          0 ELF files", r.stdout)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_doctor_missing_package(self):
        r = self.run_prod("doctor", "--quick", missing="gdbm-devel")
        self.assertEqual(r.returncode, 1)
        self.assertIn("FAIL  packages         1 required package(s) missing", r.stdout)
        self.assertIn("missing: gdbm-devel", r.stdout)

    @unittest.skipUnless(HAVE_MODULES and shutil.which("gcc"), "needs environment-modules and gcc")
    def test_doctor_ldd_finds_missing_library(self):
        build = os.path.join(self.tmp, "build")
        os.makedirs(build)
        with open(os.path.join(build, "foo.c"), "w") as f:
            f.write("int foo(void) { return 1; }\n")
        with open(os.path.join(build, "main.c"), "w") as f:
            f.write("int foo(void);\nint main(void) { return foo(); }\n")
        subprocess.run(["gcc", "-shared", "-fPIC", "-o", os.path.join(build, "libprodfoo.so"),
                        os.path.join(build, "foo.c")], check=True)
        self.version("HDF5", "V1.14.9", {"bin/h5dump": b"#!/bin/sh\n"})
        subprocess.run(["gcc", "-o", os.path.join(self.products, "HDF5", "V1.14.9", "bin", "needsfoo"),
                        os.path.join(build, "main.c"), "-L" + build, "-lprodfoo"], check=True)
        self.recipe("HDF5", "V1.14.9")
        self.draft("ldd", [("HDF5", "V1.14.9")])
        self.release("ldd")
        self.sync("ldd")
        r = self.run_prod("doctor", "ldd")
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("FAIL  ldd ldd          1 missing shared libraries in 1 ELF files", r.stdout)
        self.assertIn("HDF5/V1.14.9/bin/needsfoo: libprodfoo.so", r.stdout)

    def test_postinstall(self):
        r = self.postinstall("--check")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("check only", r.stdout)
        self.assertFalse(os.path.exists(self.cpaths.packages_record))
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
        from prod import packages
        with open(self.cpaths.packages_record, "rb") as f:
            rec = packages.record_loads(f.read())
        self.assertEqual(rec["groups"]["python"]["failed"], ["gdbm-devel"])
        self.assertEqual(rec["groups"]["root"]["optional_failed"], ["R-Rcpp-devel"])
        r = self.run_prod("doctor", "--quick")
        self.assertIn("WARN  record", r.stdout)
        self.assertIn("groups that failed when applied: python", r.stdout)

        r = self.postinstall()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        r = self.run_prod("doctor", "--quick")
        self.assertIn("ok    record", r.stdout)

    def test_postinstall_needs_root(self):
        script = os.path.join(self.cprod, ".prod", "current", "share", "postinstall.sh")
        r = subprocess.run([script, "--check"], capture_output=True, text=True, env=self.env())
        if os.geteuid() != 0:
            self.assertEqual(r.returncode, 1)
            self.assertIn("run as root", r.stderr)


class TestParseLdd(unittest.TestCase):
    def test_parse(self):
        from prod.doctor import parse_ldd
        one = "\tlinux-vdso.so.1 (0x00007ffd)\n\tlibfoo.so => not found\n\tlibc.so.6 => /lib64/libc.so.6 (0x7f)\n"
        self.assertEqual(parse_ldd(one, ["/p/bin/a"]), [("/p/bin/a", "libfoo.so")])
        many = ("/p/bin/a:\n\tlibc.so.6 => /lib64/libc.so.6 (0x7f)\n"
                "/p/lib/b.so:\n\tlibbar.so.1 => not found\n\tlibbaz.so => not found\n"
                "/p/bin/static:\n\tnot a dynamic executable\n")
        self.assertEqual(parse_ldd(many, ["/p/bin/a", "/p/lib/b.so", "/p/bin/static"]),
                         [("/p/lib/b.so", "libbar.so.1"), ("/p/lib/b.so", "libbaz.so")])
