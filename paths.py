"""Find the game executable and the save folder on this computer.

Order of precedence, for each of the two:
  1. the command-line option (--exe / --save)
  2. an environment variable (MELVOR2_EXE / MELVOR2_SAVE)
  3. config.local.json next to these scripts (copy config.example.json; it is git-ignored)
  4. auto-detection: every Steam library on this computer, and the standard
     per-user data folders for Windows, Proton (Linux), Linux and macOS

MELVOR2_SAVE / "save" may point at a .sav file or at a folder to search for the newest one.
"""
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).parent
CONFIG = ROOT / "config.local.json"
GAME_DIRS = ("Melvor Idle 2",)
EXE_NAMES = ("Melvor2.exe",)
SAVE_SUBDIR = Path("Games By Malcs") / "Melvor Idle 2"


def _config():
    if not CONFIG.exists():
        return {}
    try:
        return json.loads(CONFIG.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        sys.exit(f"could not read {CONFIG.name}: {e}")


def _setting(env, key):
    v = os.environ.get(env) or _config().get(key)
    return Path(os.path.expandvars(os.path.expanduser(v))) if v else None


def _steam_roots():
    home = Path.home()
    roots = []
    if sys.platform == "win32":
        try:
            import winreg
            for hive, key, name in ((winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam", "SteamPath"),
                                    (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam", "InstallPath"),
                                    (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Valve\Steam", "InstallPath")):
                try:
                    with winreg.OpenKey(hive, key) as k:
                        roots.append(Path(winreg.QueryValueEx(k, name)[0]))
                except OSError:
                    pass
        except ImportError:
            pass
        for env in ("ProgramFiles(x86)", "ProgramFiles"):
            if os.environ.get(env):
                roots.append(Path(os.environ[env]) / "Steam")
    elif sys.platform == "darwin":
        roots.append(home / "Library" / "Application Support" / "Steam")
    else:
        roots += [home / ".steam" / "steam", home / ".local" / "share" / "Steam",
                  home / ".var" / "app" / "com.valvesoftware.Steam" / ".local" / "share" / "Steam"]
    return roots


def steam_libraries():
    """Every Steam library folder (the ones that contain steamapps/)."""
    libs = []
    for root in _steam_roots():
        if not (root / "steamapps").is_dir():
            continue
        libs.append(root)
        vdf = root / "steamapps" / "libraryfolders.vdf"
        if vdf.exists():
            text = vdf.read_text(encoding="utf-8", errors="replace")
            libs += [Path(p.replace("\\\\", "\\")) for p in re.findall(r'"path"\s+"([^"]+)"', text)]
    seen, out = set(), []
    for lib in libs:
        key = os.path.normcase(str(lib.resolve()) if lib.exists() else str(lib))
        if key not in seen and (lib / "steamapps").is_dir():
            seen.add(key)
            out.append(lib)
    return out


def find_exe(cli=None):
    """Path to Melvor2.exe, or exit with instructions."""
    p = Path(cli) if cli else _setting("MELVOR2_EXE", "game_exe")
    if p:
        if p.is_dir():
            p = next((p / n for n in EXE_NAMES if (p / n).exists()), p / EXE_NAMES[0])
        if not p.exists():
            sys.exit(f"game executable not found: {p}")
        return p
    looked = []
    for lib in steam_libraries():
        for d in GAME_DIRS:
            for n in EXE_NAMES:
                cand = lib / "steamapps" / "common" / d / n
                looked.append(cand)
                if cand.exists():
                    return cand
    sys.exit("Could not find Melvor2.exe" + (" in any Steam library." if looked else " (no Steam library found).")
             + "\nPass --exe PATH, set MELVOR2_EXE, or set \"game_exe\" in config.local.json "
             "(see config.example.json).")


def save_dirs():
    """Folders the game may write saves to, most likely first."""
    home = Path.home()
    dirs = []
    if os.environ.get("APPDATA"):
        dirs.append(Path(os.environ["APPDATA"]) / SAVE_SUBDIR)
    if sys.platform == "darwin":
        dirs.append(home / "Library" / "Application Support" / SAVE_SUBDIR)
    elif sys.platform != "win32":
        dirs.append(Path(os.environ.get("XDG_DATA_HOME", home / ".local" / "share")) / SAVE_SUBDIR)
        # Proton keeps a Windows-style profile per game under compatdata
        for lib in steam_libraries():
            dirs += sorted((lib / "steamapps" / "compatdata").glob(
                "*/pfx/drive_c/users/steamuser/AppData/Roaming/" + SAVE_SUBDIR.as_posix()))
    return dirs


def _newest(folder):
    saves = sorted(folder.rglob("*.sav"), key=lambda p: p.stat().st_mtime)
    return saves[-1] if saves else None


def find_save(cli=None):
    """Path to the save to read (the newest .sav when given a folder), or exit with instructions."""
    p = Path(cli) if cli else _setting("MELVOR2_SAVE", "save")
    if p:
        if p.is_dir():
            found = _newest(p)
            if not found:
                sys.exit(f"no .sav file under {p}")
            return found
        if not p.exists():
            sys.exit(f"save file not found: {p}")
        return p
    found = [s for s in (_newest(d) for d in save_dirs() if d.is_dir()) if s]
    if found:
        return max(found, key=lambda s: s.stat().st_mtime)
    sys.exit("Could not find a Melvor Idle 2 save. Looked in:\n  " + "\n  ".join(map(str, save_dirs()))
             + "\nPass --save PATH, set MELVOR2_SAVE, or set \"save\" in config.local.json "
             "(see config.example.json).")


if __name__ == "__main__":
    print("Steam libraries:", *steam_libraries() or ["none found"], sep="\n  ")
    print("Save folders checked:", *save_dirs(), sep="\n  ")
    for label, fn in (("Game", find_exe), ("Save", find_save)):
        try:
            print(f"{label}: {fn()}")
        except SystemExit as e:
            print(f"{label}: not found - {e}")
