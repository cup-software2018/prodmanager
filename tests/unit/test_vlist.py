import os
import tempfile
import unittest

from prod import vlist
from prod.errors import FormatError, FormatTooNew

H1 = "sha256:" + "1" * 64
H2 = "sha256:" + "2" * 64
H3 = "sha256:" + "3" * 64

DRAFT = b"""\
format: 1
description: PYTHON 3.11 + ROOT 6.32
products:
  - {name: PYTHON, version: V3.11.14}
  - {name: ROOT, version: V6.32.20_3.11.14}
"""


def released(**over):
    v = vlist.Vlist(
        description="PYTHON 3.11 + ROOT 6.32 (lsim)",
        released="2026-12-01T10:00:00+09:00",
        released_by="prod v0.1 on cupdev0",
        bundle=H3,
        products=[
            vlist.Product("PYTHON", "V3.11.14", H1, "PYTHON/V3.11.14", H2),
            vlist.Product("ROOT", "V6.32.20_3.11.14", H1, "ROOT/V6.32.20_3.11.14-m2", H2),
        ],
    )
    for k, val in over.items():
        setattr(v, k, val)
    return v


class TestDraft(unittest.TestCase):
    def test_draft_values_are_strings(self):
        v = vlist.loads(DRAFT, draft=True)
        self.assertTrue(v.is_draft)
        self.assertEqual(v.format, "1")
        self.assertEqual([(p.name, p.version) for p in v.products],
                         [("PYTHON", "V3.11.14"), ("ROOT", "V6.32.20_3.11.14")])

    def test_numbers_and_dates_stay_strings(self):
        v = vlist.loads(b"format: 1\ndescription: 3.10\nproducts:\n  - {name: A, version: V1}\n", draft=True)
        self.assertEqual(v.description, "3.10")  # must not become the float 3.1
        v = vlist.loads(b"format: 1\ndescription: 2026-12-01\nproducts:\n  - {name: A, version: V1}\n", draft=True)
        self.assertEqual(v.description, "2026-12-01")

    def test_draft_rejects_tool_fields(self):
        with self.assertRaises(FormatError):
            vlist.loads(DRAFT + b"bundle: " + H3.encode() + b"\n", draft=True)
        bad = DRAFT.replace(b"{name: PYTHON, version: V3.11.14}",
                            b"{name: PYTHON, version: V3.11.14, manifest: " + H1.encode() + b"}")
        with self.assertRaises(FormatError):
            vlist.loads(bad, draft=True)

    def test_errors(self):
        cases = {
            "unknown key": DRAFT + b"extra: x\n",
            "duplicate key": DRAFT + b"description: again\n",
            "duplicate product": DRAFT + b"  - {name: ROOT, version: V6.36.06_3.11.14}\n",
            "bad product name": DRAFT.replace(b"name: ROOT", b"name: root"),
            "bad version": DRAFT.replace(b"version: V3.11.14", b"version: 3.11.14"),
            "empty products": b"format: 1\ndescription: x\nproducts: []\n",
            "no format": DRAFT.replace(b"format: 1\n", b""),
            "bad format": DRAFT.replace(b"format: 1", b"format: one"),
            "newline in description": b'format: 1\ndescription: "a\\nb"\nproducts:\n  - {name: A, version: V1}\n',
            "yaml syntax": b"format: 1\ndescription: [\n",
            "not a mapping": b"- a\n- b\n",
        }
        for label, data in cases.items():
            with self.subTest(label):
                with self.assertRaises(FormatError):
                    vlist.loads(data, draft=True)

    def test_format_too_new(self):
        with self.assertRaises(FormatTooNew) as cm:
            vlist.loads(DRAFT.replace(b"format: 1", b"format: 2") + b"newfield: x\n", draft=True)
        self.assertEqual(cm.exception.exit_code, 5)

    def test_korean_description(self):
        v = vlist.loads("format: 1\ndescription: 한글 설명\nproducts:\n  - {name: A, version: V1}\n".encode(),
                        draft=True)
        self.assertEqual(v.description, "한글 설명")


