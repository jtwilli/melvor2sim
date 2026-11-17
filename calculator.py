"""Best XP/hour and GP/hour per skill for what this character can do right now.

Uses data/tables (build_tables.py) and data/save (read_save.py). Every action
you have unlocked gets your active bonuses applied with the game's own formulas
(docs/FORMULAS.md). Everything is costed from a standing start: the time
to gather or craft each input with your fastest available action is included,
and the XP those steps give in other skills is tracked. Combat is estimated
from the gear you have equipped.

    python -I calculator.py                  # writes data/calc/report.html
    python -I calculator.py --open           # ...and opens it in the browser
    python -I calculator.py --top 20         # rows per skill in the report
    python -I calculator.py --no-shop        # never buy inputs from the shop
    python -I calculator.py --passive-free   # count Farming/Ranching produce as free
"""
import argparse
import csv
import functools
import json
import math
import random
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))  # sibling modules, also under python -I
TABLES = ROOT / "data" / "tables"
SAVE = ROOT / "data" / "save"
OUT = ROOT / "data" / "calc"
NS = "MelvorBase:"
HOUR = 3_600_000
PASSIVE_TYPES = {"FarmingRecipe", "GrainMillRecipe", "RanchingAnimal"}
PER_ITEM_STATS = {"FlatProductQuantity", "PercentProductQuantity", "FlatOnHitItemChance", "PercentOnHitItemChance"}
CATEGORY_TABLE = {"Woodcutting": "WoodcuttingRecipeCategory", "Mining": "MiningRecipeCategory",
                  "Fishing": "FishingLocation", "Thieving": "ThievingLocation", "Hunting": "HuntingLocation",
                  "Farming": "FarmingCategory", "Ranching": None}
GLOBAL_STATS = {"GlobalPercentXpGained": "PercentXPGained", "GlobalFlatXpGained": "FlatXPGained",
                "GlobalPercentActionInterval": "PercentInterval"}


def short(i):
    return i[len(NS):] if isinstance(i, str) and i.startswith(NS) else i


cmb_short = short


