"""The real modulefile sources, recipes and package lists of this repository."""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest

from prod import modulefile, packages, recipe

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SRC = os.path.join(REPO, "modulefiles")
RECIPES = os.path.join(REPO, "recipes")
sys.path.insert(0, os.path.join(SRC, "_common"))
import sh2mod  # noqa: E402

# The legacy vlists to migrate, in their order.
# 1.0 and 2.0 are not migrated: they use Anaconda, which is no longer used.
LEGACY = {
    "3.0": [("PYTHON", "V3.11.14"), ("ROOT", "V6.32.20_3.11.14"), ("HDF5", "V1.14.2")],
    "3.1": [("PYTHON", "V3.11.14"), ("ROOT", "V6.32.20_3.11.14"), ("HDF5", "V1.14.2"), ("GEANT4", "V11.1.3"),
            ("CAENHVWRAPPER", "V6.6")],
    "daq_1.0": [("PYTHON", "V3.11.14"), ("ROOT", "V6.32.20_3.11.14"), ("HDF5", "V1.14.2"), ("CAENHVWRAPPER", "V6.6")],
    "daq_2.0": [("PYTHON", "V3.11.14"), ("ROOT", "V6.32.20_3.11.14"), ("HDF5", "V1.14.2"), ("CAENHVWRAPPER", "V6.6"),
                ("CUPDATAMODEL", "V1.1"), ("CUPDAQ", "V4.2")],
    "daq_2.1": [("PYTHON", "V3.11.14"), ("ROOT", "V6.32.20_3.11.14"), ("HDF5", "V1.14.2"), ("CAENHVWRAPPER", "V6.6"),
                ("CUPDATAMODEL", "V1.1"), ("CUPDAQ", "V4.3")],
    "mon_1.0": [("PYTHON", "V3.11.14"), ("ROOT", "V6.32.20_3.11.14"), ("CAENHVWRAPPER", "V6.6")],
}
MODULECMD = shutil.which("modulecmd.tcl") or "/usr/share/Modules/libexec/modulecmd.tcl"


class TestRealSources(unittest.TestCase):
    def test_every_template_loads(self):
        names = sorted(d for d in os.listdir(SRC) if not d.startswith("_") and os.path.isdir(os.path.join(SRC, d)))
        self.assertEqual(names, ["CAENHVWRAPPER", "CUPDAQ", "CUPDATAMODEL", "GEANT4", "HDF5", "PYTHON", "ROOT"])
        homes = {n: modulefile.load_source(SRC, n).home_var for n in names}
        self.assertEqual(homes["ROOT"], "ROOTSYS")
        self.assertEqual(homes["GEANT4"], "G4BASE")
        self.assertEqual(homes["HDF5"], "HDF5SYS")
        self.assertEqual(len(set(homes.values())), len(homes))

    def test_every_recipe_loads_and_has_a_template(self):
        count = 0
        for name in sorted(os.listdir(RECIPES)):
            for f in sorted(os.listdir(os.path.join(RECIPES, name))):
                if f.endswith(".yml"):
                    r = recipe.load(RECIPES, name, f[:-4])
                    modulefile.load_source(SRC, name)
                    for d, _ in r.depends:
                        modulefile.load_source(SRC, d)
                    count += 1
        self.assertEqual(count, 14)

    def test_legacy_vlists_satisfy_dependency_order(self):
        """What prod release --migrate will check, before touching the server."""
        for name, products in LEGACY.items():
            for i, (p, v) in enumerate(products):
                for d, dv in recipe.load(RECIPES, p, v).depends:
                    self.assertIn((d, dv), products[:i], f"{name}: {p} {v} needs {d} {dv} earlier in the list")

    def test_package_lists(self):
        groups = packages.load_all(os.path.join(REPO, "packages"))
        covered = {p for g in groups for p in g.products}
        templates = {d for d in os.listdir(SRC) if not d.startswith("_")}
        self.assertLessEqual(covered, templates)