class TestReleased(unittest.TestCase):
    def test_roundtrip_and_determinism(self):
        data = vlist.dumps(released())
        self.assertEqual(data, vlist.dumps(vlist.loads(data)))
        self.assertEqual(vlist.vlist_hash(data), vlist.vlist_hash(vlist.dumps(released())))
        self.assertTrue(data.startswith(b'format: "1"\ndescription: '))
        v = vlist.loads(data)
        self.assertEqual(v.products[1].module, "ROOT/V6.32.20_3.11.14-m2")
        self.assertEqual(v.bundle, H3)

    def test_migrated_from(self):
        data = vlist.dumps(released(migrated_from="3.1.vlist"))
        self.assertEqual(vlist.loads(data, name="3.1").migrated_from, "3.1.vlist")
        with self.assertRaises(FormatError):
            vlist.loads(data, name="3.0")

    def test_released_requires_tool_fields(self):
        with self.assertRaises(FormatError):
            vlist.loads(DRAFT)  # not a draft, but released etc. are missing

    def test_module_names(self):
        ok = ["ROOT/V1", "ROOT/V1-m2", "ROOT/V1-m10"]
        bad = ["ROOT/V1-m1", "ROOT/V1-m0", "ROOT/V1-m02", "ROOT/V2", "PYTHON/V1", "ROOT/V1-x2", "ROOT/V1/x"]
        for m in ok:
            self.assertTrue(vlist.module_name_ok(m, "ROOT", "V1"), m)
        for m in bad:
            self.assertFalse(vlist.module_name_ok(m, "ROOT", "V1"), m)
        with self.assertRaises(FormatError):
            vlist.dumps(released(products=[vlist.Product("ROOT", "V1", H1, "ROOT/V2", H2)]))

    def test_bad_hash_and_time(self):
        with self.assertRaises(FormatError):
            vlist.dumps(released(bundle="sha256:abc"))
        with self.assertRaises(FormatError):
            vlist.dumps(released(released="2026-12-01 10:00:00"))

    def test_load_file_uses_name(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "3.1.yml")
            with open(path, "wb") as f:
                f.write(vlist.dumps(released(migrated_from="3.1.vlist")))
            self.assertEqual(vlist.load(path).migrated_from, "3.1.vlist")
            self.assertEqual(vlist.name_from_path(path), "3.1")


class TestNames(unittest.TestCase):
    def test_names(self):
        for n in ["3.1", "daq_2.1", "mon_1.0", "0.1", "A-b.c_d"]:
            self.assertEqual(vlist.check_name(n), n)
        for n in ["", ".", "..", ".hidden", "-x", "a b", "a/b", "../x", "한글"]:
            with self.subTest(n):
                with self.assertRaises(FormatError):
                    vlist.check_name(n)

    def test_name_from_path(self):
        self.assertEqual(vlist.name_from_path("/x/vlist/daq_2.1.yml"), "daq_2.1")
        with self.assertRaises(FormatError):
            vlist.name_from_path("/x/vlist/3.1.vlist")


class TestLegacy(unittest.TestCase):
    def test_server_style(self):
        text = "#\n# Products \n#\nPYTHON\t\tV3.11.14\nROOT\t\tV6.32.20_3.11.14\nHDF5\t\tV1.14.2\n#GEANT4          V11.1.3\n"
        self.assertEqual(vlist.parse_legacy(text),
                         [("PYTHON", "V3.11.14"), ("ROOT", "V6.32.20_3.11.14"), ("HDF5", "V1.14.2")])

    def test_mixed_whitespace(self):
        self.assertEqual(vlist.parse_legacy("GEANT4          V11.1.3\n  CAENHVWRAPPER\tV6.6  \n"),
                         [("GEANT4", "V11.1.3"), ("CAENHVWRAPPER", "V6.6")])

    def test_errors(self):
        for text in ["ROOT\n", "ROOT V1 extra\n", "root V1\n", "ROOT V1\nROOT V2\n", "# only comments\n"]:
            with self.subTest(text):
                with self.assertRaises(FormatError):
                    vlist.parse_legacy(text)


if __name__ == "__main__":
    unittest.main()
