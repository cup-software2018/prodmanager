import os
import shutil
import stat
import tempfile
import unittest

from prod import manifest
from prod.errors import FormatError, FormatTooNew, TreeError
from prod.hashing import sha256_bytes

CREATED = "2026-12-01T10:00:00+09:00 by prod test on testhost"


def _unlock(root):
    for dirpath, dirnames, _ in os.walk(root):
        os.chmod(dirpath, 0o755)
        for d in dirnames:
            p = os.path.join(dirpath, d)
            if not os.path.islink(p):
                os.chmod(p, 0o755)


def lock(root):
    """Lock a version directory: chmod -R a-w (symlinks excluded)."""
    for dirpath, dirnames, filenames in os.walk(root, topdown=False):
        for name in filenames + dirnames:
            p = os.path.join(dirpath, name)
            if not os.path.islink(p):
                os.chmod(p, os.lstat(p).st_mode & ~0o222)
    os.chmod(root, os.lstat(root).st_mode & ~0o222)


class Tree(unittest.TestCase):
    """Creates a small fake version directory for each test."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="prod-test-")
        self.root = os.path.join(self.tmp, "V1.0")
        r = self.root
        os.makedirs(os.path.join(r, "bin"))
        os.makedirs(os.path.join(r, "lib", "sub"))
        os.makedirs(os.path.join(r, "share", "empty"))
        self.write("bin/tool", b"#!/bin/sh\necho hi\n", mode=0o755)
        self.write("lib/libA.so.1.2", b"\x7fELF" + b"a" * 100)
        os.symlink("libA.so.1.2", os.path.join(r, "lib", "libA.so"))
        os.symlink("../libA.so.1.2", os.path.join(r, "lib", "sub", "up.so"))
        os.symlink("lib", os.path.join(r, "lib64"))  # symlink to a directory
        self.write("lib/sub/data.txt", b"data\n")
        self.write(" space name", b"x")

    def tearDown(self):
        _unlock(self.tmp)
        shutil.rmtree(self.tmp)

    def write(self, rel, data, mode=0o644):
        p = os.path.join(self.root, rel)
        with open(p, "wb") as f:
            f.write(data)
        os.chmod(p, mode)


class TestBuild(Tree):
    def test_entries(self):
        entries = manifest.build(self.root)
        by = {e.path: e for e in entries}
        self.assertEqual(by["."].kind, "d")
        self.assertEqual(by["share/empty"].kind, "d")
        self.assertEqual((by["bin/tool"].kind, by["bin/tool"].exe), ("f", True))
        self.assertFalse(by["lib/libA.so.1.2"].exe)
        self.assertEqual(by["lib/libA.so.1.2"].size, 104)
        self.assertEqual(by["lib/sub/data.txt"].content, sha256_bytes(b"data\n"))
        self.assertEqual((by["lib/libA.so"].kind, by["lib/libA.so"].content), ("l", "libA.so.1.2"))
        self.assertEqual(by["lib64"].kind, "l")
        self.assertNotIn("lib64/libA.so", by)  # directory symlinks are not followed
        paths = [os.fsencode(e.path) for e in entries]
        self.assertEqual(paths, sorted(paths))

    def test_roundtrip_deterministic(self):
        data = manifest.dumps(manifest.build(self.root), product="TEST", version="V1.0", created=CREATED)
        self.assertEqual(data, manifest.dumps(manifest.build(self.root, workers=1),
                                              product="TEST", version="V1.0", created=CREATED))
        header, entries = manifest.loads(data)
        self.assertEqual(header["product"], "TEST")
        self.assertEqual(header["entries"], str(len(entries)))
        self.assertEqual(entries, manifest.build(self.root))
        self.assertIn(b"\nf\tx\t", data)
        self.assertIn(b"\nd\t-\t-\t-\t.\n", data)

    def test_non_utf8_name(self):
        name = b"caf\xe9.txt"
        with open(os.path.join(os.fsencode(self.root), name), "wb") as f:
            f.write(b"x")
        data = manifest.dumps(manifest.build(self.root), product="T", version="V1", created=CREATED)
        self.assertIn(b"\t" + name + b"\n", data)
        _, entries = manifest.loads(data)
        self.assertEqual(manifest.verify(entries, self.root, quick=False),
                         manifest.verify(manifest.build(self.root), self.root))

    def test_violations(self):
        r = self.root
        os.symlink("/etc/passwd", os.path.join(r, "abs"))
        os.symlink("../../outside", os.path.join(r, "lib", "out"))
        os.symlink("missing", os.path.join(r, "dangling"))
        os.mkfifo(os.path.join(r, "fifo"))
        self.write("tab\tname", b"x")
        os.symlink(self.tmp, os.path.join(r, "escape_dir"))  # also an absolute path
        with self.assertRaises(TreeError) as cm:
            manifest.build(r)
        reasons = dict(cm.exception.violations)
        self.assertIn("abs", reasons)
        self.assertIn("lib/out", reasons)
        self.assertIn("dangling", reasons)
        self.assertIn("fifo", reasons)
        self.assertIn(repr("tab\tname"), reasons)
        self.assertEqual(cm.exception.exit_code, 1)

    def test_relative_link_escaping_via_another_link(self):
        # lib/sub/up -> '..' points to lib, so it is fine by itself.
        # c -> 'lib/sub/up/../../..' is '.' lexically, but resolving it goes lib -> root -> outside the root.
        os.symlink("..", os.path.join(self.root, "lib", "sub", "up"))
        os.symlink("lib/sub/up/../../..", os.path.join(self.root, "c"))
        with self.assertRaises(TreeError) as cm:
            manifest.build(self.root)
        self.assertEqual([p for p, _ in cm.exception.violations], ["c"])


class TestVerify(Tree):
    def setUp(self):
        super().setUp()
        self.entries = manifest.build(self.root)
        lock(self.root)

    def codes(self, quick=False):
        return {(f.code, f.path) for f in manifest.verify(self.entries, self.root, quick=quick)}

    def unlock_then(self, fn):
        _unlock(self.root)
        for dirpath, _, filenames in os.walk(self.root):
            for n in filenames:
                p = os.path.join(dirpath, n)
                if not os.path.islink(p):
                    os.chmod(p, os.lstat(p).st_mode | 0o200)
        fn()
        lock(self.root)

    def test_clean(self):
        self.assertEqual(self.codes(), set())
        self.assertFalse(manifest.has_errors(manifest.verify(self.entries, self.root)))

    def test_writable_is_warning(self):
        os.chmod(os.path.join(self.root, "lib", "sub", "data.txt"), 0o644)
        findings = manifest.verify(self.entries, self.root)
        self.assertEqual({(f.level, f.code, f.path) for f in findings},
                         {("warning", "writable", "lib/sub/data.txt")})
        self.assertFalse(manifest.has_errors(findings))

    def test_same_size_content_change(self):
        self.unlock_then(lambda: self.write("lib/sub/data.txt", b"DATA\n", mode=0o644))
        self.assertEqual(self.codes(), {("content", "lib/sub/data.txt")})
        self.assertEqual(self.codes(quick=True), set())  # quick does not look at contents

    def test_size_change(self):
        self.unlock_then(lambda: self.write("lib/sub/data.txt", b"longer data\n"))
        self.assertEqual(self.codes(quick=True), {("size", "lib/sub/data.txt")})

    def test_missing_extra_exec_target_kind(self):
        def tamper():
            os.remove(os.path.join(self.root, "lib", "sub", "data.txt"))
            self.write("lib/new.txt", b"new")
            os.makedirs(os.path.join(self.root, "lib", "__pycache__"))
            os.chmod(os.path.join(self.root, "lib", "libA.so.1.2"), 0o755)
            os.remove(os.path.join(self.root, "lib", "libA.so"))
            os.symlink("other", os.path.join(self.root, "lib", "libA.so"))
            shutil.rmtree(os.path.join(self.root, "share", "empty"))
            self.write("share/empty", b"now a file")
        self.unlock_then(tamper)
        self.assertEqual(self.codes(), {
            ("missing", "lib/sub/data.txt"),
            ("extra", "lib/new.txt"),
            ("extra", "lib/__pycache__"),
            ("exec", "lib/libA.so.1.2"),
            ("target", "lib/libA.so"),
            ("kind", "share/empty"),
        })

    def test_missing_root(self):
        _unlock(self.root)
        shutil.rmtree(self.root)
        self.assertEqual(self.codes(), {("missing", ".")})


class TestLoadsErrors(unittest.TestCase):
    GOOD = (b"# prodmanager manifest\n# format: 1\n# product: T\n# version: V1\n"
            b"# created: x\n# entries: 2\n"
            b"d\t-\t-\t-\t.\n"
            b"f\t-\t1\t" + ("sha256:" + "a" * 64).encode() + b"\ta\n")

    def test_good(self):
        header, entries = manifest.loads(self.GOOD)
        self.assertEqual(len(entries), 2)

    def test_errors(self):
        g = self.GOOD
        cases = {
            "no magic": g.replace(b"# prodmanager manifest\n", b""),
            "count": g.replace(b"# entries: 2", b"# entries: 3"),
            "unsorted": g.replace(b"d\t-\t-\t-\t.\n", b"") + b"d\t-\t-\t-\t.\n",
            "no root": g.replace(b"d\t-\t-\t-\t.\n", b"").replace(b"# entries: 2", b"# entries: 1"),
            "bad kind": g.replace(b"\nf\t", b"\nz\t"),
            "bad hash": g.replace(b"sha256:" + b"a" * 64, b"sha256:abc"),
            "columns": g.replace(b"\ta\n", b" a\n"),
            "no newline at end": g[:-1],
            "missing header": g.replace(b"# product: T\n", b""),
            "dir with size": g.replace(b"d\t-\t-\t-\t.", b"d\t-\t1\t-\t."),
        }
        for label, data in cases.items():
            with self.subTest(label):
                with self.assertRaises(FormatError):
                    manifest.loads(data)

    def test_too_new(self):
        with self.assertRaises(FormatTooNew):
            manifest.loads(self.GOOD.replace(b"# format: 1", b"# format: 2"))


if __name__ == "__main__":
    unittest.main()
