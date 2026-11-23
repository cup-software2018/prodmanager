"""recipes/_common/save-recipe.py: the recipe saved next to a version, read by `prod release`."""

import os
import shutil
import subprocess
import tempfile
import unittest

from prod import recipe

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SCRIPT = os.path.join(REPO, "recipes", "_common", "save-recipe.py")
RECIPE = b'format: "1"\nproduct: "CUPDAQ"\nversion: "V4.3.2"\ndepends:\n  - {name: "HDF5", version: "V2.2.0"}\n'


class TestSaveRecipe(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="prod-test-")
        self.products = os.path.join(self.tmp, "Products")
        self.repo = os.path.join(self.tmp, "repo")
        self.recipe = os.path.join(self.repo, "recipes", "CUPDAQ", "V4.3.2.yml")
        os.makedirs(os.path.dirname(self.recipe))
        with open(self.recipe, "wb") as f:
            f.write(RECIPE)
        os.makedirs(os.path.join(self.products, "CUPDAQ", "V4.3.2"))

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def run_script(self, *args):
        return subprocess.run([SCRIPT, *args, "--products", self.products], capture_output=True, text=True,
                              check=False)

    def git(self, *args):
        subprocess.run(["git", "-C", self.repo, "-c", "user.name=t", "-c", "user.email=t@example.invalid", *args],
                       capture_output=True, check=True)

    def saved(self):
        return recipe.find(self.products, None, "CUPDAQ", "V4.3.2")

    def test_in_git(self):
        self.git("init", "-q")
        self.git("add", ".")
        self.git("commit", "-q", "-m", "x")
        r = self.run_script(self.recipe)
        self.assertEqual(r.returncode, 0, r.stderr)
        rec = self.saved()
        self.assertTrue(rec.saved)
        self.assertEqual(rec.depends, [("HDF5", "V2.2.0")])
        self.assertRegex(rec.built, r"^recipes/CUPDAQ/V4.3.2.yml at commit [0-9a-f]{12} on \S+ at \S+$")
        with open(recipe.saved_path(self.products, "CUPDAQ", "V4.3.2"), "rb") as f:
            self.assertTrue(f.read().startswith(RECIPE))  # the recipe as it is, plus the 'built' line
        # a change in recipes/<NAME>/ that is not committed is recorded
        with open(os.path.join(os.path.dirname(self.recipe), "V4.3.2.options"), "w") as f:
            f.write("-DX=1\n")
        self.assertEqual(self.run_script(self.recipe).returncode, 0)
        self.assertIn(", with uncommitted changes in recipes/CUPDAQ/ on ", self.saved().built)

    def test_not_in_git(self):
        self.assertEqual(self.run_script(self.recipe).returncode, 0)
        self.assertIn(f"{self.recipe} (not in a git checkout) on ", self.saved().built)

    def test_refusals(self):
        vdir = os.path.join(self.products, "CUPDAQ", "V4.3.2")
        saved = recipe.saved_path(self.products, "CUPDAQ", "V4.3.2")

        def refused(path, message):
            r = self.run_script(path)
            self.assertNotEqual(r.returncode, 0)
            self.assertIn(message, r.stderr)

        with open(vdir + ".manifest", "w") as f:
            f.write("x")
        refused(self.recipe, "already released")
        os.unlink(vdir + ".manifest")
        os.rmdir(vdir)
        refused(self.recipe, "no version directory")
        os.makedirs(vdir)
        with open(self.recipe, "ab") as f:
            f.write(b'built: "x"\n')
        refused(self.recipe, "already has a 'built' line")
        with open(self.recipe, "wb") as f:
            f.write(RECIPE.replace(b"V4.3.2", b"V4.3.1", 1))
        refused(self.recipe, "do not match the file location")
        refused(saved, "is a saved copy")
        self.assertFalse(os.path.exists(saved))  # none of them wrote anything

if __name__ == "__main__":
    unittest.main()
