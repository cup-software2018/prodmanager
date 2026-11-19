import os
import tempfile
import unittest

from prod import state
from prod.config import Paths
from prod.errors import FormatError, LockBusy

H = "sha256:" + "a" * 64


class TestState(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.paths = Paths(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_now_iso(self):
        t = state.now_iso()
        self.assertRegex(t, r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}[+-]\d{2}:\d{2}$")

    def test_atomic_write(self):
        path = os.path.join(self.tmp.name, "a", "b", "file")
        state.atomic_write(path, b"one", mode=0o444)
        state.atomic_write(path, b"two")  # replaces even a read-only file (rename, not open)
        with open(path, "rb") as f:
            self.assertEqual(f.read(), b"two")
        self.assertEqual(os.listdir(os.path.dirname(path)), ["file"])  # no temporary files left

    def test_lock(self):
        with state.lock(self.paths, exclusive=True):
            with self.assertRaises(LockBusy) as cm:
                with state.lock(self.paths, exclusive=True):
                    pass
            self.assertEqual(cm.exception.exit_code, 4)
            with self.assertRaises(LockBusy):
                with state.lock(self.paths, exclusive=False):
                    pass
        with state.lock(self.paths, exclusive=False):
            with state.lock(self.paths, exclusive=False):  # shared locks coexist
                pass
            with self.assertRaises(LockBusy):
                with state.lock(self.paths, exclusive=True):
                    pass
        with state.lock(self.paths, exclusive=True):  # released again
            pass

    def test_history(self):
        self.assertEqual(state.read_history(self.paths), [])
        self.assertTrue(state.record(self.paths, "v0.1.0", "sync", vlist="3.1", result="ok"))
        state.record(self.paths, "v0.1.0", "verify", vlist="3.1", result="error", detail="한글도 됨")
        with open(self.paths.history, "a") as f:
            f.write("not json\n")
        events = state.read_history(self.paths)
        self.assertEqual([e["command"] for e in events], ["sync", "verify"])
        self.assertEqual(events[0]["vlist"], "3.1")
        self.assertEqual(events[1]["detail"], "한글도 됨")
        for key in ("time", "host", "user", "tool"):
            self.assertIn(key, events[0])

    def test_history_not_writable(self):
        os.makedirs(self.paths.state)
        os.chmod(self.paths.state, 0o555)
        try:
            if os.access(self.paths.state, os.W_OK):
                self.skipTest("running as root")
            self.assertFalse(state.record(self.paths, "v0.1.0", "verify"))
        finally:
            os.chmod(self.paths.state, 0o755)

    def test_trusted(self):
        self.assertIsNone(state.get_trusted(self.paths, "3.1"))
        state.set_trusted(self.paths, "3.1", H)
        self.assertEqual(state.get_trusted(self.paths, "3.1"), H)
        with open(os.path.join(self.paths.trusted, "3.1.sha256"), "w") as f:
            f.write("garbage\n")
        with self.assertRaises(FormatError):
            state.get_trusted(self.paths, "3.1")
        with self.assertRaises(ValueError):
            state.set_trusted(self.paths, "3.1", "garbage")


if __name__ == "__main__":
    unittest.main()