@unittest.skipUnless(os.path.exists(MODULECMD) and shutil.which("tclsh"), "needs environment-modules and tclsh")
class TestSh2mod(unittest.TestCase):
    def test_capture(self):
        with tempfile.TemporaryDirectory() as d:
            prefix = os.path.join(d, "V1")
            os.makedirs(os.path.join(prefix, "bin"))
            script = os.path.join(prefix, "bin", "setup.sh")
            with open(script, "w") as f:
                f.write(f"""
export FOO_HOME="{prefix}"
export PATH="{prefix}/bin:$PATH"
export LD_LIBRARY_PATH="/usr/lib64:{prefix}/lib${{LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}}"
export MANPATH="{prefix}/man:/usr/share/man"
export WORKDIR="$HOME/work dir"
export DYLD_LIBRARY_PATH="{prefix}/lib"
export WEIRD='a "quoted" value [x]'
myfunc() {{ echo hi; }}
""")
            out = sh2mod.capture(prefix, script, drop={"FOO_HOME"})
            lines = out.splitlines()
            self.assertIn(f'prepend-path PATH "{prefix}/bin"', lines)
            self.assertIn(f'prepend-path LD_LIBRARY_PATH "{prefix}/lib"', lines)
            self.assertIn(f'prepend-path MANPATH "{prefix}/man"', lines)
            self.assertIn("append-path MANPATH {}", lines)
            self.assertIn('setenv WORKDIR "$::env(HOME)/work dir"', lines)
            self.assertIn(r'setenv WEIRD "a \"quoted\" value \[x\]"', lines)
            self.assertIn("# dropped: LD_LIBRARY_PATH entry /usr/lib64 (outside the version directory)", lines)
            self.assertIn("# dropped: variable DYLD_LIBRARY_PATH", lines)
            self.assertIn("# dropped: variable FOO_HOME", lines)
            self.assertIn("# dropped: function myfunc", lines)
            self.assertFalse(any(l.startswith("setenv FOO_HOME") for l in lines))
            r = subprocess.run(["tclsh"], input=f"puts [info complete {{{out}}}]", capture_output=True, text=True)
            self.assertEqual(r.stdout.strip(), "1")

    def test_prefix_through_a_symlink(self):
        # ROOT 6.40's thisroot.sh resolves its location physically; the module must keep the given prefix
        with tempfile.TemporaryDirectory() as d:
            real = os.path.join(d, "disk", "Products", "ROOT", "V1")
            os.makedirs(os.path.join(real, "bin"))
            os.symlink(os.path.join(d, "disk", "Products"), os.path.join(d, "Products"))
            prefix = os.path.join(d, "Products", "ROOT", "V1")
            script = os.path.join(real, "bin", "setup.sh")
            with open(script, "w") as f:
                f.write(f'export PATH="{real}/bin:$PATH"\nexport JUPYTER_CONFIG_PATH="{real}/etc/notebook"\n'
                        f'export OTHER="{real}2/x"\n')
            lines = sh2mod.capture(prefix, script).splitlines()
            self.assertIn(f'prepend-path PATH "{prefix}/bin"', lines)
            self.assertIn(f'setenv JUPYTER_CONFIG_PATH "{prefix}/etc/notebook"', lines)
            self.assertIn(f'setenv OTHER "{real}2/x"', lines)  # only the version directory itself is mapped

    def test_backslash_value_is_refused(self):
        with tempfile.TemporaryDirectory() as d:
            script = os.path.join(d, "setup.sh")
            with open(script, "w") as f:
                f.write("export ODD='costs $5'\n")
            with self.assertRaisesRegex(SystemExit, "ODD contains a backslash"):
                sh2mod.capture(d, script)
            self.assertNotIn("ODD", sh2mod.capture(d, script, drop={"ODD"}).replace("variable ODD", ""))


if __name__ == "__main__":
    unittest.main()
