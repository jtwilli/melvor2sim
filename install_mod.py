"""Install or remove the in-game panel (unofficial UI mod).

    python -I install_mod.py              # install
    python -I install_mod.py --status     # show what the mod reported last time the game ran
    python -I install_mod.py --uninstall  # remove everything this script added

What install does:
  1. Copies mod/*.gd to the game's user-data folder (melvor2sim/ next to your saves).
  2. Writes melvor2sim/config.json there: where this repo and Python are, so the panel can
     start sim_server.py.
  3. Writes ONE file into the game folder: override.cfg next to Melvor2.exe, which tells the
     engine to load the mod script at startup. No game file is changed.

Close the game before installing; the override is only read when the game starts.
"""
import argparse
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))  # sibling modules, also under python -I
import paths  # noqa: E402

MARK = "; melvor2sim in-game panel (unofficial). Delete this file to remove it."
OVERRIDE = MARK + '\n[autoload]\n\nMelvor2Sim="*user://melvor2sim/loader.gd"\n'


def user_dir():
    """The game's user-data folder (where saves live)."""
    for d in paths.save_dirs():
        if d.is_dir():
            return d
    sys.exit("Could not find the game's user-data folder. Run the game once, then try again.\nLooked in:\n  "
             + "\n  ".join(map(str, paths.save_dirs())))


def ours(path):
    return path.exists() and path.read_text(encoding="utf-8", errors="replace").startswith(MARK)


def install(exe, port):
    override, mod = exe.parent / "override.cfg", user_dir() / "melvor2sim"
    if override.exists() and not ours(override):
        sys.exit(f"{override} already exists and was not written by this script; not touching it.")
    mod.mkdir(exist_ok=True)
    for f in sorted((ROOT / "mod").glob("*.gd")):
        shutil.copy2(f, mod / f.name)
        print(f"copied {f.name} -> {mod}")
    (mod / "config.json").write_text(json.dumps(
        {"repo": ROOT.as_posix(), "python": Path(sys.executable).as_posix(), "port": port}, indent=1), encoding="utf-8")
    (mod / "status.json").unlink(missing_ok=True)
    try:
        override.write_text(OVERRIDE, encoding="utf-8")
    except PermissionError:
        sys.exit(f"No permission to write {override}. Re-run this from a terminal opened as administrator.")
    print(f"wrote {override}")
    print("\nInstalled. Start the game: a new button appears at the right of the top banner."
          "\nIf it does not, run: python -I install_mod.py --status")


def uninstall(exe):
    override, mod = exe.parent / "override.cfg", user_dir() / "melvor2sim"
    if ours(override):
        override.unlink()
        print(f"removed {override}")
    elif override.exists():
        print(f"left {override} alone: it was not written by this script")
    if mod.is_dir():
        shutil.rmtree(mod)
        print(f"removed {mod}")
    print("Uninstalled.")


def status(exe):
    override, mod = exe.parent / "override.cfg", user_dir() / "melvor2sim"
    print("override.cfg:", "installed" if ours(override) else "not installed")
    print("mod files:   ", ", ".join(p.name for p in sorted(mod.glob("*"))) if mod.is_dir() else "none")
    st = mod / "status.json"
    if st.exists():
        print("last run:\n" + st.read_text(encoding="utf-8"))
    else:
        print("last run:     no status yet (the game has not loaded the mod since install)")


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--uninstall", action="store_true")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--exe", help="path to Melvor2.exe (default: found in your Steam libraries)")
    ap.add_argument("--port", type=int, default=8765, help="port the panel uses for sim_server.py")
    args = ap.parse_args()
    exe = paths.find_exe(args.exe)
    if args.status:
        status(exe)
    elif args.uninstall:
        uninstall(exe)
    else:
        install(exe, args.port)


if __name__ == "__main__":
    main()