def load(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def objects(name):
    return {NS + o["Id"]: o for o in load(TABLES / "objects" / f"{name}.json")}


# ---------------------------------------------------------------- requirements

QUEST_ACTIVE, QUEST_DONE = 1, 3      # status values in the save (a finished step or task is 2)
QUEST_GATE_TYPES = ("QuestStepActive", "QuestTaskGoalActive")
RECIPE_TABLES = ("ArtisanRecipe", "CookingRecipe", "FiremakingRecipe", "FishingRecipe", "MiningRecipe",
                 "WoodcuttingRecipe")


def quest_gates():
    """(action type, id) -> quest steps that show it. Quest actions have no other requirement: the game
    lists them while the step is running (they can be repeated then) and removes them when it ends."""
    out = {}
    for table in RECIPE_TABLES:
        path = TABLES / "objects" / f"{table}.json"
        if not path.exists():
            continue
        for oid, o in objects(table).items():
            gates = [d["Data"] for d in (o.get("DisplayRequirements") or []) if short(d["Type"]) in QUEST_GATE_TYPES]
            if gates:
                out[(table, oid)] = gates
    return out


class Requirements:
    def __init__(self, state, assume_tools=False):
        self.state = state
        self.assume_tools = assume_tools     # treat "equip X on the toolbelt" as met (for looking ahead)
        self.assume_unlocks = False          # treat every non-level requirement as met (GP planner)
        self._quest_gates = None
        # category ids repeat across skills (e.g. "Trees" in Woodcutting and Farming)
        self.category_reqs = {}
        for t in ("RecipeCategory", "FishingLocation", "ThievingLocation", "HuntingLocation",
                  "WoodcuttingRecipeCategory", "MiningRecipeCategory", "FarmingCategory"):
            self.category_reqs[t] = {oid: o["Requirements"] for oid, o in objects(t).items() if o.get("Requirements")}

    def level(self, skill):
        return self.state["skills"].get(skill, {}).get("level", 0)

    def check_raw(self, r):
        """One requirement in game-data form -> (met: True/False/None, label)."""
        data = r.get("Data") or {}
        entity = short((data.get("Component") or {}).get("Entity"))
        return self.check(short(r["Type"]), entity, data)

    def check(self, kind, entity, data):
        ok, label = self._check(kind, entity, data)
        # what-if mode (GP planner): anything besides a skill level counts as met, though it is still named
        if self.assume_unlocks and ok is not True and kind not in ("Level", "DemoLocked"):
            return True, label
        return ok, label

    def _check(self, kind, entity, data):
        if kind == "DemoLocked":
            return False, "demo locked"
        if kind == "Level":
            return self.level(entity) >= data["Level"], f"{entity} {data['Level']}"
        if kind == "ShopPurchase":
            pid = data["ShopPurchase"]
            return self.state["shop_purchases"].get(pid, 0) >= data.get("Amount", 1), f"buy {short(pid)}"
        if kind == "ToolbeltItemEquipped":
            slots = self.state["toolbelts"].get(entity, {})
            have = self.assume_tools or any(s["item"] == data["Item"] for s in slots.values())
            return have, f"equip {short(data['Item'])}"
        if kind.endswith("Statistic") or kind == "HuntingAnimalSpecies":
            return self._statistic(kind, data)
        if kind == "AstrologyMajorNodeUnlocked":
            node = data.get("MajorNode")
            have = any(node in c.get("major_nodes", []) for c in (self.state.get("astrology") or {}).values())
            return have, f"Astrology node {short(node)}"
        if kind == "MasteryGuildRank":
            return self._mastery_rank(entity, data.get("Rank"))
        if kind == "QuestComplete" and "quests" in self.state:
            q = self.state["quests"].get(data["Quest"])
            return q is not None and q["status"] == QUEST_DONE, f"finish quest {short(data['Quest'])}"
        return None, kind  # statistics (and quests in an old player_state.json): not decoded from the save

    STAT_KEYS = ("Item", "Course", "Event", "Enemy", "Species", "Animal", "Constellation")

    def _statistic(self, kind, data):
        """'have done X at least N times' requirements, read from the save's statistics."""
        if "object_statistics" not in self.state:
            return None, kind           # player_state.json from an older read_save.py
        stat = data.get("Statistic")
        key = next((data[k] for k in self.STAT_KEYS if data.get(k)), None)
        need = data.get("Value", 1)
        table = (self.state.get("item_statistics") or {}).get(stat)
        if table is None:
            table = (self.state.get("object_statistics") or {}).get(stat) or {}
        have = table.get(key, 0) if key else sum(table.values())
        label = f"{short(stat)}{' ' + short(key) if key else ''} {have:,.0f}/{need:,.0f}"
        return have >= need, label

    def _mastery_rank(self, skill, rank):
        """Mastery Guild rank: True / False, or None when the save's points leave it uncertain."""
        info = (self.state.get("mastery_ranks") or {}).get(skill)
        if not info or not rank:
            return None, f"{short(rank)} rank"
        order = list(info.get("rank_pct") or {})
        if rank not in order:
            return None, f"{short(rank)} rank"
        need = order.index(rank)
        lo = order.index(info["rank_min"]) if info.get("rank_min") in order else -1
        hi = order.index(info["rank_max"]) if info.get("rank_max") in order else -1
        if lo >= need:
            return True, f"{short(rank)} rank"
        return (False if hi < need else None), f"{short(rank)} rank"

    # quest-only actions ("Quests" category): offered only while a quest step is running
    def quest_gate(self, a):
        """The quest requirement that makes this quest action available now, or None."""
        if self._quest_gates is None:
            self._quest_gates = quest_gates()
        for g in a.get("quest_gates") or self._quest_gates.get((a["type"], a["id"]), ()):
            if self.quest_step_open(g):
                return g
        return None

    def quest_step_open(self, g):
        """g: QuestStepActive / QuestTaskGoalActive data {Quest, Step, Task}."""
        q = (self.state.get("quests") or {}).get(g["Quest"])
        if not q or q["status"] != QUEST_ACTIVE or not 0 <= g["Step"] < len(q["steps"]):
            return False
        step = q["steps"][g["Step"]]
        if step["status"] != QUEST_ACTIVE:
            return False
        if g.get("Task") is None:
            return True
        return 0 <= g["Task"] < len(step["tasks"]) and step["tasks"][g["Task"]]["status"] == QUEST_ACTIVE

    def action(self, a):
        """(available, reasons-not-met, unverified)"""
        missing, unknown = [], []
        if a["demo_locked"]:
            return False, ["demo locked"], []
        if a["level_req"] and self.level(a["skill"]) < a["level_req"]:
            missing.append(f"{a['skill']} {a['level_req']}")
        for r in a["other_requirements"]:
            ok, label = self.check(r["type"], r.get("entity"), r)
            (missing if ok is False else unknown if ok is None else []).append(label)
        table = CATEGORY_TABLE.get(a["skill"], "RecipeCategory")
        for r in self.category_reqs.get(table, {}).get(a["category"], []):
            ok, label = self.check_raw(r)
            (missing if ok is False else unknown if ok is None else []).append(label)
        return not missing and not unknown, missing, unknown


# ---------------------------------------------------------------- bonuses

class Bonuses:
    def __init__(self, active, include_uncertain):
        self.rows = defaultdict(list)
        self.skipped = defaultdict(int)
        for b in active:
            if b.get("condition") or b.get("scaling") or b.get("from_context"):
                self.skipped["conditional/scaling"] += 1
                continue
            if "UNCERTAIN" in (b.get("note") or "") and not include_uncertain:
                self.skipped["uncertain mastery rank"] += 1
                continue
            if b["entity"] is None and b["stat"] in GLOBAL_STATS:
                self.rows["*"].append(dict(b, stat=GLOBAL_STATS[b["stat"]]))
            elif b["entity"] and b["component"] in ("Action", "Experience", "Passive"):
                self.rows[b["entity"]].append(b)

    @staticmethod
    def _match(ctx, a, item):
        raw = a["raw_extra"]
        outs = {o["item"] for o in a["outputs"]}
        for k, v in ctx.items():
            if k == "Category":
                ok = v == a["category"]
            elif k in ("Recipe", "Animal"):
                ok = v == a["id"]
            elif k == "Subcategory":
                ok = v == a["subcategory"]
            elif k == "Location":
                ok = v in (a["category"], raw.get("Location"))
            elif k in ("Tool", "TrapType"):
                ok = v == raw.get(k)
            elif k == "Item":
                ok = v == item if item else v in outs
            else:
                ok = False
            if not ok:
                return False
        return True

    def total(self, a, stat, item=None, extra=()):
        rows = self.rows.get(a["skill"], []) + self.rows.get("*", []) + list(extra)
        per_item = stat in PER_ITEM_STATS
        t = 0.0
        for b in rows:
            if b["stat"] != stat:
                continue
            ctx = b.get("context") or {}
            if per_item and "Item" not in ctx and item is None:
                pass
            if self._match(ctx, a, item if per_item else None):
                t += b.get("effective_value", b["value"])
        return t


# ---------------------------------------------------------------- per-action rates

def expected_fires(health, skip, guarded=True):
    """Timer fires to clear `health` when each fire removes 1, or 2 with chance
    `skip` (guarded: only while more than 1 remains)."""
    e = [0.0] * (health + 1)
    for h in range(1, health + 1):
        if guarded and h == 1:
            e[h] = 1.0
        else:
            e[h] = 1 + skip * e[max(0, h - 2)] + (1 - skip) * e[h - 1]
    return e[health]


def tree_hits(health, double, weak, instant):
    weak_dmg = 3 + int(0.1 * health)
    e = [0.0] * (health + 1)
    for h in range(1, health + 1):
        if h == 1:
            e[h] = 1.0
            continue
        rest = 0.0
        for base, pb in ((weak_dmg, weak), (1, 1 - weak)):
            for mult, pd in ((2, double), (1, 1 - double)):
                rest += pb * pd * e[max(0, h - base * mult)]
        e[h] = 1 + (1 - instant) * rest
    return e[health]


@functools.lru_cache(maxsize=None)
def thieving_damage(damage, avoid, max_hp, heal, failures=20000):
    """Per failed pickpocket: food eaten and deaths, from a seeded run of `failures` failures.
    Each hit deals 1..damage (unless avoided). Auto-eat: at or below 25% of max HP (and above 0) eat up to full,
    max(1, floor((max - hp) / heal)) at once. A hit from above the threshold straight to 0 kills you; HP resets.
    heal 0 = no food: also returns how many failures it takes to die from full HP."""
    rng = random.Random(damage * 7919 + max_hp * 31 + heal)
    threshold = round(0.25 * max_hp)
    hp, food, deaths, first_death = max_hp, 0, 0, None
    for i in range(failures):
        if rng.random() < avoid:
            continue
        hp -= rng.randint(1, damage)
        if hp <= 0:
            deaths += 1
            first_death = first_death or i + 1
            hp = max_hp
        elif hp <= threshold and heal > 0:
            n = max(1, (max_hp - hp) // heal)
            food += n
            hp = min(max_hp, hp + n * heal)
    if heal <= 0:
        mean = (damage + 1) / 2 * (1 - avoid)
        return {"food": 0.0, "deaths": deaths / failures, "fails_to_death": max_hp / mean if mean else float("inf")}
    return {"food": food / failures, "deaths": deaths / failures, "fails_to_death": first_death or float("inf")}


def technique_xp_for(level):
    """Agility techniques' own XP curve: XP needed to reach `level`."""
    if level < 1:
        return 0.0
    r = 2 ** (1 / 3.25)
    return math.floor((level * level - level + 600 * ((2 ** (level / 3.25) - r) / (r - 1))) / 6)


def technique_level(xp, cap=40):
    level = 1
    while level < cap and xp >= technique_xp_for(level + 1):
        level += 1
    return level


@functools.lru_cache(maxsize=None)
def mixed_damage(hits, avoid, max_hp, heal, failures=20000):
    """Like thieving_damage, but each failure comes from one of several obstacles: hits = ((damage, weight), ...).
    Returns food eaten and deaths per failure, and failures until death with no food."""
    rng = random.Random(hash(hits) & 0xFFFF ^ max_hp * 31 + heal)
    total = sum(wt for _, wt in hits) or 1
    cum, acc = [], 0.0
    for d, wt in hits:
        acc += wt / total
        cum.append((acc, d))
    threshold = round(0.25 * max_hp)
    hp, food, deaths, first_death = max_hp, 0, 0, None
    for i in range(failures):
        if rng.random() < avoid:
            continue
        x = rng.random()
        d = next((dd for c, dd in cum if x <= c), cum[-1][1])
        hp -= rng.randint(1, d)
        if hp <= 0:
            deaths += 1
            first_death = first_death or i + 1
            hp = max_hp
        elif hp <= threshold and heal > 0:
            n = max(1, (max_hp - hp) // heal)
            food += n
            hp = min(max_hp, hp + n * heal)
    if heal <= 0:
        mean = sum((d + 1) / 2 * wt for d, wt in hits) / total * (1 - avoid)
        return {"food": 0.0, "deaths": deaths / failures, "fails_to_death": max_hp / mean if mean else float("inf")}
    return {"food": food / failures, "deaths": deaths / failures, "fails_to_death": first_death or float("inf")}



def drop_unsourced_supplies(rates, best):
    """Supplies an action uses up (combat food, ammo, runes; Thieving food) that nothing can provide are
    left out of its chain rather than dropping the action. Returns {action key: {item ids left out}}."""
    left_out = defaultdict(set)
    for key, r in rates.items():
        optional = set(r["inputs"]) if r["skill"] == "Combat" else set(r.get("supplies") or ())
        for inp in [i for i in r["inputs"] if i in optional and best[i][1] is None]:
            del r["inputs"][inp]
            left_out[key].add(inp)
    return left_out


class Rates:
    def __init__(self, state, bonuses, reqs):
        self.state, self.b, self.reqs = state, bonuses, reqs
        self.extra_stealth = 0      # what-if: stealth from gear or ranks you don't have yet
        self.items = objects("Item")
        self.thieving = objects("ThievingRecipe")
        self.hunting = objects("HuntingAnimal")
        self.hunt_loc = objects("HuntingLocation")
        self.mining = objects("MiningRecipe")
        tech = TABLES / "objects" / "AgilityTechnique.json"
        self.techniques = objects("AgilityTechnique") if tech.exists() else {}
        ev = TABLES / "astrology_events.json"
        self.astro_events = load(ev) if ev.exists() else {}
        cfg = load(TABLES / "entity_config.json")
        self.stone_items = [x["Id"] if isinstance(x, dict) else x for x in cfg[NS + "Mining"].get("StoneItems", [])]
        self.traps = defaultdict(list)
        for iid, it in self.items.items():
            cap = (it.get("Capabilities") or {}).get(NS + "HuntingTrap")
            if cap:
                self.traps[cap["TrapType"]].append(iid)

    def xp(self, a, base=None, extra=()):
        base = a["base_xp"] if base is None else base
        return (base + self.b.total(a, "FlatXPGained", extra=extra)) * (1 + self.b.total(a, "PercentXPGained", extra=extra))

    def compute(self, a):
        fn = getattr(self, "r_" + a["type"], None)
        return fn(a) if fn else None

    # artisan: Smithing, Crafting, Fletching, Runecrafting, Beastcrafting
    def r_ArtisanRecipe(self, a):
        t = lambda s, i=None: self.b.total(a, s, i)
        iv = math.floor(a["interval_ms"] * (1 + t("PercentInterval")) + t("FlatInterval"))
        steps = max(1, int((a["steps"] or 1) + t("FlatSteps")))
        fires = expected_fires(steps, min(1, t("SkipStepChance")))
        keep = min(1, t("PreservationChance"))
        dbl = min(1, t("DoublingChance"))
        outs = {}
        for o in a["outputs"]:
            q = max(1, int((o["qty"] + t("FlatProductQuantity", o["item"])) * (1 + t("PercentProductQuantity", o["item"]))))
            outs[o["item"]] = outs.get(o["item"], 0) + q * o["chance"] * (1 + dbl)
        ins = {i["item"]: i["qty"] * (1 - keep) for i in a["inputs"]}
        return dict(ms=iv * fires, xp=self.xp(a), inputs=ins, outputs=outs,
                    detail=f"{iv}ms x {fires:.2f} fires")

    def r_CookingRecipe(self, a):
        t = lambda s, i=None: self.b.total(a, s, i)
        iv = math.floor(a["interval_ms"] * (1 + t("PercentInterval")) + t("FlatInterval"))
        prof = max(0, self.reqs.level("Cooking") + t("FlatProficiency"))
        diff = max(0, a["raw_extra"]["Difficulty"] + t("FlatDifficulty"))
        win = max(0.0, min(1.0, (69 + prof) / (69 + diff)))
        keep, keep_fail = min(1, t("PreservationChance")), min(1, t("PreservationChanceOnFail"))
        dbl = min(1, t("DoublingChance"))
        consume = (1 - keep) * (win + (1 - win) * (1 - keep_fail))
        outs = {}
        for o in a["outputs"]:
            q = max(1, int((o["qty"] + t("FlatProductQuantity", o["item"])) * (1 + t("PercentProductQuantity", o["item"]))))
            outs[o["item"]] = q * o["chance"] * win * (1 + dbl)
        return dict(ms=iv, xp=self.xp(a) * win, inputs={i["item"]: i["qty"] * consume for i in a["inputs"]},
                    outputs=outs, detail=f"{iv}ms, {win:.0%} success")

    def r_FiremakingRecipe(self, a):
        t = lambda s, i=None: self.b.total(a, s, i)
        iv = math.floor((a["interval_ms"] + t("FlatInterval")) * (1 + t("PercentInterval")))
        dbl, keep = min(1, t("DoublingChance")), min(1, t("PreservationChance"))
        outs, cur = {}, {}
        for o in a["outputs"]:
            (cur if o.get("kind") == "currency" else outs)[o["item"]] = o["qty"] * o["chance"] * (1 + dbl)
        return dict(ms=iv, xp=self.xp(a), inputs={i["item"]: i["qty"] * (1 - keep) for i in a["inputs"]},
                    outputs=outs, currency=cur, detail=f"{iv}ms")

    def r_WoodcuttingRecipe(self, a):
        t = lambda s, i=None: self.b.total(a, s, i)
        raw = a["raw_extra"]
        iv = math.floor((a["interval_ms"] + t("FlatInterval")) * (1 + t("PercentInterval")))
        hp = math.ceil((raw["BaseMaxHealth"] + t("FlatMaxHealth")) * (1 + t("PercentMaxHealth")))
        hits = tree_hits(hp, min(1, t("DoubleDamageChance")), min(1, t("WeakSpotChance")), min(1, t("InstantCutChance")))
        dbl = min(1, t("DoublingChance"))
        outs = {}
        for o in a["outputs"]:
            if o["per"] == "cut" and o["chance"] == 1:
                q = o["qty"] + int(t("FlatProductQuantity", o["item"]))
            elif o["per"] == "hit":
                q = (o["chance"] + t("FlatOnHitItemChance", o["item"])) * (1 + t("PercentOnHitItemChance", o["item"])) * hits
            else:
                q = o["qty"] * o["chance"]
            outs[o["item"]] = outs.get(o["item"], 0) + q * (1 + dbl)
        return dict(ms=iv * hits, xp=self.xp(a), inputs={}, outputs=outs,
                    detail=f"{iv}ms x {hits:.2f} hits (hp {hp})")

    def r_MiningRecipe(self, a):
        t = lambda s, i=None: self.b.total(a, s, i)
        rec = self.mining[a["id"]]
        iv = math.floor((a["interval_ms"] + t("FlatInterval")) * (1 + t("PercentInterval")))
        p = max(1e-9, min(1.0, rec["BaseProductChance"] * (1 + t("PercentIncreaseRockBreakChance"))))
        start = t("FlatMinimumProspecting")
        k = max(1, math.ceil(round((0.9999 - start) / p, 9)))
        hits = (1 - (1 - p) ** k) / p
        dbl = min(1, t("DoublingChance"))
        shatter = min(1, t("ShatterChance"))
        ore = rec["Product"]["Quantity"] + int(t("FlatProductQuantity", rec["Product"]["Item"]))
        ore = (ore + min(1, t("AdditionalProductQuantityChance"))) * (1 + dbl) * (1 + 2 * shatter)
        outs = {rec["Product"]["Item"]: ore}
        for o in rec.get("RandomProductsPerHit") or []:
            c = (o["Chance"] + t("FlatOnHitItemChance", o["Item"])) * (1 + t("PercentOnHitItemChance", o["Item"]))
            outs[o["Item"]] = outs.get(o["Item"], 0) + c * o["Quantity"] * hits * (1 + dbl)
        stone_chance = t("StoneItemChance")
        for s in self.stone_items if stone_chance else []:
            outs[s] = outs.get(s, 0) + stone_chance * hits * (1 + dbl)
        if rec.get("BarItem") and t("BarItemChance"):
            outs[rec["BarItem"]] = outs.get(rec["BarItem"], 0) + t("BarItemChance") * (1 + dbl)
        # a toolbelt item the rock needs and uses up on every hit (the Starfall / Meteorite sightings logs)
        inputs = {it: hits for it in self.consumed_tool(a, "MiningMineRock")}
        return dict(ms=iv * hits, xp=self.xp(a) * (1 + shatter), inputs=inputs, outputs=outs,
                    detail=f"{iv}ms x {hits:.2f} hits (break {p:.0%})")

    def consumed_tool(self, a, trigger):
        """Toolbelt items this action requires that are used up each time `trigger` fires for it."""
        out = []
        for r in a["other_requirements"]:
            if r["type"] != "ToolbeltItemEquipped":
                continue
            tb = ((self.items.get(r["Item"]) or {}).get("Capabilities") or {}).get(NS + "Toolbelt") or {}
            for c in tb.get("ConsumesOn") or []:
                d = c.get("Data") or {}
                if short(c["Type"]) == trigger and d.get("Recipe") in (None, a["id"]) and not d.get("Chance"):
                    out.append(r["Item"])
        return out

    def r_FishingRecipe(self, a):
        t = lambda s, i=None: self.b.total(a, s, i)
        raw = a["raw_extra"]
        lo = math.floor((a["interval_min_ms"] + t("FlatMinInterval")) * (1 + t("PercentMinInterval") + t("PercentInterval")))
        hi = math.floor((a["interval_max_ms"] + t("FlatMaxInterval")) * (1 + t("PercentMaxInterval") + t("PercentInterval")))
        lo, hi = min(lo, hi), max(lo, hi)
        fish = max(0.0, min(1.0, (raw["FishChance"] + t("FlatFishChance")) * (1 + t("IncreasedFishChance"))))
        junk = (1 - fish) * (1 - min(1, t("JunkRerollChance")))
        dbl = min(1, t("DoublingChance"))
        outs, cur = {}, {}
        fish_rows = [o for o in a["outputs"] if o.get("table") == "fish"]
        total_w = sum(o["weight"] for o in fish_rows) or 1
        for o in fish_rows:
            outs[o["item"]] = (1 - junk) * o["weight"] / total_w * o["qty"] * (1 + dbl)
        for o in a["outputs"]:
            if o.get("table") == "junk":
                to_gp = t("JunkToGP")
                if to_gp > 0:
                    price = (self.items.get(o["item"], {}).get("SellsFor") or {}).get("Quantity", 0)
                    cur[NS + "GoldPieces"] = junk * (1 + dbl) * price * to_gp
                else:
                    outs[o["item"]] = junk * (1 + dbl)
            elif not o.get("table"):
                outs[o["item"]] = o["qty"] * min(1, o["chance"] * (1 + t("IncreasedSpecialProductChance"))) * (1 + dbl)
        return dict(ms=(lo + hi) / 2, xp=self.xp(a), inputs={}, outputs=outs, currency=cur,
                    detail=f"{lo}-{hi}ms, {fish:.0%} fish")

    def item_bonuses(self, item_id, skill):
        """Bonus rows an item gives to a skill (e.g. a food's +stealth while it is your Thieving food)."""
        if not hasattr(self, "_item_rows"):
            self._item_rows = defaultdict(list)
            for b in load(TABLES / "bonus_sources.json"):
                if b.get("source_type") == "Item" and b.get("entity") and b.get("component") in ("Action", "Experience"):
                    self._item_rows[(b["source_id"], b["entity"])].append(b)
        return self._item_rows.get((item_id, skill), [])

    def thieving_food(self):
        """The food Thieving eats: its own food slot, else your combat food. (item id, healing) or (None, 0)."""
        for fid in (self.state.get("thieving_food"), (self.state.get("food") or {}).get("food")):
            cap = ((self.items.get(fid) or {}).get("Capabilities") or {}).get(NS + "Food") if fid else None
            if cap:
                return fid, int(cap["BaseHealing"])
        return None, 0

    def r_ThievingRecipe(self, a):
        """Follows ThievingActionComponent: each attempt waits the interval and succeeds with
        min(1, (100 + stealth) / (100 + perception)). A failure deals 1..Damage (unless avoided) and stuns
        (unless avoided), and the stun waits its own interval before the next attempt. Your HP is shared with
        combat; Thieving auto-eats its own food, and dying loses a toolbelt item and stops the action.
        The food's own bonuses (e.g. Salmon Sushi +20 stealth) apply while it is your Thieving food."""
        food_id, _ = self.thieving_food()
        food_rows = self.item_bonuses(food_id, "Thieving") if food_id else []
        t = lambda s, i=None: self.b.total(a, s, i, extra=food_rows)
        rec = self.thieving[a["id"]]
        iv = math.floor((rec["BaseInterval"] + t("FlatInterval")) * (1 + t("PercentInterval")))
        stun = math.floor((2000 + t("FlatStunInterval")) * (1 + t("PercentStunInterval")))
        stealth = max(0, math.floor((self.reqs.level("Thieving") + t("FlatStealth") + self.extra_stealth)
                                    * (1 + t("PercentStealth"))))
        perception = max(0, math.floor((rec["Perception"] + t("FlatPerception")) * (1 + t("PercentPerception"))))
        win = min(1.0, (100 + stealth) / (100 + perception))
        avoid = min(1, t("StunAvoidanceChance"))
        per_success = iv / win + (1 - win) / win * (1 - avoid) * stun
        stages = max(1, rec["Stages"] + int(t("FlatStages")))
        successes = expected_fires(stages, min(1, t("SkipStageChance")), guarded=False)
        failures = (1 - win) / win * successes          # failed attempts per completion
        fortune = t("FlatFortune") + (t("FlatFortuneUnderSuccess") if win < 1 else 0)
        dbl = min(1, t("DoublingChance"))
        outs, cur = {}, {}

        def table(rows, chance, scale):
            tw = sum(r["Weight"] for r in rows) or 1
            for r in rows:
                lo, hi = int(r["MinQuantity"]), int(r["MaxQuantity"])
                # quantity rolled uniformly, then floor(q x (1 + fortune / 100)), then maybe doubled
                q = sum(math.floor(n * (1 + fortune / 100)) for n in range(lo, hi + 1)) / max(1, hi - lo + 1)
                outs[r["Item"]] = outs.get(r["Item"], 0) + chance * r["Weight"] / tw * q * (1 + dbl) * scale

        c = rec.get("CurrencyReward")
        if c:
            ch = min(1, rec["BaseCurrencyLootChance"] * (1 + t("PercentCurrencyLootChance")))
            pct, flat = t("PercentCurrencyReward"), t("FlatCurrencyReward")
            lo = max(0, math.floor((c["MinQuantity"] + flat) * (1 + pct)))
            hi = max(0, math.floor((c["MaxQuantity"] + flat) * (1 + pct)))
            if hi > 0:
                cur[c["Currency"]] = ch * (lo + hi) / 2 * successes
        table(rec["CommonDropTable"], min(1, rec["BaseCommonLootChance"] * (1 + t("PercentCommonLootChance"))), successes)
        table(rec["RareDropTable"], min(1, rec["BaseRareLootChance"] * (1 + t("PercentRareLootChance"))), successes)
        table(rec["CompletionDropTable"], 1.0, 1)

        # damage taken and the food it costs
        inputs, notes, risk = {}, [], {}
        if failures > 0 and rec["Damage"] > 0:
            food, heal = self.thieving_food()
            max_hp = max(10, 10 * self.reqs.level("Hitpoints"))
            per_fail = thieving_damage(int(rec["Damage"]), round(min(1, t("DamageAvoidanceChance")), 4), max_hp, heal)
            eaten = per_fail["food"] * failures
            deaths = per_fail["deaths"] * failures
            if food and eaten > 0:
                inputs[food] = eaten
            hrs = per_success * successes / 3_600_000
            risk = {"food": food, "food_per_completion": eaten, "deaths_per_hr": deaths / hrs if hrs else 0.0,
                    "damage": rec["Damage"], "max_hp": max_hp}
            if not food:
                notes.append(f"no Thieving food: you die after about {per_fail['fails_to_death']:.0f} failures")
            elif deaths > 0:
                notes.append(f"a {rec['Damage']}-damage hit can kill you from above the auto-eat point "
                             f"(about every {1 / risk['deaths_per_hr']:.1f} h; you lose a toolbelt item)")
        if t("SameAreaPickpocketChance") > 0:
            notes.append("extra pickpockets of nearby NPCs not counted")
        return dict(ms=per_success * successes, xp=self.xp(a), inputs=inputs, outputs=outs, currency=cur,
                    detail=f"{iv}ms, {win:.0%} success, {stages} stage(s)", note="; ".join(notes) or None,
                    supplies=set(inputs), thieving=dict(success=win, **risk))

    def r_HerbloreRecipe(self, a):
        """A brew prepares, heats and stirs once, then bottles 10 times. Each bottling uses one set of ingredients
        (unless preserved), gives the XP and the potion (maybe doubled). Rates are per bottling."""
        t = lambda s, i=None: self.b.total(a, s, i)
        raw = a["raw_extra"]

        def phase(name):
            pct = t(f"Percent{name}Interval") + t("PercentInterval")
            return math.floor(raw[f"Base{name}Interval"] * (1 + pct) + t(f"Flat{name}Interval") + t("FlatInterval"))

        prep, heat, stir, bottle = (phase(n) for n in ("Preparing", "Heating", "Stirring", "Bottling"))
        ms = (prep + heat + stir) / BOTTLES_PER_BREW + bottle
        keep, dbl, cost = min(1, t("PreservationChance")), min(1, t("DoublingChance")), t("PercentIngredientCost")
        ins = {i["item"]: max(1, int(i["qty"] * (1 + cost))) * (1 - keep) for i in a["inputs"]}
        outs = {}
        for o in a["outputs"]:
            q = int(max(1, (o["qty"] + t("FlatProductQuantity", o["item"])) * (1 + t("PercentProductQuantity", o["item"]))))
            outs[o["item"]] = outs.get(o["item"], 0) + q * o["chance"] * (1 + dbl)
        return dict(ms=ms, xp=self.xp(a), inputs=ins, outputs=outs,
                    detail=f"{prep + heat + stir}ms brewing per {BOTTLES_PER_BREW} bottles + {bottle}ms each")

    def agility_food(self):
        """The food Agility eats: its own food slot, else your combat food. (item id, healing) or (None, 0)."""
        for fid in (self.state.get("agility_food"), (self.state.get("food") or {}).get("food")):
            cap = ((self.items.get(fid) or {}).get("Capabilities") or {}).get(NS + "Food") if fid else None
            if cap:
                return fid, int(cap["BaseHealing"])
        return None, 0

    def r_AgilityCoursePath(self, a):
        """One lap of a course path: each obstacle waits its interval, then succeeds with
        0.95^((difficulty - technique level + 1) / 2) (1 once your technique level is above it; traps 80%, max 95%).
        A failure deals 1..damage (unless avoided) and sends you back to the start (unless the repeat chance
        lets you retry that obstacle); a restart pays the entry cost again. XP and rewards come per lap."""
        t = lambda s, i=None: self.b.total(a, s, i)
        raw = a["raw_extra"]
        techs = self.state.get("agility_techniques") or {}
        levels = {tid: (techs.get(tid) or {}).get("level", 1) for tid in self.techniques}
        repeat, skip = t("RepeatFailedObstacleChance"), t("SkipObstacleChance")
        avoid, dbl = min(1, t("AvoidDamageChance")), min(1, t("DoublingChance"))
        obs, full_at, reward_qty = [], {}, []
        for o in raw["obstacles"]:
            diff = max(0.0, o["difficulty"] + t("FlatObstacleDifficulty"))
            # every technique at or above this obstacle's difficulty adds its bonus to this obstacle
            met = defaultdict(float)
            for tid, tech in self.techniques.items():
                if levels[tid] >= diff:
                    for b in tech.get("DifficultyMetBonuses") or []:
                        met[b["Stat"]["Name"]] += b["Value"]
            iv = math.floor((o["interval"] + t("FlatInterval")) * (1 + t("PercentInterval") + met["PercentInterval"]))
            if o["trap"]:
                p = max(0.0, min(0.95, 0.8 + t("FlatTrapSuccessChance") + met["FlatTrapSuccessChance"]))
            elif not o["technique"]:
                p = 0.6
            else:
                n = max(0.0, diff - levels.get(o["technique"], 1) + 1)
                p = 0.95 ** (n / 2) if n > 0 else 1.0
                need = math.floor(diff) + 1                  # the technique level that makes it certain
                full_at[o["technique"]] = max(full_at.get(o["technique"], 0), need)
            dmg = max(1, math.floor(o["damage"] * (1 + t("PercentDamageTaken"))))
            obs.append((iv, p, dmg, min(1, repeat + met["RepeatFailedObstacleChance"]),
                        min(1, skip + met["SkipObstacleChance"])))
            reward_qty.append(met["FlatObstacleRewardQuantity"])

        def per_lap(cost, only=None):
            """Expected total of a per-attempt cost over one lap, failures and restarts included.
            Obstacle k: X_k = (1 - s_k)(c_k + (1 - p_k)(1 - r_k) E_{k-1}) / (1 - (1 - s_k)(1 - p_k))."""
            total = 0.0
            for k, (iv, p, dmg, r_k, s_k) in enumerate(obs):
                s_k = s_k if k > 0 else 0.0
                c = cost(iv, p, dmg) if only is None or only == k else 0.0
                x = (1 - s_k) * (c + (1 - p) * (1 - r_k) * total) / (1 - (1 - s_k) * (1 - p))
                total += x
            return total

        ms = per_lap(lambda iv, p, d: iv)
        fails_at = [per_lap(lambda iv, p, d: 1 - p, only=k) for k in range(len(obs))]
        fails = sum(fails_at)
        success = math.prod(o[1] for o in obs)
        restarts = sum(f * (1 - o[3]) for f, o in zip(fails_at, obs))
        keep_entry = min(1, t("EntryCostPreservationChance"))
        inputs = {i["item"]: i["qty"] * (1 + restarts) * (1 - keep_entry)
                  for i in a["inputs"] if i.get("kind") != "currency"}
        outs, cur = {}, {}
        pct_reward = t("PercentRewardQuantity")
        for o in a["outputs"]:
            if o.get("kind") == "currency":
                cur[o["item"]] = cur.get(o["item"], 0) + max(1, int(o["qty"] * (1 + pct_reward)))
            elif o["chance"] >= 1:
                outs[o["item"]] = outs.get(o["item"], 0) + max(1, int(o["qty"] * (1 + pct_reward)))
            else:      # the random obstacle reward, collected when you pass that obstacle
                ch = max(0.0, min(1.0, (o["chance"] + t("FlatObstacleRewardChance"))
                                  * (1 + t("IncreasedObstacleRewardChance"))))
                q = sum(max(1, int((o["qty"] + t("FlatObstacleRewardQuantity") + extra)
                                   * (1 + t("PercentObstacleRewardQuantity")))) for extra in reward_qty) / len(reward_qty)
                outs[o["item"]] = outs.get(o["item"], 0) + ch * q * (1 + dbl)
        notes, risk = [], {}
        if fails > 1e-9:
            food, heal = self.agility_food()
            max_hp = max(10, 10 * self.reqs.level("Hitpoints"))
            mix = tuple(sorted((int(o[2]), round(f / fails, 3)) for f, o in zip(fails_at, obs) if f > 1e-9))
            per_fail = mixed_damage(mix, round(avoid, 4), max_hp, heal)
            eaten, deaths = per_fail["food"] * fails, per_fail["deaths"] * fails
            if food and eaten > 0:
                inputs[food] = inputs.get(food, 0) + eaten
            hrs = ms / 3_600_000
            risk = {"food": food, "food_per_completion": eaten, "deaths_per_hr": deaths / hrs if hrs else 0.0,
                    "damage": max(o[2] for o in obs), "max_hp": max_hp}
            if not food:
                notes.append(f"no food: you die after about {per_fail['fails_to_death']:.0f} failed obstacles")
            elif deaths > 0:
                notes.append(f"failures can kill you (about every {1 / risk['deaths_per_hr']:.1f} h; "
                             f"you lose a toolbelt item)")
        if raw.get("timed_rewards"):
            notes.append("rewards for fast laps not counted")
        gaps = {k: v for k, v in full_at.items() if levels.get(k, 1) < v}
        if gaps:
            def need(k, v):
                cap = (self.techniques.get(k) or {}).get("LevelCap") or 40
                if v <= cap:
                    return f"{cmb_short(k)} {v}"
                return f"{cmb_short(k)} {cap} (cap: {0.95 ** ((v - cap) / 2):.0%} per obstacle at best)"
            notes.insert(0, "certain at " + ", ".join(need(k, v) for k, v in sorted(gaps.items())))
        tech_xp = {}
        if raw.get("gives_technique_xp") and raw.get("technique_xp"):
            counts = defaultdict(int)
            for o in raw["obstacles"]:
                counts[o["technique"]] += 1
            primary = max(counts, key=counts.get)
            for tid in {o["technique"] for o in raw["obstacles"] if o["technique"]}:
                tech_xp[tid] = max(1.0, raw["technique_xp"] * (1 if tid == primary else 0.25))
        return dict(ms=ms, xp=self.xp(a), inputs=inputs, outputs=outs, currency=cur, technique_xp=tech_xp,
                    detail=f"{len(obs)} obstacles, {success:.0%} chance of a clean lap, {fails:.2f} fails per lap",
                    note="; ".join(notes) or None, supplies={i for i in inputs if i == risk.get("food")},
                    agility=dict(success=success, fails=fails, **risk))

    def r_AstrologyConstellation(self, a):
        """Each study waits the interval, gives XP and knowledge, and rolls every event: global ones and the
        constellation's own. Item events give base + 0..(Astrology level x scaling) items, maybe doubled."""
        t = lambda s, i=None: self.b.total(a, s, i)
        iv = math.floor((a["interval_ms"] + t("FlatInterval")) * (1 + t("PercentInterval")))
        level = self.reqs.level("Astrology")
        outs = {}

        def visit(eid, chance, depth):
            e = self.astro_events.get(eid)
            if not e or depth > 4 or chance <= 0:
                return
            if e["kind"] == "AstrologyItemEvent":
                q = e["BaseQuantity"] + level * e["LevelScalingQuantity"] / 2
                outs[e["Item"]] = outs.get(e["Item"], 0) + chance * q * (1 + min(1, t("ItemDoublingChance")))
            elif e["kind"] == "AstrologyTableEvent":
                tw = sum(x["Weight"] for x in e.get("Events") or []) or 1
                for x in e.get("Events") or []:
                    visit(x["Event"], chance * x["Weight"] / tw, depth + 1)

        for eid in a["raw_extra"]["events"]:
            e = self.astro_events.get(eid)
            if e:
                visit(eid, min(1.0, max(0.0, e["Chance"] * (1 + t("PercentEventChance")) + t("FlatEventChance"))), 0)
        knowledge = max(0.0, (a["raw_extra"]["knowledge"] + t("FlatKnowledge")) * (1 + t("PercentKnowledge")))
        return dict(ms=iv, xp=self.xp(a), inputs={}, outputs=outs,
                    detail=f"{iv}ms, {knowledge:g} knowledge per study")

    def r_HuntingAnimal(self, a):
        rec = self.hunting[a["id"]]
        trapless_ok = rec.get("EnableTrapless") and all(
            self.reqs.check_raw(r)[0] for r in rec.get("TraplessRequirements") or [])
        extra = []
        if trapless_ok:
            for b in rec.get("TraplessBonuses") or []:
                ctx = {k: v for k, v in (b.get("LocalContext") or {}).items() if k != "Component" and v is not None}
                extra.append({"stat": b["Stat"]["Name"], "value": b["Value"], "context": ctx})
        t = lambda s, i=None: self.b.total(a, s, i, extra=extra)

        def rng(lo, hi, flat, flat_lo, flat_hi, pct, pct_lo, pct_hi):
            x = math.floor((lo + flat + flat_lo) * (1 + pct + pct_lo))
            y = math.floor((hi + flat + flat_hi) * (1 + pct + pct_hi))
            return (min(x, y) + max(x, y)) / 2

        track_iv = rng(rec["BaseMinTrackInterval"], rec["BaseMaxTrackInterval"], t("FlatTrackingInterval"),
                       t("FlatMinTrackingInterval"), t("FlatMaxTrackingInterval"), t("PercentTrackingInterval"),
                       t("PercentMinTrackingInterval"), t("PercentMaxTrackingInterval"))
        trap_iv = rng(rec["BaseMinTrapInterval"], rec["BaseMaxTrapInterval"], t("FlatTrappingInterval"),
                      t("FlatMinTrappingInterval"), t("FlatMaxTrappingInterval"), t("PercentTrappingInterval"),
                      t("PercentMinTrappingInterval"), t("PercentMaxTrappingInterval"))
        loc = self.hunt_loc.get(rec["Location"], {})
        survey_iv = math.floor((loc.get("BaseSurveyInterval", 0) + t("FlatSurveyInterval")) * (1 + t("PercentSurveyInterval")))
        base_track = rec["BaseTrackSuccessChance"]
        track_win = max(0.0, min(1.0, base_track + t("FlatTrackingSuccessChance") + base_track * t("IncreasedTrackingSuccessChance")))
        base_trap = rec["BaseTrapSuccessChance"]
        trap_win = max(0.01, min(1.0, base_trap + t("FlatTrappingSuccessChance") + base_trap * t("IncreasedTrappingSuccessChance")))
        smin = max(1, int(rec["BaseMinTrackingSteps"] + t("FlatTrackingSteps") + t("FlatMinTrackingSteps")))
        smax = max(1, int(rec["BaseMaxTrackingSteps"] + t("FlatTrackingSteps") + t("FlatMaxTrackingSteps")))
        smin, smax = min(smin, smax), max(smin, smax)
        skip = min(1, t("SkipTrackingStepChance"))
        track_actions = sum(expected_fires(n, skip) for n in range(smin, smax + 1)) / (smax - smin + 1)
        surveys = 1 + (1 - track_win) + (1 - track_win) ** 2
        attempts = 1 / trap_win
        retrack = (attempts - 1) * (1 - min(1, t("TrappingReattemptChance"))) * expected_fires(smin, skip)
        ms = surveys * survey_iv + (track_actions + retrack) * track_iv + attempts * trap_iv
        dbl = min(1, t("DoublingChance"))
        outs = {}
        for p in rec["Products"] + rec["SurveyingProducts"]:
            outs[p["Item"]] = outs.get(p["Item"], 0) + p["Quantity"] * p["Chance"] * (1 + dbl)
        ins = {}
        if not trapless_ok:
            traps = self.traps.get(rec["TrapType"]) or []
            if traps:
                basic = next((x for x in traps if "Basic" in x), traps[0])
                ins[basic] = attempts * (1 - min(1, t("PreservationChance")))
        return dict(ms=ms, xp=self.xp(a, extra=extra), inputs=ins, outputs=outs,
                    detail=f"{trap_win:.0%} trap, {'trapless' if trapless_ok else 'trap'}",
                    note="first catch of an undiscovered animal takes 4x max steps" if not
                    self.state["hunting_journal"].get(a["id"], {}).get("discovered") else None)


# ---------------------------------------------------------------- chains

def solve_chains(rates, sources, max_iter=60):
    """Fastest active time to obtain one unit of each item, allowing multi-step
    chains. sources: item -> ('shop'|'passive', gp per unit)."""
    INF = float("inf")
    best = defaultdict(lambda: (INF, None))
    for item, (kind, gp) in sources.items():
        best[item] = (0.0, kind)
    producers = defaultdict(list)
    for key, r in rates.items():
        for item, q in r["outputs"].items():
            if q > 1e-12:
                producers[item].append((key, q))
    supplies = {key: _supplies(r) for key, r in rates.items()}

    def cost_of(key, skip_supplies):
        r = rates[key]
        cost = r["ms"]
        for inp, n in r["inputs"].items():
            c = best[inp][0]
            if c == INF and skip_supplies and inp in supplies[key]:
                continue            # a supply that can't be priced yet: leave it out for now
            cost += n * c
            if cost == INF:
                break
        return cost

    # Pass 1 prices supplies only when they already have a price, which breaks loops such as
    # "Thieving needs food -> the food needs an ingredient from Thieving". Pass 2 then prices
    # everything, so the loop's cost is included once each part has a finite price.
    for skip in (True, False):
        for _ in range(max_iter):
            changed = False
            for item, plist in producers.items():
                for key, q in plist:
                    unit = cost_of(key, skip) / q
                    if unit < best[item][0] - 1e-9 or (not skip and best[item][1] == key and abs(unit - best[item][0]) > 1e-6):
                        best[item] = (unit, key)
                        changed = True
            if not changed:
                break
    return best


def _supplies(r):
    """Inputs an action uses up without them being an ingredient: combat supplies, Thieving food."""
    return set(r["inputs"]) if r.get("skill") == "Combat" else set(r.get("supplies") or ())


def expand(key, rates, best):
    """Everything needed for ONE completion of `key`, from a standing start.
    Returns (steps: action key -> completions, in the order you would do them;
    shop: item -> units bought; passive: item -> units; missing item or None)."""
    steps, shop, passive = {}, defaultdict(float), defaultdict(float)

    def visit(k, mult, stack):
        for inp, n in rates[k]["inputs"].items():
            if n <= 0:
                continue
            src = best[inp][1]
            if src == "shop":
                shop[inp] += n * mult
            elif src in ("passive", "bank"):
                passive[inp] += n * mult
            elif src is None:
                return inp
            elif (src in stack or len(stack) > 12) and n * mult < 1e-4:
                continue        # a loop (e.g. Thieving food made from Thieving loot) whose remainder is negligible
            elif len(stack) > 40:
                return inp      # a loop that does not shrink: it can't be costed
            else:
                missing = visit(src, n * mult / rates[src]["outputs"][inp], stack + (k,))
                if missing:
                    return missing
        steps[k] = steps.get(k, 0.0) + mult
        return None

    missing = visit(key, 1.0, ())
    return (None, None, None, missing) if missing else (steps, shop, passive, None)


# ---------------------------------------------------------------- shared pipeline

BOTTLES_PER_BREW = 10   # Herblore: bottles per brew (fixed in the game)
CONVERTER_MS = 1        # instant steps (opening an item, the Grain Mill) still need a non-zero time


def converters(calc, reqs, actions):
    """Steps that take no active time but turn one item into another, so chains can use them:
    opening an item (one roll of its drop table plus its fixed drops; e.g. Seaweed Clump -> 100 Seaweed), and
    the Grain Mill once bought (e.g. 10 Wheat -> 20 Grain; it runs on its own while you do other things).
    They are marked converter=True and never ranked themselves."""
    out = {}
    for iid, it in calc.items.items():
        cap = (it.get("Capabilities") or {}).get(NS + "Openable")
        if not cap or not all(reqs.check_raw(r)[0] for r in (cap.get("Requirements") or [])):
            continue
        outs = {}
        for d in cap.get("AlwaysDrops") or []:
            outs[d["Item"]] = outs.get(d["Item"], 0) + d["Quantity"]
        table = cap.get("DropTable") or []
        tw = sum(d["Weight"] for d in table) or 1
        for d in table:
            outs[d["Item"]] = outs.get(d["Item"], 0) + d["Weight"] / tw * (d["MinQuantity"] + d["MaxQuantity"]) / 2
        name = (it.get("DisplayName") or {}).get("Singular") if isinstance(it.get("DisplayName"), dict) else it.get("DisplayName")
        out[f"Open/{short(iid)}"] = dict(key=f"Open/{short(iid)}", skill="Bank", name=f"Open {name or short(iid)}",
                                         level_req=1, ms=CONVERTER_MS, xp=0.0, xp_split={}, inputs={iid: 1.0},
                                         outputs=outs, detail="opening takes no time", converter=True)
    for a in actions:
        if a["type"] == "GrainMillRecipe" and not a["demo_locked"] and reqs.action(a)[0]:
            out[a["key"]] = dict(key=a["key"], skill=a["skill"], name=f"Grain Mill: {a['name']}", level_req=a["level_req"] or 1,
                                 ms=CONVERTER_MS, xp=0.0, xp_split={},
                                 inputs={i["item"]: i["qty"] for i in a["inputs"]},
                                 outputs={o["item"]: o["qty"] * o["chance"] for o in a["outputs"]},
                                 detail="the Grain Mill runs on its own", converter=True)
    return out


def action_rates(actions, state, bonuses, reqs, calc=None, quests=False):
    """Rates for every non-passive action this state has unlocked, plus instant converters (see converters()).
    quests=True also includes quest actions whose quest step is running now (marked with "quest").
    Returns (rates, passive_rows, blocked: skill -> [(action, missing, unknown)])."""
    calc = calc or Rates(state, bonuses, reqs)
    rates, passive_rows, blocked = {}, [], defaultdict(list)
    rates.update(converters(calc, reqs, actions))
    for a in actions:
        gate = None
        if (a["category"] and "Quest" in a["category"]) or a.get("quest_gates"):
            # temporary: only while its quest step runs, so it is left out of levelling plans
            gate = reqs.quest_gate(a) if quests and not a["demo_locked"] else None
            if gate is None:
                continue
        ok, missing, unknown = reqs.action(a)
        if not ok:
            if not a["demo_locked"]:
                blocked[a["skill"]].append((a, missing, unknown))
            continue
        if a["type"] in PASSIVE_TYPES:
            if a["type"] != "GrainMillRecipe":
                passive_rows.append(a)
            continue
        r = calc.compute(a)
        if r is None or not r["ms"]:
            continue
        r.update(key=a["key"], skill=a["skill"], name=a["name"], level_req=a["level_req"] or 1,
                 xp_split={a["skill"]: r["xp"]})
        if gate:
            r["quest"] = gate
        rates[a["key"]] = r
    return rates, passive_rows, blocked


def zero_time_sources(reqs, gold, no_shop=False, passive_rows=(), passive_free=False, rates=None, stock=None):
    """Items obtainable with no active time: shop purchases for gold, and passive produce (Farming, Ranching).
    Passive produce counts for every item with passive_free; otherwise (given `rates`) only for items no active
    action makes, such as herbs, so chains that need them can still be costed."""
    sources = {}
    made = {i for r in (rates or {}).values() for i, q in r["outputs"].items() if q > 1e-12}
    for a in passive_rows:
        for o in a["outputs"]:
            if passive_free or (rates is not None and o["item"] not in made):
                sources[o["item"]] = ("passive", 0.0)
    # what is already in your bank (stock): no time to get, and chain_row charges its sell value instead
    for item, qty in (stock or {}).items():
        if qty > 0:
            sources[item] = ("bank", 0.0)
    if not no_shop:
        for s in load(TABLES / "shop_purchases.json"):
            gp = [c for c in s["costs"]["Currencies"] if c["Currency"] == gold]
            if s["buy_limit"] != 0 or s["costs"]["Items"] or len(gp) != len(s["costs"]["Currencies"]) or not gp:
                continue
            if not all(reqs.check_raw(r)[0] for r in s["requirements"]):
                continue
            for p in s["provides_items"]:
                unit = gp[0]["Quantity"] / p["Quantity"]
                if p["Item"] not in sources or sources[p["Item"]][1] > unit:
                    sources[p["Item"]] = ("shop", unit)
    return sources


def chain_row(key, rates, best, sources, item_name, price, gold, state=None, uncosted=()):
    """One action costed from a standing start, per hour. Returns (row, None) or (None, missing item)."""
    r = rates[key]
    steps, shop, passive, missing = expand(key, rates, best)
    if missing:
        return None, missing
    total_ms = sum(c * rates[k]["ms"] for k, c in steps.items())
    f = HOUR / total_ms
    xp_by_skill, net, coins, other_cur = defaultdict(float), defaultdict(float), 0.0, defaultdict(float)
    step_rows = []
    for k, c in steps.items():
        sr = rates[k]
        for sk, v in sr["xp_split"].items():
            xp_by_skill[sk] += v * c * f
        for it, q in sr["outputs"].items():
            net[it] += q * c
        for it, n in sr["inputs"].items():
            net[it] -= n * c
        for cur, q in (sr.get("currency") or {}).items():
            if cur == gold:
                coins += q * c
            else:
                other_cur[short(cur)] += q * c * f
        makes = sorted(((q * c * f, item_name(it)) for it, q in sr["outputs"].items() if q * c * f >= 0.05),
                       reverse=True)
        step_rows.append(dict(
            key=k, name=sr["name"], skill=sr["skill"], final=k == key, per_hr=c * f,
            share=c * sr["ms"] / total_ms, secs_each=sr["ms"] / 1000,
            xp_hr={sk: v * c * f for sk, v in sr["xp_split"].items()},
            uses=[(n * c * f, item_name(it)) for it, n in sr["inputs"].items() if n > 0],
            makes=makes[:4], detail=sr["detail"], note=sr.get("note")))
    shop_rows, shop_cost = [], 0.0
    for it, n in shop.items():
        net[it] += n
        shop_cost += n * sources[it][1]
        shop_rows.append(dict(item=item_name(it), per_hr=n * f, gp_hr=n * sources[it][1] * f))
    stock_rows, stock_value, stock_hours = [], 0.0, None
    for it, n in passive.items():
        net[it] += n
        if sources.get(it, ("",))[0] == "bank":
            have = (state or {}).get("bank", {}).get(it, 0)
            lasts = have / (n * f) if n > 0 else None
            stock_rows.append(dict(item=item_name(it), per_hr=n * f, have=have, hours=lasts))
            stock_value += n * price(it)            # what you could have sold it for instead
            if lasts is not None:
                stock_hours = lasts if stock_hours is None else min(stock_hours, lasts)
    sold = sorted(((q * price(it) * f, q * f, item_name(it)) for it, q in net.items()
                   if q > 1e-9 and price(it) > 0), reverse=True)
    sell_value = sum(v for v, _, _ in sold) - stock_value * f
    own_xp = (sum(r["xp_split"].values()) * steps[key] * f if r["skill"] == "Combat"
              else xp_by_skill.get(r["skill"], 0.0))
    return dict(
        skill=r["skill"], name=r["name"], key=key, level_req=r["level_req"],
        xp_hr=own_xp, xp_total_hr=sum(xp_by_skill.values()), xp_by_skill=dict(xp_by_skill),
        gp_hr=sell_value + coins * f - shop_cost * f, sell_hr=sell_value, coins_hr=coins * f,
        shop_hr=shop_cost * f, other_currencies=dict(other_cur),
        sold=[dict(gp_hr=v, per_hr=q, item=n) for v, q, n in sold],
        steps=step_rows, shop=shop_rows, stock=stock_rows, stock_hours=stock_hours,
        passive=[dict(id=it, item=item_name(it), per_hr=n * f, src=sources.get(it, ("",))[0])
                 for it, n in passive.items()],
        final_share=steps[key] * r["ms"] / total_ms, per_hr=steps[key] * f,
        seconds_each=r["ms"] / 1000, detail=r["detail"], note=r.get("note"), combat=r.get("combat"),
        loadout=r.get("loadout"), recorded=recorded(state, r) if state else None,
        uncosted=sorted(uncosted)), None


# ---------------------------------------------------------------- combat history

def recorded(state, r):
    """What the save's loot history says about this enemy, per kill."""
    c = r.get("combat")
    h = (state.get("loot_history") or {}).get(c["enemy_id"]) if c else None
    if not h or not h["kills"]:
        return None
    return dict(kills=h["kills"], seconds=h["seconds"] / h["kills"],
                xp={k: v / h["kills"] for k, v in h["xp"].items() if v / h["kills"] >= 0.05},
                model_seconds=r["ms"] / 1000, model_xp=dict(r["xp_split"]))


def check_combat(state, rates, gear_sets):
    """Model vs the fights the game recorded. History spans all your past levels
    and gear, so the most recently fought enemies are the fair comparison."""
    history = state.get("loot_history") or {}
    fights = [r for r in rates.values() if r.get("combat")]
    fmt = lambda xp: ", ".join(f"{k} {v:.1f}" for k, v in sorted(xp.items(), key=lambda kv: -kv[1]) if v >= 0.05)
    print("Recorded = averages over every kill in your save's loot history (most recent enemy first).")
    print("Model    = simulation with each loadout as it is now; * marks the stance saved for that loadout.\n")
    for eid, h in sorted(history.items(), key=lambda kv: -kv[1]["last_tick"]):
        if not h["kills"]:
            continue
        print(f"{short(eid)}: {h['kills']:,} kills recorded")
        print(f"   recorded            {h['seconds'] / h['kills']:6.1f} s/kill   {fmt({k: v / h['kills'] for k, v in h['xp'].items()})}")
        mine = [r for r in fights if r["combat"]["enemy_id"] == eid]
        if not mine:
            print("   (no model row: area locked or enemy not reachable)")
        for r in sorted(mine, key=lambda r: (r["loadout"] or "", not r["combat"]["your_stance"], r["ms"])):
            c = r["combat"]
            tag = "*" if c["your_stance"] else " "
            print(f"  {tag}{(r['loadout'] or '')[:10]:10} {c['stance'][:9]:9}{r['ms'] / 1000:6.1f} s/kill   {fmt(r['xp_split'])}"
                  f"   [{c['hit']:.0%} hit, {c['min_hit']}-{c['max_hit']}, {c['interval']:.0f}ms]")
        print()


def slayer_tables(engines, rates, price, gold):
    """Auto-slayer per loadout and master, from fights already simulated (no extra simulation)."""
    out = []
    for label, eng in engines.items():
        if not eng.weapon_type:
            continue
        for mid, m in eng.masters.items():
            if not all(eng.reqs.check_raw(r)[0] is not False for r in m.get("UnlockRequirements") or []):
                continue
            enemies = eng.master_enemies(mid)
            if not enemies:
                continue
            best = None
            for sid in eng.stance_options():
                rows = []
                for eid, aid, unlocked in enemies:
                    r = rates.get(f"Combat/{short(eid)}@{short(aid)}/{short(sid)}/{label}")
                    rows.append((eid, r))
                ok = [r for _, r in rows if r]
                if not ok:
                    continue
                tot_ms = sum(r["ms"] for r in ok)
                tot_xp = sum(r["xp"] for r in ok)
                score = tot_xp / tot_ms if len(ok) == len(rows) else -1
                if best is None or score > best[0]:
                    best = (score, sid, rows, tot_ms, tot_xp)
            if not best:
                continue
            _, sid, rows, tot_ms, tot_xp = best
            stuck = [eng.enemies[e]["DisplayName"] for e, r in rows if not r]
            ok = [(e, r) for e, r in rows if r]
            val = lambda r: sum(q * price(i) for i, q in r["outputs"].items()) + r["currency"].get(gold, 0)
            xp_by = defaultdict(float)
            for _, r in ok:
                for k, v in r["xp_split"].items():
                    xp_by[k] += v
            enemy_rows = []
            for e, r in ok:
                rest_ms, rest_xp = tot_ms - r["ms"], tot_xp - r["xp"]
                enemy_rows.append(dict(
                    name=eng.enemies[e]["DisplayName"], seconds=r["ms"] / 1000, xp_kill=r["xp"],
                    xp_hr=r["xp"] / r["ms"] * HOUR, share=r["ms"] / tot_ms,
                    if_blocked=(rest_xp / rest_ms * HOUR) if rest_ms > 0 else None,
                    survival=r["combat"]["survival"]["verdict"], note=r.get("note")))
            enemy_rows.sort(key=lambda x: x["xp_hr"])   # weakest first: these are the ones worth blocking
            out.append(dict(loadout=label, master=m["DisplayName"], stance=eng.stances[sid]["DisplayName"],
                            xp_hr=tot_xp / tot_ms * HOUR if not stuck else None,
                            xp_by_skill={k: v / tot_ms * HOUR for k, v in xp_by.items()},
                            gp_hr=sum(val(r) for _, r in ok) / tot_ms * HOUR,
                            food_hr=sum(r["combat"]["survival"]["food_per_hr"] * r["ms"] / HOUR for _, r in ok) / tot_ms * HOUR,
                            enemies=enemy_rows, stuck=stuck,
                            task_length=(m["BaseTaskLength"], m["BaseTaskLength"] + 3 * eng.levels.get(NS + "Slayer", 1))))
    return out


# ---------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--top", type=int, default=10, help="actions listed per skill in the report (default 10)")
    ap.add_argument("--no-shop", action="store_true", help="do not buy inputs from the shop")
    ap.add_argument("--passive-free", action="store_true",
                    help="treat Farming/Ranching produce as free inputs (they run in parallel, but output is limited)")
    ap.add_argument("--assume-uncertain", action="store_true",
                    help="apply mastery-rank bonuses the save cannot confirm")
    ap.add_argument("--no-combat", action="store_true", help="leave combat out")
    ap.add_argument("--check-combat", action="store_true",
                    help="compare the combat model with the fights recorded in your save, then exit")
    ap.add_argument("--open", action="store_true", help="open the report in your browser")
    args = ap.parse_args()

    for p in (TABLES / "actions.json", SAVE / "player_state.json", SAVE / "active_bonuses.json"):
        if not p.exists():
            sys.exit(f"missing {p} - run build_tables.py and read_save.py first")
    import combat as combat_mod
    import report as report_mod

    actions = load(TABLES / "actions.json")
    state = load(SAVE / "player_state.json")
    active = load(SAVE / "active_bonuses.json")["active"]
    bonuses = Bonuses(active, args.assume_uncertain)
    reqs = Requirements(state)
    calc = Rates(state, bonuses, reqs)
    items = calc.items
    gold = NS + "GoldPieces"

    def item_name(i):
        dn = items.get(i, {}).get("DisplayName")
        return dn.get("Singular", short(i)) if isinstance(dn, dict) else (dn or short(i))

    def price(i):
        sell = items.get(i, {}).get("SellsFor") or {}
        return sell.get("Quantity", 0) if sell.get("Currency") == gold else 0

    rates, passive_rows, blocked = action_rates(actions, state, bonuses, reqs, calc)

    # every equipment loadout: the one worn now plus the saved sets
    gear_sets, no_weapon, engines = [], [], {}
    for lid in combat_mod.loadout_ids(state):
        setup = combat_mod.setup_from_state(state, lid)
        gear_sets.append((setup["name"], setup, setup["worn"], lid))
    combat_skipped = {}
    if not args.no_combat:
        blank = lambda skill: {"skill": skill, "category": None, "subcategory": None, "id": None,
                               "raw_extra": {}, "outputs": []}
        xp_mult = lambda skill: 1 + bonuses.total(blank(skill), "PercentXPGained")
        history = state.get("loot_history") or {}
        only = set(history) if args.check_combat else None
        for label, setup, current, lid in gear_sets:
            engine = combat_mod.Combat(TABLES, state, reqs, items, active, xp_mult, setup, label)
            engines[label] = engine
            fights, skipped = engine.rates(only)
            rates.update(fights)
            for k, v in skipped.items():
                if k.startswith("fights"):
                    combat_skipped[k] = combat_skipped.get(k, 0) + v
                elif k != "no weapon equipped" and current:
                    combat_skipped[k] = v
            if "no weapon equipped" in skipped:
                no_weapon.append(label)

    if args.check_combat:
        check_combat(state, rates, gear_sets)
        return

    # zero-active-time sources: shop purchases and (optionally) passive produce
    sources = zero_time_sources(reqs, gold, args.no_shop, passive_rows, args.passive_free, rates)

    best = solve_chains(rates, sources)
    # a supply nothing can provide (e.g. food you cannot cook) is dropped from the chain, not the action
    uncosted = {k: {item_name(i) for i in v} for k, v in drop_unsourced_supplies(rates, best).items()}
    if uncosted:
        best = solve_chains(rates, sources)
    rows, no_source = [], defaultdict(list)
    for key, r in rates.items():
        if r.get("converter"):
            continue        # instant steps are part of chains, not things to do on their own
        row, missing = chain_row(key, rates, best, sources, item_name, price, gold, state, uncosted.get(key, ()))
        if missing:
            no_source[r["skill"]].append((r["name"], item_name(missing)))
            continue
        rows.append(row)

    OUT.mkdir(parents=True, exist_ok=True)
    with open(OUT / "rates.csv", "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh)
        w.writerow(["skill", "action", "xp_hr_skill", "xp_hr_all_skills", "gp_hr", "xp_by_skill", "times_per_hr",
                    "share_of_hour_on_final_step", "chain", "bought_from_shop", "detail", "note"])
        for row in sorted(rows, key=lambda x: (x["skill"], -x["xp_hr"])):
            w.writerow([row["skill"], row["name"], round(row["xp_hr"]), round(row["xp_total_hr"]), round(row["gp_hr"]),
                        "; ".join(f"{k} {v:.0f}" for k, v in sorted(row["xp_by_skill"].items(), key=lambda x: -x[1])),
                        round(row["per_hr"], 1), round(row["final_share"], 3),
                        " > ".join(f"{s['name']} x{s['per_hr']:.1f}/hr" for s in row["steps"]),
                        "; ".join(f"{s['item']} x{s['per_hr']:.0f}/hr" for s in row["shop"]),
                        row["detail"], row["note"] or ""])

    next_unlock = {}
    for skill, lst in blocked.items():
        lvl = reqs.level(skill)
        nxt = sorted((a["level_req"], a["name"]) for a, miss, unk in lst
                     if a["level_req"] and a["level_req"] > lvl and len(miss) == 1)
        if nxt:
            next_unlock[skill] = nxt[0]
    notes = []
    if passive_rows:
        notes.append(f"{len(passive_rows)} passive Farming, Ranching and Grain Mill actions are not ranked.")
    hidden = sum(1 for v in blocked.values() for _, _, u in v if u)
    if hidden:
        notes.append(f"{hidden} action(s) left out because a quest or statistic requirement could not be read from the save.")
    for k, v in bonuses.skipped.items():
        notes.append(f"{v} bonus(es) not applied: {k}.")
    for k, v in combat_skipped.items():
        notes.append(f"Combat: {v} {k if k.startswith('fights') else 'area(s) skipped (' + k + ')'}.")
    if no_weapon:
        notes.append("Combat: no weapon in " + ", ".join(no_weapon) + ", so it was not evaluated.")
    loadouts = [dict(label=label, name=label + (" (worn now)" if cur else ""),
                     weapon=item_name(gear["Weapon"]["item"]) if gear.get("Weapon") else None,
                     gear=[item_name(v["item"]) for k, v in gear.items()
                           if k == "Weapon" or v["item"] != gear.get("Weapon", {}).get("item")])
                for label, gear, cur, _lid in [(a, b["equipment"], c, d) for a, b, c, d in gear_sets]]
    meta_slayer = slayer_tables(engines, rates, price, gold) if engines else []
    meta = dict(character=state["header"]["character"], total_level=state["header"]["total_level"],
                levels={k: v["level"] for k, v in state["skills"].items()}, top=args.top,
                options=[o for o, on in (("shop purchases off", args.no_shop),
                                         ("passive produce counted as free", args.passive_free),
                                         ("uncertain mastery bonuses applied", args.assume_uncertain)) if on],
                next_unlock=next_unlock, no_source=dict(no_source), notes=notes,
                loadouts=loadouts, slayer=meta_slayer)
    out_html = OUT / "report.html"
    out_html.write_text(report_mod.build(rows, meta), encoding="utf-8")

    # ------------------------------------------------ terminal summary
    by_skill = defaultdict(list)
    for row in rows:
        by_skill[row["skill"]].append(row)
    print(f"{meta['character']} - best per skill, starting with nothing\n")
    print(f"{'skill':14} {'best for XP':30} {'xp/hr':>8}   {'best for GP':30} {'gp/hr':>8}")
    for skill in sorted(by_skill, key=lambda s: -max(x["xp_hr"] for x in by_skill[s])):
        bx = max(by_skill[skill], key=lambda x: x["xp_hr"])
        bg = max(by_skill[skill], key=lambda x: x["gp_hr"])
        print(f"{skill:14} {bx['name'][:30]:30} {bx['xp_hr']:>8,.0f}   {bg['name'][:30]:30} {bg['gp_hr']:>8,.0f}")
    if by_skill.get("Combat"):
        print("\ncombat by loadout (XP/hr is combat XP only):")
        for lo_ in loadouts:
            mine = [x for x in by_skill["Combat"] if x["loadout"] == lo_["label"]]
            if not mine:
                print(f"  {lo_['name']}: not evaluated")
                continue
            bx = max(mine, key=lambda x: x["xp_hr"])
            bg = max(mine, key=lambda x: x["gp_hr"])
            split = ", ".join(f"{k} {v:,.0f}" for k, v in sorted(bx["xp_by_skill"].items(), key=lambda kv: -kv[1])
                              if k in report_mod.COMBAT_SKILLS)
            strip = lambda nm: nm.rsplit(" - ", 1)[0]
            print(f"  {lo_['name']} [{lo_['weapon']}]")
            print(f"    best XP:   {strip(bx['name'])}: {bx['xp_hr']:,.0f} xp/hr ({split}), {bx['gp_hr']:,.0f} gp/hr")
            print(f"    best gold: {strip(bg['name'])}: {bg['gp_hr']:,.0f} gp/hr, {bg['xp_hr']:,.0f} xp/hr")
    for n in notes:
        print("note:", n)
    print(f"\nreport: {out_html}\nall {len(rows)} actions: {OUT / 'rates.csv'}")
    if args.open:
        import webbrowser
        webbrowser.open(out_html.as_uri())


if __name__ == "__main__":
    main()
