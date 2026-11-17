"""Developer tool: compile-check the in-game panel's scripts with the game's own engine.

    python -I check_mod.py

Copies mod/*.gd to a temporary folder, starts the game headless in the mod loader's check mode
(it quits before the game loads), and prints which scripts compile. Needs the mod installed
(python -I install_mod.py) and the game closed. Exit code 1 if any script fails.
"""
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))  # sibling modules, also under python -I
import paths            # noqa: E402
import install_mod      # noqa: E402


def main():
    exe = paths.find_exe()
    mod = install_mod.user_dir() / "melvor2sim"
    if not install_mod.ours(exe.parent / "override.cfg") or not (mod / "loader.gd").exists():
        sys.exit("The mod is not installed. Run: python -I install_mod.py")
    # the installed loader does the checking, so it must be the current one
    shutil.copy2(ROOT / "mod" / "loader.gd", mod / "loader.gd")
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        for f in (ROOT / "mod").glob("*.gd"):
            shutil.copy2(f, tmp / f.name)
        out = tmp / "result.json"
        try:
            subprocess.run([str(exe), "--headless", "--script", str(ROOT / "mod" / "tools" / "noop.gd"),
                            "--", "--m2s-check", tmp.as_posix(), out.as_posix()],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=90)
        except subprocess.TimeoutExpired:
            sys.exit("The game did not exit in time (is it already running? close it and retry).")
        if not out.exists():
            sys.exit("No result: the loader's check mode did not run (loader.gd itself may have an error).")
        result = json.loads(out.read_text(encoding="utf-8"))
    bad = [f for f, ok in sorted(result.items()) if not ok]
    for f, ok in sorted(result.items()):
        print(f"{'ok  ' if ok else 'FAIL'}  {f}")
    if bad:
        sys.exit(1)


if __name__ == "__main__":
    main()
