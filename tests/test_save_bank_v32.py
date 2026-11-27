"""Save bank v31/v32 regression tests using synthetic, non-personal fixtures.

Run in the melvor2sim repository root: python -m unittest discover -s tests -v
"""
import struct
import unittest

from read_save import Reader, d_bank


def i32(value):
    return struct.pack("<i", value)


def text(value):
    encoded = value.encode("utf-8")
    length = len(encoded)
    prefix = bytearray()
    while length >= 128:
        prefix.append((length & 0x7F) | 0x80)
        length >>= 7
    prefix.append(length)
    return bytes(prefix) + encoded


def tab(name, items, version):
    # Three stack limits and two configuration flags.
    data = bytearray(i32(99) + i32(999) + i32(9999) + b"\x00\x01")
    data.extend(i32(len(items)))
    for item_id, quantity in items:
        data.extend(i32(quantity) + b"\x00\x00" + text(item_id))
    data.extend(text(name))
    data.extend(i32(0))  # no bank tags in this synthetic fixture
    if version >= 32:
        data.extend(b"\x01")  # extra per-tab flag
    return bytes(data)


def fixture(version, tabs):
    return i32(0) + i32(len(tabs)) + b"".join(tab(name, items, version) for name, items in tabs)


class BankVersionTests(unittest.TestCase):
    def test_v31_two_tabs(self):
        payload = fixture(31, [("Materials", [("MelvorBase:Copper_Ore", 100)]),
                               ("Food", [("MelvorBase:Shrimp", 20)])])
        reader = Reader(payload)
        result = d_bank(reader, 31)
        self.assertEqual([t["label"] for t in result], ["Materials", "Food"])
        self.assertEqual([t["stacks"][0]["qty"] for t in result], [100, 20])
        self.assertEqual(reader.left, 0)

    def test_v32_two_tabs(self):
        payload = fixture(32, [("Materials", [("MelvorBase:Copper_Ore", 100)]),
                               ("Food", [("MelvorBase:Shrimp", 20)])])
        reader = Reader(payload)
        result = d_bank(reader, 32)
        self.assertEqual([t["label"] for t in result], ["Materials", "Food"])
        self.assertEqual([t["stacks"][0]["qty"] for t in result], [100, 20])
        self.assertEqual(reader.left, 0)

    def test_v32_empty_bank(self):
        reader = Reader(fixture(32, []))
        self.assertEqual(d_bank(reader, 32), [])
        self.assertEqual(reader.left, 0)

    def test_v32_single_tab(self):
        reader = Reader(fixture(32, [("Tab", [])]))
        self.assertEqual(len(d_bank(reader, 32)), 1)
        self.assertEqual(reader.left, 0)


if __name__ == "__main__":
    unittest.main()
