# Melvor Idle 2 — XP & GP per hour calculator

**Find the fastest way to level every skill in Melvor Idle 2, based on your own character.**

This tool reads your Melvor Idle 2 save and the game's own data, then works
out how much XP and gold per hour every action in the game gives *you*: with your
levels, your equipment, your upgrades, your mastery and every other bonus you have
active. You can use it three ways:

1. **An XP button inside the game.** It opens a panel over the game with every
   tool in tabs: best XP and gold, a levelling planner, production per hour, a
   loadout comparison and a drop-chance calculator. See
   [The in-game panel](#the-in-game-panel).
2. **A report**: an HTML page you open in your browser. It ranks the best XP/hour
   and GP/hour for every skill you can train right now, plus combat for each of
   your loadouts.
3. **Two pages in your browser**, served from your own computer:
   - a **combat simulator**, where you can try different gear, food, spells,
     prayers and levels against any enemy, dungeon or auto-slayer master;
   - a **levelling planner**, which plans the fastest route from your current level
     to any target level.

Every skill is covered: the gathering skills, the crafting skills, Cooking,
Herblore, Thieving, Hunting, Agility, Astrology and combat. Farming and Ranching
are passive, so they're used as ingredient sources rather than ranked.

Unofficial fan tool, not affiliated with or endorsed by Games By Malcs.

---

## Contents

- [Why this exists](#why-this-exists)
- [What you get](#what-you-get)
- [How it works](#how-it-works)
- [Before you start](#before-you-start)
- [Quick start](#quick-start)
- [The in-game panel](#the-in-game-panel)
- [Everyday use](#everyday-use)
- [Using the report](#using-the-report)
- [Using the combat simulator](#using-the-combat-simulator)
- [Using the levelling planner](#using-the-levelling-planner)
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
  final number. Chains follow every way the game lets you get an item: gathering,
  crafting, shop purchases, opening items (a Seaweed Clump gives 100 Seaweed) and
  the Grain Mill.
- **Bonuses change everything.** Toolbelt items, mastery ranks, astrology, pets,
  shop upgrades and equipment change intervals, success chances, doubling and
  preservation. The tool reads which bonuses *you* have and applies them.
- **Chains give XP in several skills at once.** Making a sword trains Mining,
  Smithing and maybe Firemaking. Every view shows the XP for each of them.
- **Some actions cost more than they show.** Thieving and Agility failures hurt,
  and the food you eat has to be caught and cooked. Combat uses food, arrows, runes
  and prayer points. All of that is counted.
- **Combat depends on your exact setup.** Kill speed, damage taken, food eaten
  and whether you can die depend on your gear, stance, prayers and the enemy's
  armour and special attacks. The simulator plays out the fights to find out.

## What you get

**The in-game panel**: an XP button in the game's top banner. See
[The in-game panel](#the-in-game-panel) for the tabs and how to install it.

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

**The combat simulator** (`http://127.0.0.1:8765` while the server is running)

- Starts with your saved loadouts. Change any equipment slot, stance, spell,
  prayer, food or combat level.
- Shows kills, XP, gold and food per hour, and how long your food lasts before
  you would die, for one enemy or for a whole slayer master.
- Pin a result as a baseline to compare changes against it.

**The levelling planner** (`http://127.0.0.1:8765/planner`, also a tab in the panel)

- Pick a skill and a target level to get the fastest route there, step by step.
- Each step shows the levels it covers, how long it takes, and exactly what to do:
  how many times to do each action in the chain and how many items each one makes
  (for example *mine 453 Rune Essence → craft 969 Water Runes*).
- Optionally restrict it to a category (Arrows, Runes, Staves & Wands...) or to
  what the items are used for (combat, consumables, ingredients for another skill).
- Shows the levels you gain in other skills on the way, and everything you end up with.

## How it works

```
 Melvor2.exe ──► unpack_melvor.py ──► data/raw/      (the game's data files)
                                          │
                                          ▼
                 build_tables.py  ──► data/tables/   (recipes, items, enemies, bonuses)
                                          │
 your .sav   ──► read_save.py     ──► data/save/     (levels, bank, gear, quests, active bonuses)
                                          │
                                          ▼
                 calculator.py    ──► data/calc/report.html, rates.csv
                 sim_server.py    ──► browser pages, and the in-game panel's numbers
```

1. **`unpack_melvor.py`** copies the game's data files (recipes, items, enemies,
   and so on) out of the game program. Run it once, and again after game updates.
2. **`build_tables.py`** turns those files into tidy tables. Run it once, and
   again after game updates.
3. **`read_save.py`** decodes your save: levels, bank, equipment and loadouts,
   shop upgrades, mastery, astrology, pets, Agility techniques, quest progress,
   statistics, slayer tasks and fight history. It also works out which bonuses are
   active. Run it whenever your character has changed (the in-game panel and the
   server do this for you).
4. **`calculator.py`** applies your bonuses to every action using the game's
   formulas, solves the cheapest chain for every ingredient, simulates combat,
   and writes the report.
5. **`sim_server.py`** runs the same calculations on demand for the browser pages
   and the in-game panel.

The formulas used are written out in [docs/FORMULAS.md](docs/FORMULAS.md).

## Before you start

You need:

- **Melvor Idle 2** (Early Access), installed through Steam, and a character you've played.
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

To get the XP button inside the game as well, carry on with the next section.

## The in-game panel

The panel puts every tool one click away while you play. It adds an **XP button**
to the right of the game's top banner, next to the statistics button. Clicking it
opens a panel over the game; Esc, **Close** or a click outside closes it.

### Installing the button

Do the [Quick start](#quick-start) first, so the game data and your save have been
read at least once. Then **close the game** and run, from the repo folder:

```
python -I install_mod.py
```

Start the game. The XP button appears in the top banner once your character has
loaded.

What the installer does:

1. Copies the panel's scripts (`mod/*.gd`) into a `melvor2sim` folder inside the
   game's user-data folder, next to your saves.
2. Writes `melvor2sim/config.json` there, recording where this repo and Python are,
   so the panel can start `sim_server.py` itself.
3. Writes **one file into the game folder**: `override.cfg` next to `Melvor2.exe`.
   It tells the game engine to load the panel's script at startup. No game file is
   changed.

When the game starts, the panel starts `sim_server.py` in the background and stops
it when the game closes. You don't need to run anything else while you play. The
first open after starting the game takes a few seconds while your save is read.

**Check or remove it:**

| Command | What it does |
|---|---|
| `python -I install_mod.py --status` | Shows whether it's installed, and what the panel reported the last time the game ran (button found, server started, any errors) |
| `python -I install_mod.py --uninstall` | Removes `override.cfg` and the copied scripts. The game is back to normal |
| `python -I install_mod.py --exe PATH` | Installs for a game in a folder that isn't found automatically |
| `python -I install_mod.py --port 9000` | Uses another port if 8765 is taken |

Run `install_mod.py` again after you pull a new version of this repo, so the game
gets the updated scripts. The panel also reloads its scripts when they change, so a
reinstall while the game is running takes effect the next time you open it.

**If the button doesn't appear:**

1. Run `python -I install_mod.py --status`. If it says `override.cfg: not installed`,
   the install didn't reach the game folder. Close the game and run the installer
   again, or pass `--exe` with the path to `Melvor2.exe`.
2. If it's installed but the status shows errors, a game update may have moved
   the top banner. Open an issue with what `--status` prints.

> This is an unofficial UI mod. It only adds a button and a panel; it doesn't
> change how the game plays or touch your save. A game update can break it. If
> that happens, `--uninstall` removes it completely.

### The tabs

Every tab uses your current save. **Reload save** re-reads it after you've played a
while, and **Open in browser** opens the browser pages.

**Best XP / GP**

- The best action in every skill by XP/hour or GP/hour, or the top actions in one
  skill. Each one shows its whole chain.
- **Include combat** adds your best enemies with the loadout you're wearing,
  including what each fight uses (food, ammo, runes) and the gold left after
  supplies.
- **Look ahead to level:** every action in one skill up to a level you choose,
  including ones you haven't unlocked yet. It shows XP/hour when each unlocks and at
  the target level, and what else each still needs (a tool, a quest, a shop
  purchase). For Thieving and Agility it also shows the success chance, food per
  hour and whether a failure can kill you.

**Levelling planner**

- The planner from the browser, with these options:
  - **Make / Used for:** level a certain way, for example Fletching by making arrows.
  - **Use items in my bank:** take ingredients from your bank before gathering more.
  - **Never buy from the shop.**
  - **Assume I get required tools:** plan as if you had the toolbelt items an
    action needs.
  - **Food** (Thieving and Agility): the food eaten when a failure hurts you. The
    time to make it is counted, so a cheap food can be faster.
  - **Extra stealth** (Thieving): test gear or ranks you don't have yet.
  - **Loadout** (combat skills): which saved loadout to fight with.

**Production**

- Pick any item to see the fastest way to make it per hour, every other way ranked
  below it, and the best chain step by step.
- **Include quest actions:** some quests open extra actions while a quest step is
  running, for example cutting and burning Treant Remains during Tree Trimming.
  These can be strong resource generators. The tab reads your quest progress from
  the save, includes the ones open now, and lists them with their rates.
- **Include combat drops:** also ranks enemies that drop the item, fought with a
  loadout you choose.

**Compare loadouts**

- Two setups side by side, A and B, starting from your saved loadouts. Change gear
  by clicking a slot, then stance, spell, food, prayers or "what if" levels.
- Runs A and B against every enemy you can fight, the dungeons, a slayer master's
  list or one area. A chart shows the difference with its uncertainty.
- Click a target for the detail: XP, kills, kill time, food, prayer and supplies
  for A and B side by side. "B is better" appears only when the difference is
  larger than the uncertainty.

**Maths**

- **Attempts until a drop:** enter a drop chance (2%, or 1/50) to see how many
  attempts give a 50%, 90%, 99%... chance of seeing it. It also works for several
  drops, and converts attempts to time if you enter attempts per hour.

## Everyday use

With the in-game panel installed, just play: everything is in the XP button.

Without it, after the first run you only need the last two commands:

```
python -I read_save.py
python -I calculator.py --open
```

The game autosaves about every 10 seconds, so there's no need to close it first.

To use the combat simulator and the levelling planner in your browser:

```
python -I read_save.py
python -I sim_server.py
```

The combat simulator opens at `http://127.0.0.1:8765`. The levelling planner is at
`http://127.0.0.1:8765/planner`, and each page links to the other. If the game is
running with the panel installed, its server is already up and the panel's
**Open in browser** button takes you there.

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

For comparing two setups across many enemies and dungeons at once, use the
panel's **Compare loadouts** tab.

## Using the levelling planner

Open `http://127.0.0.1:8765/planner` while `sim_server.py` is running (or follow
the **Levelling planner** link on the combat simulator), or use the panel's
**Levelling planner** tab.

1. **Pick a skill and a target level.** The quick buttons fill in +1, +5, +10, 50
   or 99. Every skill is capped at 99.
2. **Optionally set a focus** to level a certain way:
   - **Make:** a category from the game, such as Fletching › Arrows,
     Runecrafting › Standard Runes, or Smithing › Iron Gear.
   - **Used for:** what the final product is for. The options are used in combat
     (gear, ammo, runes, food), combat gear only, ammo, runes, food, consumables,
     skilling tools, or an ingredient for another skill (for example Crafting
     items that Hunting uses as traps).

   You can set both. The focus only limits the final action; ingredients still
   come from wherever is fastest. Each option shows the level it first unlocks.
   Until then, the planner uses the best action overall and marks those steps
   "outside your focus".
3. **Press Plan route.**

How the route is chosen:

- **Each level:** the planner picks the action with the most XP per hour in the
  skill. The rate includes the time to gather and craft ingredients from nothing,
  using the same chains as the report.
- **XP in other skills** along the way raises those levels too, and later steps
  can use what they unlock.
- **Temporary quest actions are left out**, because they disappear when you hand
  the quest step in. The panel's Production tab shows them.

The result shows:

- **Each step:** the levels it covers, XP/hour, time and running total, then
  every action in the chain with **how many times to do it and how many items
  it makes**. For example, *Mining: Rune Essence 453× → 969 Rune Essence;
  Runecrafting: Water Rune 969× → 969 Water Rune*. Shop purchases are counted
  too.
- **Totals:** total time, everything you end up with, everything bought, and the
  levels you gain in other skills.

**Combat skills:** pick one of your saved loadouts. The planner re-chooses the
enemy as your levels rise and avoids fights where you could die. Combat plans
take a few seconds, because each checkpoint simulates every enemy.

- **Each combat step** shows kills, loot, and everything used up: food, runes,
  ammo and prayer points, as a total for the step and per hour.
- **A Supplies table** totals each one for the whole route next to how many you
  have (from your bank, quiver and prayer points), and shows when and at what
  level it would run out.
- **Prayer points:** some prayers drain on your attack turns and some on the
  enemy's. Both are counted, including any bonuses that lower prayer cost.

**Not counted:** the mastery and other bonuses you'd earn on the way, so real
times should be a little shorter.

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
| `--passive-free` | Treat all Farming and Ranching produce as free ingredients |
| `--no-combat` | Leave combat out (much faster) |
| `--check-combat` | Compare the combat model with the fights recorded in your save, then exit |
| `--assume-uncertain` | Apply mastery-rank bonuses the save cannot confirm |

`unpack_melvor.py`: `--exe PATH`, `--list` prints every file in the game pack,
`--all` extracts everything (about 490 MB) instead of just the data files.

`read_save.py`: `--save PATH`.

`sim_server.py`: `--port N`, `--no-browser`.

`install_mod.py`: `--status`, `--uninstall`, `--exe PATH`, `--port N` (see
[The in-game panel](#the-in-game-panel)).

## Privacy and safety

- **Your save is only read.** Nothing ever writes to it.
- **The game folder is only read,** except for the one `override.cfg` file that
  `install_mod.py` writes if you install the in-game panel. `--uninstall` removes it.
- **Stays on your computer.** Nothing is uploaded. The server, used by the browser
  pages and the panel, only accepts connections from your own computer.
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
  Their produce counts as free in chains when nothing else makes it, such as the
  herbs Herblore needs.
- **Agility** success depends on your technique levels (Climbing, Jumping, ...),
  which are kept at today's levels. Plans and look-aheads get better than shown as
  you run courses. Agility and Thieving count the time to make the food they eat,
  so pick a cheap food in the planner.
- **Astrology** counts the sighting logs from events. Temporary bonus events
  (moons, auroras) aren't counted.
- **Not modelled:** Firemaking bonfires (a timed buff), Thieving distractions,
  and the chance to find pets. Pets you've already found are applied.
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

The tool is built against the Early Access release, save version 31. `read_save.py`
prints a warning if your save is a different version or contains something it doesn't
understand. If a game update changes something the tool relies on, it may need an
update too. Check this repo for a newer version.

If the in-game panel stops appearing after an update, run
`python -I install_mod.py --status` and see
[If the button doesn't appear](#installing-the-button).

## Files in this repo

| File | Purpose |
|---|---|
| `paths.py` | Finds the game and your save (Steam libraries, save folders, overrides) |
| `unpack_melvor.py` | Extracts data files from the game program |
| `build_tables.py` | Turns the game data into tables in `data/tables/` |
| `read_save.py` | Decodes your save into `data/save/` and works out active bonuses |
| `calculator.py` | Applies bonuses, solves ingredient chains, writes the report |
| `combat.py` | Combat stats and fight simulation, dungeons, food and death, auto-slayer |
| `report.py` | Builds the HTML report |
| `planner.py` | Levelling routes and look-ahead |
| `sim_server.py` | Local server for the browser pages and the in-game panel |
| `game_api.py` | What each panel tab shows, ready to draw |
| `sim.html`, `planner.html` | The combat simulator and levelling planner pages |
| `install_mod.py` | Installs, checks or removes the in-game panel |
| `mod/` | The in-game panel's scripts: `loader.gd` adds the button, `panel.gd` the panel, `tab_*.gd` the tabs |
| `check_mod.py` | Developer tool: compiles the panel's scripts with the game's engine to catch errors |
| `docs/FORMULAS.md` | The formulas used and what isn't modelled |
| `config.example.json` | Template for `config.local.json` |
| `requirements.txt` | Python dependency (`brotli`) |

Generated (git-ignored): `data/raw/`, `data/tables/`, `data/save/`, `data/calc/`.

## License

MIT. See [LICENSE](LICENSE).
