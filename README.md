# Melvor Idle 2 Demo — XP & GP per hour calculator

**Find the fastest way to level every skill in Melvor Idle 2, based on your own character.**

This tool reads your Melvor Idle 2 Demo save and the game's own data, then works
out how much XP and gold per hour every action in the game gives *you*: with your
levels, your equipment, your upgrades, your mastery and every other bonus you have
active. It produces two things:

1. **A report** (an HTML page you open in your browser) ranking the best XP/hour
   and GP/hour for every skill you can train right now, plus combat for each of
   your loadouts.
2. **A combat simulator** (a small page served from your own computer) where you
   can try different gear, food, spells, prayers and levels against any enemy or
   an auto-slayer master, and see the result before you change anything in game.

Unofficial fan tool, not affiliated with or endorsed by Games By Malcs.

---

## Contents

- [Why this exists](#why-this-exists)
- [What you get](#what-you-get)
- [How it works](#how-it-works)
- [Before you start](#before-you-start)
- [Quick start](#quick-start)
- [Everyday use](#everyday-use)
- [Using the report](#using-the-report)
- [Using the combat simulator](#using-the-combat-simulator)
- [If your game or save is not found](#if-your-game-or-save-is-not-found)
- [Command options](#command-options)
- [Privacy and safety](#privacy-and-safety)
- [Accuracy and limits](#accuracy-and-limits)
- [After a game update](#after-a-game-update)
- [Files in this repo](#files-in-this-repo)
- [License](#license)

---

## Why this exists

The XP shown on a recipe in game isn't what you actually earn per hour:

- **Most things need ingredients.** A smithing recipe looks fast until you count
  the time to mine the ore and smelt the bars. This tool works out the whole
  chain, starting from an empty bank, and charges every step's time to the
  final number.
- **Bonuses change everything.** Toolbelt items, mastery ranks, astrology,
  shop upgrades and equipment change intervals, success chances, doubling and
  preservation. The tool reads which bonuses *you* have and applies them.
- **Chains give XP in several skills at once.** Making a sword trains Mining,
  Smithing and maybe Firemaking. The report shows the XP for each of them.
- **Combat depends on your exact setup.** Kill speed, damage taken, food eaten
  and whether you can die depend on your gear, stance, prayers and the enemy's
  armour and special attacks. The simulator plays out the fights to find out.

## What you get

**The report** (`data/calc/report.html`)

- For every skill: your top actions by XP/hour or by GP/hour (toggle at the top).
- Each row opens to show the hour step by step. For example, *mine 120 Copper Ore →
  mine 120 Tin Ore → smelt 120 Bronze Bars → smith 40 Bronze Swords*, with the
  share of the hour each step takes and the XP it gives in each skill.
- **Combat** for each of your saved loadouts: XP, gold and loot per hour against
  every enemy you can fight, food eaten, and whether you might die. Where you have
  fought an enemy before, your real recorded results are shown next to the estimate.
- **Auto-slayer**: each slayer master's overall rate, which enemies drag it down,
  and how much blocking them would gain.
- `data/calc/rates.csv`: every action in one spreadsheet.

**The combat simulator** (`http://127.0.0.1:8765` while it's running)

- Starts with your saved loadouts. Change any equipment slot, stance, spell,
  prayer, food or combat level.
- Shows kills, XP, gold and food per hour, and how long your food lasts before
  you would die, for one enemy or for a whole slayer master.
- Pin a result as a baseline to compare changes against it.

## How it works

```
 Melvor2.exe ──► unpack_melvor.py ──► data/raw/      (the game's data files)
                                          │
                                          ▼
                 build_tables.py  ──► data/tables/   (recipes, items, enemies, bonuses)
                                          │
 your .sav   ──► read_save.py     ──► data/save/     (levels, bank, gear, active bonuses)
                                          │
                                          ▼
                 calculator.py    ──► data/calc/report.html, rates.csv
                 sim_server.py    ──► combat simulator in your browser
```

1. **`unpack_melvor.py`** copies the game's data files (recipes, items, enemies,
   and so on) out of the game program. Run it once.
2. **`build_tables.py`** turns those files into tidy tables. Run it once.
3. **`read_save.py`** decodes your save: levels, bank, equipment and loadouts,
   shop upgrades, mastery, astrology, slayer tasks and fight history. It also
   works out which bonuses are active. Run it whenever your character has changed.
4. **`calculator.py`** applies your bonuses to every action using the game's
   formulas, solves the cheapest chain for every ingredient, simulates combat,
   and writes the report.

The formulas used are written out in [docs/FORMULAS.md](docs/FORMULAS.md).

## Before you start

You need:

- **Melvor Idle 2 Demo**, installed through Steam, and a character you've played.
- **Python 3.10 or newer.** Get it from [python.org](https://www.python.org/downloads/).
  On Windows, tick "Add python.exe to PATH" in the installer.
- **Git** to clone the repo. You can also use GitHub's *Code → Download ZIP*
  and unzip it instead.

Windows is tested. Linux (Steam with Proton) and macOS are supported but untested.

> **Which Python command?** The examples use `python`. If that isn't found, use
> `py` on Windows or `python3` on Linux and macOS. The `-I` flag runs Python in
> isolated mode, so it won't pick up stray files from other folders. It's
> recommended but not required.

## Quick start

Open a terminal (on Windows: PowerShell or Command Prompt) and run:

```
git clone https://github.com/jtwilli/melvor2sim.git
cd melvor2sim
python -m pip install -r requirements.txt
```

That installs the only dependency, `brotli`, which is used to decompress the save.

Then, from the same folder:

```
python -I unpack_melvor.py
python -I build_tables.py
python -I read_save.py
python -I calculator.py --open
```

Your report opens in your browser. The first two commands take a few seconds.
The last one takes about a minute, because it simulates combat against every
enemy for every loadout.

`read_save.py` prints your character name and levels. Check that they're the
character you expect.

## Everyday use

After the first run you only need the last two commands:

```
python -I read_save.py
python -I calculator.py --open
```

The game autosaves about every 10 seconds, so there's no need to close it first.

To use the combat simulator:

```
python -I read_save.py
python -I sim_server.py
```

## Using the report

- **Rank by XP / Rank by gold** at the top switches the order of every list.
- Each skill lists its best actions. The columns are:
  - **XP / hr**: XP in that skill per hour, including the time spent gathering
    and crafting every ingredient from nothing.
  - **All-skill XP**: the XP earned in every skill along the chain.
  - **GP / hr**: what everything left at the end of the hour sells for, minus
    anything bought from the shop.
- **Click a row** to see the chain step by step: how many times you do each
  step per hour, its share of the hour, and the XP each step gives.
- Ingredients cheaper to buy than to make are bought from the shop, and the cost
  is subtracted from GP/hour. Run with `--no-shop` to always gather instead.
- **Combat** has a section per loadout. Each enemy shows XP and gold per hour,
  food used, and a warning if you can die or would run out of food. The food's
  fishing and cooking time is part of the chain.
- **Auto-slayer** lists every slayer master per loadout, its enemies from weakest
  to strongest XP/hour, and the gain from blocking each.
- The **"Limits of these numbers"** box at the bottom lists what isn't modelled.

## Using the combat simulator

Run `python -I sim_server.py`. A browser tab opens at `http://127.0.0.1:8765`.
The page is served by your own computer only; nothing goes online. Press Ctrl+C
in the terminal to stop it.

1. **Pick a loadout.** The buttons load each saved loadout's gear, stance, spell,
   prayers and food. The one you're wearing is selected first.
2. **Change anything.**
   - **Equipment:** each slot lists every item that fits it. Items you own or
     wear come first, and unmet level requirements are shown.
   - **Fighting style:** stance (or "compare all"), spell, prayers (as many as
     your Prayer level allows), food and how much of it you have.
   - **Levels:** set any combat level to test a "what if".
3. **Pick a target.**
   - **One enemy:** results for each stance. They include kills, XP by skill,
     gold, loot, food per hour and whether you can die. Your recorded history is
     shown if you've fought that enemy.
   - **Auto-slayer:** pick a master to see its overall rate and every enemy it
     can assign, weakest first, with the gain from blocking each. Tick enemies to
     block them. Your saved blocks start ticked.
4. **Pin as baseline** to keep a result. Later runs show the % change.
5. **Reload save** re-reads your save after you've played, without restarting.

Options: `--port 9000` to use another port, `--no-browser` to not open a tab.

## If your game or save is not found

The scripts search every Steam library on your computer for the game, and these
folders for your save:

| System | Save folder searched |
|---|---|
| Windows | `%APPDATA%\Games By Malcs\Melvor Idle 2` |
| Linux (Proton) | `<Steam library>/steamapps/compatdata/*/pfx/drive_c/users/steamuser/AppData/Roaming/Games By Malcs/Melvor Idle 2` |
| Linux | `~/.local/share/Games By Malcs/Melvor Idle 2` |
| macOS | `~/Library/Application Support/Games By Malcs/Melvor Idle 2` |

The most recently saved `.sav` file is used. To see what was found:

```
python -I paths.py
```

To point the scripts somewhere else, use any one of these (the first one found wins):

1. **Command-line option:** `python -I unpack_melvor.py --exe PATH` or
   `python -I read_save.py --save PATH`
2. **Environment variable:** `MELVOR2_EXE` or `MELVOR2_SAVE`
3. **Config file:** copy `config.example.json` to `config.local.json` and edit
   the paths. Git ignores this file, so your paths never end up in the repo.

The save setting takes a `.sav` file, or a folder to pick the newest save from.
The game setting takes `Melvor2.exe` or the folder it's in.

If you have several characters, use `--save` to choose one.

## Command options

`calculator.py`

| Option | Effect |
|---|---|
| `--open` | Open the report in your browser when done |
| `--top 20` | Rows listed per skill (default 10) |
| `--no-shop` | Never buy ingredients from the shop; gather or craft everything |
| `--passive-free` | Treat Farming and Ranching produce as free ingredients |
| `--no-combat` | Leave combat out (much faster) |
| `--check-combat` | Compare the combat model with the fights recorded in your save, then exit |
| `--assume-uncertain` | Apply mastery-rank bonuses the save cannot confirm |

`unpack_melvor.py`: `--exe PATH`, `--list` prints every file in the game pack,
`--all` extracts everything (about 490 MB) instead of just the data files.

`read_save.py`: `--save PATH`.

`sim_server.py`: `--port N`, `--no-browser`.

## Privacy and safety

- **Read-only.** The game folder and your save are only ever read, never changed.
- **Stays on your computer.** Nothing is uploaded. The simulator only accepts
  connections from your own computer.
- **No game files or saves in this repo.** Everything extracted or decoded goes
  into `data/`, which git ignores.
- **Before you share your report,** remember it shows your character name,
  levels and loadouts.

## Accuracy and limits

- Formulas follow the game's rules and are written out in
  [docs/FORMULAS.md](docs/FORMULAS.md).
- **Combat** is a simulation of many fights, not a guess. You can check it against
  your own recorded fights with `python -I calculator.py --check-combat`. Melee
  kill speeds match closely. Ranged and magic haven't been checked against
  recorded fights yet. Some enemy effects aren't simulated (stacking and
  charging effects, lifesteal, reflect), and the report names them where they
  apply.
- **Farming, Ranching and the Grain Mill** are passive, so they aren't ranked.
- **Sell prices** are the game's fixed prices.
- **Auto-slayer** assumes no delay between tasks.
- **Random results** are averages. For example, fishing time is the midpoint of
  its range.

Found something that doesn't match the game? Open an issue with the action, what
the tool says and what you see in game.

## After a game update

Re-run the first two commands before anything else:

```
python -I unpack_melvor.py
python -I build_tables.py
```

The tool was built against the Demo, save version 28. `read_save.py` prints a
warning if your save is a different version or contains something it doesn't
understand. The full game, when released, will likely need updates.

## Files in this repo

| File | Purpose |
|---|---|
| `paths.py` | Finds the game and your save (Steam libraries, save folders, overrides) |
| `unpack_melvor.py` | Extracts data files from the game program |
| `build_tables.py` | Turns the game data into tables in `data/tables/` |
| `read_save.py` | Decodes your save into `data/save/` and works out active bonuses |
| `calculator.py` | Applies bonuses, solves ingredient chains, writes the report |
| `combat.py` | Combat stats and fight simulation, food and death, auto-slayer |
| `report.py` | Builds the HTML report |
| `sim_server.py`, `sim.html` | The combat simulator |
| `docs/FORMULAS.md` | The formulas used and what isn't modelled |
| `config.example.json` | Template for `config.local.json` |
| `requirements.txt` | Python dependency (`brotli`) |

Generated (git-ignored): `data/raw/`, `data/tables/`, `data/save/`, `data/calc/`.

## License

MIT. See [LICENSE](LICENSE).
