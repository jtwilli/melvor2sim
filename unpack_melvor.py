"""Extract data files from the Godot pack embedded in Melvor2.exe.

The exe is opened read-only. By default only data-like files (.json, .cfg,
.po) are written to data/raw/; use --all for everything (~490 MB).

    python -I unpack_melvor.py            # extract data files
    python -I unpack_melvor.py --list     # print the pack directory
    python -I unpack_melvor.py --exe PATH # if the game is not found automatically

The game is found automatically in any Steam library; see paths.py for overrides.
"""
import argparse
import hashlib
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # sibling modules, also under python -I
import paths  # noqa: E402

DATA_EXTS = {".json", ".cfg", ".po"}
PACK_REL_FILEBASE = 2
PACK_DIR_ENCRYPTED = 1


def find_pck_section(f):
    """Return (offset, size) of the PE section named 'pck'."""
    f.seek(0x3C)
    pe = struct.unpack("<I", f.read(4))[0]
    f.seek(pe)
    if f.read(4) != b"PE\0\0":
        raise ValueError("not a PE file")
    f.seek(pe + 6)
    nsec = struct.unpack("<H", f.read(2))[0]
    f.seek(pe + 20)
    opt_size = struct.unpack("<H", f.read(2))[0]
    f.seek(pe + 24 + opt_size)
    for _ in range(nsec):
        sec = f.read(40)
        if sec[:8].rstrip(b"\0") == b"pck":
            raw_size, raw_ptr = struct.unpack("<II", sec[16:24])
            return raw_ptr, raw_size
    raise ValueError("no 'pck' section in exe")


def read_directory(f):
    """Return (data_start, entries); entries are (path, offset, size, md5)."""
    base, _ = find_pck_section(f)
    f.seek(base)
    magic, fmt, major, minor, patch, flags = struct.unpack("<4sIIIII", f.read(24))
    if magic != b"GDPC":
        raise ValueError("pck section does not start with GDPC")
    if fmt != 4:
        raise ValueError(f"unsupported pack format {fmt} (expected 4)")
    if flags & PACK_DIR_ENCRYPTED:
        raise ValueError("pack directory is encrypted")
    file_base, dir_offset = struct.unpack("<QQ", f.read(16))
    data_start = file_base + (base if flags & PACK_REL_FILEBASE else 0)
    f.seek(base + dir_offset)
    count = struct.unpack("<I", f.read(4))[0]
    entries = []
    for _ in range(count):
        n = struct.unpack("<I", f.read(4))[0]
        path = f.read(n).rstrip(b"\0").decode("utf-8")
        offset, size = struct.unpack("<QQ", f.read(16))
        md5 = f.read(16)
        entry_flags = struct.unpack("<I", f.read(4))[0]
        if entry_flags & 1:
            raise ValueError(f"encrypted file in pack: {path}")
        entries.append((path, offset, size, md5))
    print(f"Godot {major}.{minor}.{patch} pack, format {fmt}, {count} files")
    return data_start, entries


def safe_target(out_dir, path):
    rel = Path(path.replace("res://", ""))
    if rel.is_absolute() or ".." in rel.parts or ":" in path.replace("res://", ""):
        raise ValueError(f"unsafe path in pack: {path}")
    return out_dir / rel


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--exe", help="path to Melvor2.exe (default: found in your Steam libraries)")
    ap.add_argument("--out", default=str(Path(__file__).parent / "data" / "raw"))
    ap.add_argument("--list", action="store_true", help="print the directory and exit")
    ap.add_argument("--all", action="store_true", help="extract every file, not just data files")
    args = ap.parse_args()

    exe = paths.find_exe(args.exe)
    print(f"reading {exe.name} from {exe.parent.name}")
    with open(exe, "rb") as f:
        data_start, entries = read_directory(f)
        if args.list:
            for path, _, size, _ in entries:
                print(f"{size:>10}  {path}")
            return 0
        out_dir = Path(args.out)
        written = bad = 0
        for path, offset, size, md5 in entries:
            if not args.all and Path(path).suffix.lower() not in DATA_EXTS:
                continue
            f.seek(data_start + offset)
            blob = f.read(size)
            if hashlib.md5(blob).digest() != md5:
                print(f"MD5 MISMATCH: {path}", file=sys.stderr)
                bad += 1
                continue
            target = safe_target(out_dir, path)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(blob)
            written += 1
            if not args.all:
                print(f"{size:>10}  {path}")
    print(f"extracted {written} files to {out_dir}" + (f", {bad} failed md5" if bad else ", all md5 ok"))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
