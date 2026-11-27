"""Read a Melvor Idle 2 save and work out which bonuses are currently active.

The save is opened read-only; nothing is written to the game folder.
Needs: pip install brotli, and data/tables/ from build_tables.py.

    python -I read_save.py                 # newest .sav, found automatically (see paths.py)
    python -I read_save.py --save PATH     # a .sav file, or a folder to take the newest from

Writes data/save/player_state.json and data/save/active_bonuses.json.
"""
import argparse
import json
import math
import struct
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import brotli

sys.path.insert(0, str(Path(__file__).resolve().parent))  # sibling modules, also under python -I
import paths  # noqa: E402

ROOT = Path(__file__).parent
TABLES = ROOT / "data" / "tables"
OUT = ROOT / "data" / "save"
NS = "MelvorBase:"
SUPPORTED_VERSIONS = {31, 32}


def short(i):
    return i[len(NS):] if isinstance(i, str) and i.startswith(NS) else i


class Reader:
    """Little-endian reader for the save's binary layout."""

    def __init__(self, buf, pos=0, end=None):
        self.buf, self.pos, self.end = buf, pos, len(buf) if end is None else end

    def _take(self, fmt, size):
        if self.pos + size > self.end:
            raise EOFError(f"read past end at {self.pos}")
        value = struct.unpack_from(fmt, self.buf, self.pos)[0]
        self.pos += size
        return value

    def i32(self): return self._take("<i", 4)
    def i64(self): return self._take("<q", 8)
    def u64(self): return self._take("<Q", 8)
    def f64(self): return self._take("<d", 8)
    def bool(self): return self._take("<B", 1) != 0

    def bytes(self, n):
        if self.pos + n > self.end:
            raise EOFError(f"read past end at {self.pos}")
        out = self.buf[self.pos:self.pos + n]
        self.pos += n
        return out

    def str(self):
        n = shift = 0
        while True:  # 7-bit encoded length prefix
            b = self._take("<B", 1)
            n |= (b & 0x7F) << shift
            shift += 7
            if not b & 0x80:
                break
        return self.bytes(n).decode("utf-8")

    def region(self):
        """A length-prefixed block; returns a sub-reader and skips past it."""
        n = self.i32()
        sub = Reader(self.buf, self.pos, self.pos + n)
        self.bytes(n)
        return sub

    @property
    def left(self):
        return self.end - self.pos


def decompress(path):
    # The game flushes but never finalizes the Brotli stream, so one-shot
    # decompress() rejects it; the streaming decoder returns everything written.
    dec = brotli.Decompressor()
    parts = [dec.process(Path(path).read_bytes())]
    while True:  # process() hands output back in pieces; drain until empty
        more = dec.process(b"")
        if not more:
            break
        parts.append(more)
    return b"".join(parts)


