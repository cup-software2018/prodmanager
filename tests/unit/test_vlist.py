import os
import tempfile
import unittest

from prod import vlist
from prod.errors import FormatError

DRAFT = b"""\
description: PYTHON 3.11 + ROOT 6.32
products:
  - {name: PYTHON, version: V3.11.14}
  - {name: ROOT, version: V6.32.20_3.11.14}
"""


def released(**over):
    v = vlist.Vlist(
        description="PYTHON 3.11 + ROOT 6.32 (lsim)",
        released="2026-12-01T10:00:00+09:00",
        released_by="prodman v0.1 on cupdev0",
        products=[vlist.Product("PYTHON", "V3.11.14"), vlist.Product("ROOT", "V6.32.20_3.11.14")],
    )
    for k, val in over.items():
        setattr(v, k, val)
    return v


class TestDraft(unittest.TestCase):
    def test_draft_values_are_strings(self):
        v = vlist.loads(DRAFT, draft=True)
        self.assertTrue(v.is_draft)
        self.assertEqual([(p.name, p.version) for p in v.products],
                         [("PYTHON", "V3.11.14"), ("ROOT", "V6.32.20_3.11.14")])

    def test_numbers_and_dates_stay_strings(self):
        v = vlist.loads(b"description: 3.10\nproducts:\n  - {name: A, version: V1}\n", draft=True)
        self.assertEqual(v.description, "3.10")  # must not become the float 3.1
        v = vlist.loads(b"description: 2026-12-01\nproducts:\n  - {name: A, version: V1}\n", draft=True)
        self.assertEqual(v.description, "2026-12-01")

    def test_draft_rejects_tool_fields(self):
        with self.assertRaises(FormatError):
            vlist.loads(DRAFT + b"released: \"2026-12-01T10:00:00+09:00\"\n", draft=True)

    def test_errors(self):
        cases = {
            "unknown key": DRAFT + b"extra: x\n",
            "duplicate key": DRAFT + b"description: again\n",
            "duplicate product": DRAFT + b"  - {name: ROOT, version: V6.36.06_3.11.14}\n",
            "bad product name": DRAFT.replace(b"name: ROOT", b"name: root"),
            "bad version": DRAFT.replace(b"version: V3.11.14", b"version: 3.11.14"),
            "empty products": b"description: x\nproducts: []\n",
            "newline in description": b'description: "a\\nb"\nproducts:\n  - {name: A, version: V1}\n',
            "yaml syntax": b"description: [\n",
            "not a mapping": b"- a\n- b\n",
        }
        for label, data in cases.items():
            with self.subTest(label):
                with self.assertRaises(FormatError):
                    vlist.loads(data, draft=True)

    def test_korean_description(self):
        v = vlist.loads("description: 한글 설명\nproducts:\n  - {name: A, version: V1}\n".encode(),
                        draft=True)
        self.assertEqual(v.description, "한글 설명")


class TestReleased(unittest.TestCase):
    def test_roundtrip_and_determinism(self):
        data = vlist.dumps(released())
        self.assertEqual(data, vlist.dumps(vlist.loads(data)))
        self.assertEqual(data, vlist.dumps(released()))
        self.assertTrue(data.startswith(b'description: '))
        v = vlist.loads(data)
        self.assertEqual(v.products[1].module, "ROOT/V6.32.20_3.11.14")

    def test_released_requires_tool_fields(self):
        with self.assertRaises(FormatError):
            vlist.loads(DRAFT)  # not a draft, but released etc. are missing

    def test_released_rejects_old_fields(self):
        data = vlist.dumps(released()).replace(b"products:", b'bundle: "x"\nproducts:')
        with self.assertRaises(FormatError):
            vlist.loads(data)

    def test_bad_time(self):
        with self.assertRaises(FormatError):
            vlist.dumps(released(released="2026-12-01 10:00:00"))

    def test_load_file_uses_name(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "3.1.yml")
            with open(path, "wb") as f:
                f.write(vlist.dumps(released()))
            self.assertEqual(vlist.load(path).released_by, "prodman v0.1 on cupdev0")
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


if __name__ == "__main__":
    unittest.main()
