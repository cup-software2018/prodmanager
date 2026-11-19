import os
import tempfile
import unittest

from prod import config
from prod.errors import EnvError, FormatError, FormatTooNew

CLIENT = b'format: "1"\nrole: "client"\nserver: "cupsoft@192.168.0.150:/home/cupsoft/Products"\n'


class TestServer(unittest.TestCase):
    def test_parse(self):
        s = config.parse_server("cupsoft@192.168.0.150:/home/cupsoft/Products")
        self.assertEqual((s.host, s.path, s.is_local), ("cupsoft@192.168.0.150", "/home/cupsoft/Products", False))
        self.assertEqual(s.rsync_url("vlist/3.1.yml"), "cupsoft@192.168.0.150:/home/cupsoft/Products/vlist/3.1.yml")
        s = config.parse_server("cupdev0:/data/Products/")
        self.assertEqual((s.host, s.path), ("cupdev0", "/data/Products"))
        s = config.parse_server("/tmp/sandbox/server/Products")
        self.assertTrue(s.is_local)
        self.assertEqual(s.rsync_url("ROOT/V1/"), "/tmp/sandbox/server/Products/ROOT/V1/")

    def test_bad(self):
        for spec in ["", "relative/path", "host:relative", "host:/with space", "a b@host:/p", "host:/p;rm"]:
            with self.subTest(spec):
                with self.assertRaises(FormatError):
                    config.parse_server(spec)


class TestConfig(unittest.TestCase):
    def test_client(self):
        cfg = config.loads(CLIENT, env={})
        self.assertEqual(cfg.role, "client")
        self.assertEqual(cfg.server.host, "cupsoft@192.168.0.150")
        self.assertIn("BatchMode=yes", cfg.ssh_options)
        cfg.require_role("client", "sync")
        with self.assertRaises(EnvError) as cm:
            cfg.require_role("server", "release")
        self.assertEqual(cm.exception.exit_code, 3)

    def test_env_override(self):
        cfg = config.loads(CLIENT, env={"PROD_SERVER": "newserver:/srv/Products"})
        self.assertEqual(cfg.server.spec, "newserver:/srv/Products")
        cfg = config.loads(b'format: "1"\nrole: "client"\n', env={"PROD_SERVER": "/tmp/x"})
        self.assertTrue(cfg.server.is_local)

    def test_server_role(self):
        cfg = config.loads(b'format: "1"\nrole: "server"\n', env={})
        self.assertIsNone(cfg.server)
        with self.assertRaises(EnvError):
            cfg.require_server()

    def test_errors(self):
        cases = {
            "no format": b'role: "client"\nserver: "/x"\n',
            "bad role": b'format: "1"\nrole: "master"\n',
            "unknown key": CLIENT + b'colour: "blue"\n',
            "client without server": b'format: "1"\nrole: "client"\n',
            "ssh_options not a list": CLIENT + b'ssh_options: "-o BatchMode=yes"\n',
        }
        for label, data in cases.items():
            with self.subTest(label):
                with self.assertRaises(FormatError):
                    config.loads(data, env={})
        with self.assertRaises(FormatTooNew):
            config.loads(b'format: "2"\nrole: "client"\n', env={})

    def test_dumps_roundtrip(self):
        data = config.dumps("client", "cupdev0:/home/cupsoft/Products", ["-o", "BatchMode=yes", "-i", "/k"])
        cfg = config.loads(data, env={})
        self.assertEqual(cfg.ssh_options, ["-o", "BatchMode=yes", "-i", "/k"])
        self.assertEqual(config.loads(config.dumps("server"), env={}).role, "server")

    def test_load_missing(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(EnvError):
                config.load(config.Paths(d), env={})


class TestPaths(unittest.TestCase):
    def test_paths(self):
        p = config.Paths("/home/cupsoft/Products")
        self.assertEqual(p.conf, "/home/cupsoft/Products/.prod/etc/prod.conf")
        self.assertEqual(p.vlist_file("3.1"), "/home/cupsoft/Products/vlist/3.1.yml")
        self.assertEqual(p.module_file("ROOT/V6.32.20_3.11.14-m2"),
                         "/home/cupsoft/Products/modulefiles/ROOT/V6.32.20_3.11.14-m2")
        self.assertEqual(p.bundle_file("3.1"), "/home/cupsoft/Products/modulefiles/vlist/3.1")
        self.assertEqual(p.manifest_file("ROOT", "V1"), "/home/cupsoft/Products/ROOT/V1.manifest")

    def test_products_dir_of(self):
        with tempfile.TemporaryDirectory() as d:
            products = os.path.join(d, "Products")
            tool = os.path.join(products, ".prod", "versions", "v0.1")
            os.makedirs(tool)
            os.symlink("versions/v0.1", os.path.join(products, ".prod", "current"))
            self.assertEqual(config.products_dir_of(tool), os.path.realpath(products))
            self.assertEqual(config.products_dir_of(os.path.join(products, ".prod", "current")),
                             os.path.realpath(products))
            with self.assertRaises(EnvError):
                config.products_dir_of(d)


if __name__ == "__main__":
    unittest.main()