def read_header(r):
    version = r.i32()
    uuid = r.bytes(16)
    h = {"version": version, "character": r.str(), "gamemode": r.str()}
    ticks = r.i64() & 0x3FFFFFFFFFFFFFFF  # .NET DateTime.ToBinary: top 2 bits are the Kind
    try:
        h["timestamp"] = datetime.fromordinal(1).replace(microsecond=0).__add__(
            __import__("datetime").timedelta(microseconds=ticks // 10)).isoformat()
    except OverflowError:
        h["timestamp"] = None
    h["active_entity"], _icon1 = r.str(), r.str()
    h["active_action"], _icon2 = r.str(), r.str()
    h["total_level"], h["gp"], h["slayer_coins"], h["is_demo"] = r.i32(), r.i64(), r.i64(), r.bool()
    h["uuid"] = uuid.hex()
    return h


def read_entities(r):
    """entity id -> {component name -> Reader over its payload}"""
    entities = {}
    for _ in range(r.i32()):
        name = r.str()
        body = r.region()
        comps = {}
        for _ in range(body.i32()):
            cname = body.str()
            comps[cname] = body.region()
        entities[short(name)] = comps
    return entities


# ---- component decoders ----

LEVEL_CAP = 99


def _xp_table(cap=LEVEL_CAP + 1):
    """XP needed for each level: floor(0.25 * sum(floor(i + 300 * 2^(i/7)))), level 1 = 0."""
    out, total = [0.0], 0.0
    for i in range(1, cap):
        total += math.floor(i + 300 * 2 ** (i / 7))
        out.append(math.floor(0.25 * total))
    return out


XP_TABLE = _xp_table()


def level_from_xp(xp):
    level = 1
    while level < LEVEL_CAP and xp >= XP_TABLE[level]:
        level += 1
    return level


def d_experience(r):
    # the save stores only the XP; the game works the level out from it (capped at 99)
    xp = max(0.0, r.f64())
    return {"xp": xp, "level": level_from_xp(xp)}


def d_slots(r, has_occupied=False):
    """Toolbelt / Equipment: active slots, then saved loadouts (ignored)."""
    slots = {}
    for _ in range(r.i32()):
        slot = r.str()
        s = r.region()
        qty = s.i32()
        if has_occupied:
            s.bool()
        if s.bool():
            slots[short(slot)] = {"item": s.str(), "qty": qty}
    return slots


def d_equipment(r):
    """Combat equipment: the slots worn now, then one saved set per loadout."""
    active = d_slots(r, True)
    loadouts = {}
    for _ in range(r.i32()):
        lid = r.str()
        loadouts[short(lid)] = d_slots(r.region(), True)
    return {"active": active, "loadouts": loadouts}


def d_loadouts(r):
    current = short(r.str())
    names = {}
    for _ in range(r.i32()):
        lid = r.str()
        s = r.region()
        names[short(lid)] = {"name": s.str(), "unlocked": s.bool()}
    return {"current": current, "slots": names}


def d_loot_history(r):
    """Per enemy: kills, time from spawn to kill, drops and XP actually recorded by the game."""
    out = {}
    for _ in range(r.i32()):
        enemy = r.str()
        h = r.region()
        kills = h.i32()
        items = {}
        for _ in range(h.i32()):
            q = h.i32()
            items[h.str()] = q
        currencies = {}
        for _ in range(h.i32()):
            q = h.i64()
            currencies[short(h.str())] = q
        last_tick = h.u64()
        seconds = h.i64() / 1e7  # .NET TimeSpan ticks
        xp = {}
        for _ in range(h.i32()):
            amount = h.f64()
            entity, _component = h.str(), h.str()
            xp[short(entity)] = amount
        out[enemy] = {"kills": kills, "seconds": seconds, "xp": xp, "items": items,
                      "currencies": currencies, "last_tick": last_tick}
    return out


def opt_str(r):
    return r.str() if r.bool() else None


def d_prayer(r):
    slots = {}
    for _ in range(r.i32()):
        slot = short(r.str())
        s = r.region()
        selected = opt_str(s)
        active = None
        if s.bool():
            state = s.i32()
            active = {"prayer": s.str(), "state": state}
        slots[slot] = {"selected": selected, "active": active}
    opt_str(r)                                  # selected slot type
    for _ in range(r.i32()): r.str()            # favourites
    for _ in range(r.i32()): r.str()            # hidden
    r.bool(); r.bool()
    loadouts = {}
    for _ in range(r.i32()):
        lid = short(r.str())
        l = r.region()
        loadouts[lid] = {short(l.str()): l.str() for _ in range(l.i32())}
    return {"slots": slots, "loadouts": loadouts}


def d_spells(r):
    out = {"spell": opt_str(r), "imbuement": opt_str(r)}
    opt_str(r); opt_str(r); r.bool()            # spell book, activated imbuement, buffering flag
    for _ in range(r.i32()):                    # default spell per element
        r.str(); r.str()
    out["loadouts"] = {}
    for _ in range(r.i32()):
        lid = short(r.str())
        l = r.region()
        out["loadouts"][lid] = {"spell": opt_str(l), "imbuement": opt_str(l)}
    return out


def d_food(r):
    out = {"food": opt_str(r)}
    for _ in range(r.i32()): r.str()
    for _ in range(r.i32()): r.str()
    r.bool(); r.bool(); r.i32()
    out["loadouts"] = {}
    for _ in range(r.i32()):
        lid = short(r.str())
        out["loadouts"][lid] = r.str()
    return out


def d_pets(r):
    """pet id -> unlocked (each skill keeps the pets it can find)."""
    out = {}
    for _ in range(r.i32()):
        pid = r.str()
        out[pid] = r.region().bool()
    return out


def agility_technique_level(xp, cap):
    """Agility techniques use their own XP curve."""
    need = lambda lv: math.floor((lv * lv - lv + 600 * ((2 ** (lv / 3.25) - 2 ** (1 / 3.25)) / (2 ** (1 / 3.25) - 1))) / 6)
    level = 1
    while level < cap and xp >= need(level + 1):
        level += 1
    return level


def d_techniques(r):
    """Agility technique id -> XP."""
    out = {}
    for _ in range(r.i32()):
        tid = r.str()
        out[tid] = r.region().f64()
    return out


def d_areas(r):
    r.i32(); r.i32()
    return {"area": opt_str(r), "enemy": opt_str(r), "dungeon": opt_str(r)}


def scan_loadout_stances(blob):
    """Stance saved per loadout. Sits after variable-length turn data in Combat/Player,
    so it is located by its id strings instead of parsed positionally."""
    out = {}
    for n in range(1, 10):
        key = f"{NS}Loadout{n}".encode()
        at = blob.find(bytes([len(key)]) + key)
        if at < 0:
            continue
        pos = at + 1 + len(key)
        if pos < len(blob) and pos + 1 + blob[pos] <= len(blob):
            val = blob[pos + 1:pos + 1 + blob[pos]].decode("utf-8", "replace")
            if val.startswith(NS):
                out[f"Loadout{n}"] = val
    return out


def scan_weapon_stances(blob):
    """Stance chosen per weapon type (what the worn loadout uses)."""
    path = TABLES / "objects" / "WeaponType.json"
    if not path.exists():
        return {}
    out = {}
    for wt in json.loads(path.read_text(encoding="utf-8")):
        key = (NS + wt["Id"]).encode()
        start = 0
        while True:
            at = blob.find(bytes([len(key)]) + key, start)
            if at < 0:
                break
            pos = at + 1 + len(key)
            start = pos
            if pos < len(blob) and pos + 1 + blob[pos] <= len(blob):
                val = blob[pos + 1:pos + 1 + blob[pos]].decode("utf-8", "replace")
                if val in wt["Stances"]:
                    out[NS + wt["Id"]] = val
                    break
    return out


def d_slayer(r):
    """Slayer masters: current task, progress, auto-slayer flag and block slots."""
    masters = {}
    for _ in range(r.i32()):
        mid = r.str()
        m = r.region()
        task = opt_str(m)
        length, progress, extended, auto = m.i32(), m.i32(), m.bool(), m.bool()
        slots = {}
        for _ in range(m.i32()):
            sid = m.str()
            b = m.region()
            slots[sid] = {"unlocked": b.bool(), "blocked": opt_str(b)}
        masters[mid] = {"task": task, "length": length, "progress": progress, "extended": extended,
                        "auto_slayer": auto, "block_slots": slots}
    recent = opt_str(r)
    return {"masters": masters, "recent": recent}


def d_stack_inventory(r):
    r.i32(); r.i32(); r.i32(); r.bool(); r.bool()  # stack limits and flags
    stacks = []
    for _ in range(r.i32()):
        qty, placeholder, locked = r.i32(), r.bool(), r.bool()
        stacks.append({"item": r.str(), "qty": qty, "placeholder": placeholder, "locked": locked})
    return stacks


def d_bank(r, version=31):
    r.i32()  # default tab for new items
    tabs = []
    for _ in range(r.i32()):
        stacks = d_stack_inventory(r)
        label = r.str()
        for _ in range(r.i32()):  # associated bank tags
            r.bool(); r.bool(); r.str()
        if version >= 32:
            r.bool()  # additional per-tab flag introduced in save v32
        tabs.append({"label": label, "stacks": stacks})
    return tabs


def d_wallet(r):
    out = {}
    for _ in range(r.i32()):
        qty = r.i64()
        out[short(r.str())] = qty
    return out


def d_shop(r):
    r.bool(); r.bool(); r.bool(); r.i32(); r.i32()  # UI settings
    out = {}
    for _ in range(r.i32()):
        count = r.i32()
        out[r.str()] = count
    return out


def d_mastery(r):
    if r.bool():
        r.str()  # selected category
    tasks = {}
    for _ in range(r.i32()):
        task = r.str()
        t = r.region()
        entry = {"progress": None, "goal_complete": None}
        if t.bool():
            t.str()  # goal id
            g = t.region()
            entry["progress"], entry["goal_complete"] = g.f64(), g.bool()
        entry["trigger_met"] = t.bool()
        tasks[task] = entry
    return tasks


def d_astrology(r):
    r.bool()                                   # IsStarted
    r.bool(); r.i32(); r.i32(); r.i32()        # action timer
    r.bool(); r.bool()                         # view toggles
    out = {}
    for _ in range(r.i32()):
        cid = r.str()
        c = r.region()
        knowledge = c.f64()
        minor = [c.str() for _ in range(c.i32())]
        major = [c.str() for _ in range(c.i32())]
        out[cid] = {"knowledge": knowledge, "minor_nodes": minor, "major_nodes": major}
    return out


def d_journal(r):
    out = {}
    for _ in range(r.i32()):
        animal = r.str()
        e = r.region()
        discovered = e.bool()
        xp, level = e.f64(), e.i32()
        out[animal] = {"discovered": discovered, "xp": xp, "level": level}
    return {"animals": out, "unique_rare_species_found": r.i32()}


def d_statistics(r):
    """Simple value stats, then per-item and per-currency dictionaries."""
    values = {}
    for _ in range(r.i32()):
        sid = r.str()
        values[sid] = r.region().f64()

    def dict_stats():
        out = {}
        for _ in range(r.i32()):
            sid = r.str()
            d = r.region()
            entries = {}
            for _ in range(d.i32()):
                v = d.f64()
                entries[d.str()] = v
            out[sid] = entries
        return out

    out = {"values": values, "items": dict_stats(), "currencies": dict_stats(), "objects": {}}
    while r.left > 0:      # per-course, per-enemy, per-event ... tables, in the same layout
        out["objects"].update(dict_stats())
    return out


def d_bait(r):
    return r.str() if r.bool() else None


def d_quests(r):
    """Quest progress: quest id -> {status, steps: [{id, status, tasks: [{id, status, goals: {id: {progress, complete}}}]}]}.
    Quest status 0 not started, 1 in progress, 3 complete; step and task status 0 not reached, 1 active, 2 done."""
    out = {}
    for _ in range(r.i32()):
        qid = r.str()
        q = r.region()
        status, steps = q.i32(), []
        for _ in range(q.i32()):
            sid = q.str()
            s = q.region()
            tasks = []
            for _ in range(s.i32()):
                tid = s.str()
                t = s.region()
                goals = {}
                for _ in range(t.i32()):
                    gid = t.str()
                    g = t.region()
                    goals[gid] = {"progress": g.f64(), "complete": g.bool()}
                t.bool(); t.bool()
                tasks.append({"id": tid, "status": t.i32(), "goals": goals})
            s.bool()
            steps.append({"id": sid, "status": s.i32(), "tasks": tasks})
        out[qid] = {"status": status, "steps": steps}
    return out


def try_decode(fn, reader, label, warnings, default=None):
    if reader is None:
        return default
    try:
        return fn(reader)
    except (EOFError, struct.error, UnicodeDecodeError) as e:
        warnings.append(f"could not decode {label}: {e}")
        return default


def parse_save(path):
    buf = decompress(path)
    r = Reader(buf)
    header = read_header(r)
    entities = read_entities(r)
    warnings = []
    if header["version"] not in SUPPORTED_VERSIONS:
        warnings.append(f"save version {header['version']} not in {sorted(SUPPORTED_VERSIONS)}; layouts may have changed")

    state = {"save_file": path.name,"header": header, "skills": {}, "toolbelts": {}, "mastery_tasks": {},
             "value_statistics": {}, "item_statistics": {}, "object_statistics": {}}
    for name, comps in entities.items():
        xp = try_decode(d_experience, comps.get("Experience"), f"{name}/Experience", warnings)
        if xp:
            state["skills"][name] = xp
        tb = try_decode(d_slots, comps.get("Toolbelt"), f"{name}/Toolbelt", warnings)
        if tb is not None:
            state["toolbelts"][name] = tb
        mg = try_decode(d_mastery, comps.get("MasteryGuild"), f"{name}/MasteryGuild", warnings)
        if mg:
            state["mastery_tasks"][name] = mg
        st = try_decode(d_statistics, comps.get("Statistics"), f"{name}/Statistics", warnings)
        if st:
            state["value_statistics"].update(st["values"])
            state["item_statistics"].update(st["items"])
            state["object_statistics"].update(st["objects"])

    get = lambda e, c: entities.get(e, {}).get(c)
    tabs = try_decode(lambda reader: d_bank(reader, header["version"]),
                      get("Bank", "Inventory"), "Bank/Inventory", warnings, [])
    state["bank_tabs"] = tabs
    bank = defaultdict(int)
    for tab in tabs:
        for s in tab["stacks"]:
            if not s["placeholder"]:
                bank[s["item"]] += s["qty"]
    state["bank"] = dict(sorted(bank.items()))
    state["currencies"] = try_decode(d_wallet, get("Bank", "Wallet"), "Bank/Wallet", warnings, {})
    state["shop_purchases"] = try_decode(d_shop, get("Shop", "Shop"), "Shop/Shop", warnings, {})
    equip = try_decode(d_equipment, get("Combat", "Equipment"), "Combat/Equipment", warnings) or {"active": {}, "loadouts": {}}
    state["equipment"] = equip["active"]
    state["equipment_loadouts"] = equip["loadouts"]
    state["loadouts"] = try_decode(d_loadouts, get("Combat", "Loadouts"), "Combat/Loadouts", warnings) or {}
    state["slayer"] = try_decode(d_slayer, get("Slayer", "Tasks"), "Slayer/Tasks", warnings) or {"masters": {}, "recent": None}
    state["loot_history"] = try_decode(d_loot_history, get("Combat", "LootHistory"), "Combat/LootHistory", warnings, {})
    state["prayers"] = try_decode(d_prayer, get("Combat", "Prayer"), "Combat/Prayer", warnings) or {"slots": {}, "loadouts": {}}
    state["spells"] = try_decode(d_spells, get("Combat", "MagicSpells"), "Combat/MagicSpells", warnings) or {"spell": None, "loadouts": {}}
    state["food"] = try_decode(d_food, get("Combat", "Food"), "Combat/Food", warnings) or {"food": None, "loadouts": {}}
    # Thieving shares your HP with combat but has its own food slot
    state["thieving_food"] = (try_decode(d_food, get("Thieving", "Food"), "Thieving/Food", warnings) or {}).get("food")
    state["combat_area"] = try_decode(d_areas, get("Combat", "Areas"), "Combat/Areas", warnings) or {}
    pl = get("Combat", "Player")
    state["loadout_stances"] = scan_loadout_stances(pl.buf[pl.pos:pl.end]) if pl else {}
    state["weapon_stances"] = scan_weapon_stances(pl.buf[pl.pos:pl.end]) if pl else {}
    state["astrology"] = try_decode(d_astrology, get("Astrology", "Action"), "Astrology/Action", warnings, {})
    journal = try_decode(d_journal, get("Hunting", "Journal"), "Hunting/Journal", warnings) or {}
    state["hunting_journal"] = journal.get("animals", {})
    state["hunting_unique_rare_species_found"] = journal.get("unique_rare_species_found")
    state["fishing_bait"] = try_decode(d_bait, get("Fishing", "BaitComponent"), "Fishing/BaitComponent", warnings)
    state["quests"] = try_decode(d_quests, get("Quests", "Quests"), "Quests/Quests", warnings, {})
    pets = {}
    for name, comps in entities.items():
        pets.update(try_decode(d_pets, comps.get("Pets"), f"{name}/Pets", warnings, {}))
    state["pets"] = sorted(p for p, unlocked in pets.items() if unlocked)
    caps = {NS + t["Id"]: t.get("LevelCap") or 99 for t in _objects_list("AgilityTechnique")}
    tech = try_decode(d_techniques, get("Agility", "Techniques"), "Agility/Techniques", warnings, {})
    state["agility_techniques"] = {t: {"xp": xp, "level": agility_technique_level(xp, caps.get(t, 99))}
                                   for t, xp in tech.items()}
    state["agility_food"] = (try_decode(d_food, get("Agility", "Food"), "Agility/Food", warnings) or {}).get("food")
    state["components_present"] = {e: {c: rd.end - rd.pos for c, rd in comps.items()} for e, comps in entities.items()}
    state["warnings"] = warnings
    return state


# ---- joining the save to the game data ----

def _objects_list(name):
    path = TABLES / "objects" / f"{name}.json"
    return json.loads(path.read_text(encoding="utf-8-sig")) if path.exists() else []


STAT_KEYS = ("Item", "Course", "Event", "Enemy", "Species", "Animal", "Master", "Constellation")


def condition_met(cond, state):
    """True/False for condition goals the save can answer, None otherwise."""
    kind, data = short(cond["Type"]), cond["Data"]
    if kind == "Level":
        skill = state["skills"].get(short(data["Component"]["Entity"]))
        return skill is not None and skill["level"] >= data["Level"]
    if kind == "ItemStatistic":
        stat = state["item_statistics"].get(data["Statistic"])
        return None if stat is None else stat.get(data["Item"], 0) >= data["Value"]
    if kind == "ValueStatistic":
        v = state.get("value_statistics", {}).get(data["Statistic"])
        return None if v is None else v >= data["Value"]
    if kind.endswith("Statistic") or kind == "HuntingAnimalSpecies":
        # per-object statistics: courses, events, animals, slayer masters, enemies, species
        if "object_statistics" not in state:
            return None
        key = next((data[k] for k in STAT_KEYS if data.get(k)), None)
        table = state["object_statistics"].get(data["Statistic"]) or state["item_statistics"].get(data["Statistic"]) or {}
        return (table.get(key, 0) if key else sum(table.values())) >= data.get("Value", 1)
    if kind in ("AstrologyMajorNodeUnlockCount", "AstrologyMinorNodeUnlockCount"):
        c = state.get("astrology", {}).get(data["Constellation"])
        nodes = (c or {}).get("major_nodes" if "Major" in kind else "minor_nodes", [])
        return len(nodes) >= data["Count"]
    if kind == "PetUnlocked":
        return data["Pet"] in state.get("pets", [])
    if kind == "AgilityTechniqueLevel":
        return (state.get("agility_techniques", {}).get(data["Technique"]) or {}).get("level", 1) >= data["Level"]
    if kind == "AgilityNumTechniquesLevel":
        n = sum(1 for t in state.get("agility_techniques", {}).values() if t["level"] >= data["Level"])
        return n >= data["NumTechniques"]
    if kind == "HuntingMaxLevelAnimals":
        n = sum(1 for e in state["hunting_journal"].values() if e["level"] >= data["Level"])
        return n >= data["Count"]
    if kind == "HuntingUniqueRareSpeciesFound":
        found = state.get("hunting_unique_rare_species_found")
        return None if found is None else found >= data["Count"]
    return None


def mastery_ranks(state, objects_dir, config):
    """Rank per skill. Condition goals the save cannot answer (ranching and
    slayer statistics) make points a min..max range instead of one number."""
    load = lambda t: {NS + o["Id"]: o for o in json.loads((objects_dir / f"{t}.json").read_text(encoding="utf-8"))}
    tasks, cats, ranks = load("MasteryGuildTask"), load("MasteryGuildCategory"), load("MasteryGuildRank")
    out = {}
    for skill, saved in state["mastery_tasks"].items():
        cfg = config.get(NS + skill, {})
        task_ids = [t["Id"] for t in cfg.get("MasteryGuildTasks", [])]
        rank_ids = sorted((r["Id"] for r in cfg.get("MasteryGuildRanks", [])),
                          key=lambda i: ranks[i]["PointPercentRequired"])
        if not task_ids or not rank_ids:
            continue
        total = lo = hi = 0
        for tid in task_ids:
            task = tasks[tid]
            pts = cats[task["Category"]]["BasePoints"]
            total += pts
            s = saved.get(tid)
            if s is None:
                continue
            trig_ok = s["trigger_met"] if task.get("TriggerGoal") else True
            if task.get("Goal"):
                if s["goal_complete"] and trig_ok:
                    lo += pts
                    hi += pts
            elif task.get("ConditionGoal"):
                met = condition_met(task["ConditionGoal"], state)
                if met and trig_ok:
                    lo += pts
                if met is not False and trig_ok:
                    hi += pts
            elif trig_ok:
                lo += pts
                hi += pts

        def rank_for(points):
            idx = 0
            for i in range(1, len(rank_ids)):
                if points < math.ceil(total * ranks[rank_ids[i]]["PointPercentRequired"]):
                    break
                idx = i
            return rank_ids[idx]

        out[skill] = {"points_min": lo, "points_max": hi, "total_points": total,
                      "rank_min": rank_for(lo), "rank_max": rank_for(hi),
                      "rank_pct": {r: ranks[r]["PointPercentRequired"] for r in rank_ids}}
    return out


def active_bonuses(state, bonuses, ranks_by_skill, objects_dir):
    by_source = defaultdict(list)
    for b in bonuses:
        by_source[(b["source_type"], b["source_id"])].append(b)
    active = []

    def add(src_type, src_id, via, path_prefix="", multiplier=1, note=None, extra_ctx=None):
        for b in by_source.get((src_type, src_id), []):
            if not b["path"].startswith(path_prefix):
                continue
            row = dict(b, via=via, multiplier=multiplier, effective_value=b["value"] * multiplier)
            if note:
                row["note"] = note
            if extra_ctx:
                row["context"] = {**row["context"], **extra_ctx}
            active.append(row)

    for skill, slots in state["toolbelts"].items():
        for slot, s in slots.items():
            add("Item", s["item"], f"toolbelt:{skill}/{slot}", "Capabilities.Toolbelt")
    for slot, s in state["equipment"].items():
        add("Item", s["item"], f"equipment:{slot}", "Capabilities.Equipment")
    if state.get("fishing_bait"):
        add("Item", state["fishing_bait"], "fishing_bait", "Capabilities.FishingBait")
    for pid, count in state["shop_purchases"].items():
        if count > 0:
            add("ShopPurchase", pid, "shop", note=f"purchased x{count}" if count > 1 else None)

    mg_bonuses = json.loads((objects_dir / "MasteryGuildBonus.json").read_text(encoding="utf-8"))
    for mb in mg_bonuses:
        for req in mb.get("Requirements") or []:
            if short(req["Type"]) != "MasteryGuildRank":
                continue
            skill = short(req["Data"]["Component"]["Entity"])
            info = ranks_by_skill.get(skill)
            if not info:
                continue
            need = info["rank_pct"].get(req["Data"]["Rank"])
            if need is None:
                continue
            if info["rank_pct"][info["rank_min"]] >= need:
                add("MasteryGuildBonus", NS + mb["Id"], f"mastery:{skill}")
            elif info["rank_pct"][info["rank_max"]] >= need:
                add("MasteryGuildBonus", NS + mb["Id"], f"mastery:{skill}",
                    note="UNCERTAIN: rank depends on condition-type tasks the save does not record")

    for pid in state.get("pets", []):
        add("Pet", pid, "pet")
    for cid, c in state["astrology"].items():
        for node in c["minor_nodes"] + c["major_nodes"]:
            add("AstrologyNode", node, f"astrology:{short(cid)}")
        if c["minor_nodes"]:
            add("AstrologyConstellation", cid, f"astrology:{short(cid)}", "PerMinorNodeBonuses",
                multiplier=len(c["minor_nodes"]))

    journal_bonuses = json.loads((objects_dir / "HuntingJournalBonus.json").read_text(encoding="utf-8"))
    for animal, e in state["hunting_journal"].items():
        for jb in journal_bonuses:
            if e["level"] >= jb["UnlockLevel"]:
                add("HuntingJournalBonus", NS + jb["Id"], f"journal:{short(animal)}", extra_ctx={"Animal": animal})

    # Totals: only unconditional, unscaled bonuses that apply to a whole component.
    totals = defaultdict(float)
    for b in active:
        scoped = b["context"] or b["condition"] or b["scaling"] or b["from_context"] or "note" in b
        if not scoped and b["entity"]:
            totals[(b["entity"], b["component"], b["stat"])] += b["effective_value"]
    summary = defaultdict(dict)
    for (entity, comp, stat), v in sorted(totals.items()):
        summary[entity][f"{comp}.{stat}"] = round(v, 6)
    scoped_count = sum(1 for b in active if b["context"] or b["condition"] or b["scaling"] or b["from_context"])
    return {"skill_wide_totals": summary, "scoped_or_conditional_count": scoped_count, "active": active}


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--save", help="a .sav file, or a folder to take the newest from (default: found automatically)")
    args = ap.parse_args()
    path = paths.find_save(args.save)

    state = parse_save(path)
    OUT.mkdir(parents=True, exist_ok=True)

    result = None
    if (TABLES / "bonus_sources.json").exists():
        bonuses = json.loads((TABLES / "bonus_sources.json").read_text(encoding="utf-8"))
        config = json.loads((TABLES / "entity_config.json").read_text(encoding="utf-8"))
        state["mastery_ranks"] = mastery_ranks(state, TABLES / "objects", config)
        result = active_bonuses(state, bonuses, state["mastery_ranks"], TABLES / "objects")
        (OUT / "active_bonuses.json").write_text(json.dumps(result, indent=1), encoding="utf-8")
    else:
        state["warnings"].append("data/tables missing - run build_tables.py to compute active bonuses")
    (OUT / "player_state.json").write_text(json.dumps(state, indent=1), encoding="utf-8")

    h = state["header"]
    print(f"{h['character']} ({h['gamemode']}), save v{h['version']}, total level {h['total_level']}, "
          f"GP {h['gp']:,}, doing: {h['active_entity']} / {h['active_action']}")
    print("levels:", ", ".join(f"{k} {v['level']}" for k, v in state["skills"].items()))
    print(f"bank: {len(state['bank'])} distinct items in {len(state['bank_tabs'])} tabs; "
          f"shop purchases owned: {sum(1 for c in state['shop_purchases'].values() if c > 0)}")
    for skill, slots in state["toolbelts"].items():
        if slots:
            print(f"toolbelt {skill}: " + ", ".join(f"{k}={short(v['item'])}" for k, v in slots.items()))
    if result:
        print(f"\nactive bonus rows: {len(result['active'])} "
              f"({result['scoped_or_conditional_count']} scoped to a recipe/condition)")
        for entity, stats in result["skill_wide_totals"].items():
            print(f"  {entity:14} " + ", ".join(f"{k}={v:+g}" for k, v in stats.items()))
    for w in state["warnings"]:
        print("WARNING:", w)
    print(f"\nwrote {(OUT / 'player_state.json').relative_to(ROOT)}")


if __name__ == "__main__":
    main()
