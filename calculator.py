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
import json
import math
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


def load(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def objects(name):
    return {NS + o["Id"]: o for o in load(TABLES / "objects" / f"{name}.json")}


# ---------------------------------------------------------------- requirements

class Requirements:
    def __init__(self, state):
        self.state = state
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
        if kind == "DemoLocked":
            return False, "demo locked"
        if kind == "Level":
            return self.level(entity) >= data["Level"], f"{entity} {data['Level']}"
        if kind == "ShopPurchase":
            pid = data["ShopPurchase"]
            return self.state["shop_purchases"].get(pid, 0) >= data.get("Amount", 1), f"buy {short(pid)}"
        if kind == "ToolbeltItemEquipped":
            slots = self.state["toolbelts"].get(entity, {})
            have = any(s["item"] == data["Item"] for s in slots.values())
            return have, f"equip {short(data['Item'])}"
        return None, kind  # quests, statistics: not decoded from the save

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


class Rates:
    def __init__(self, state, bonuses, reqs):
        self.state, self.b, self.reqs = state, bonuses, reqs
        self.items = objects("Item")
        self.thieving = objects("ThievingRecipe")
        self.hunting = objects("HuntingAnimal")
        self.hunt_loc = objects("HuntingLocation")
        self.mining = objects("MiningRecipe")
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
        return dict(ms=iv * hits, xp=self.xp(a) * (1 + shatter), inputs={}, outputs=outs,
                    detail=f"{iv}ms x {hits:.2f} hits (break {p:.0%})")

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

    def r_ThievingRecipe(self, a):
        t = lambda s, i=None: self.b.total(a, s, i)
        rec = self.thieving[a["id"]]
        iv = math.floor((rec["BaseInterval"] + t("FlatInterval")) * (1 + t("PercentInterval")))
        stun = math.floor((2000 + t("FlatStunInterval")) * (1 + t("PercentStunInterval")))
        stealth = max(0, math.floor((self.reqs.level("Thieving") + t("FlatStealth")) * (1 + t("PercentStealth"))))
        perception = max(0, math.floor((rec["Perception"] + t("FlatPerception")) * (1 + t("PercentPerception"))))
        win = min(1.0, (100 + stealth) / (100 + perception))
        avoid = min(1, t("StunAvoidanceChance"))
        per_success = iv / win + (1 - win) / win * (1 - avoid) * stun
        stages = max(1, rec["Stages"] + int(t("FlatStages")))
        successes = expected_fires(stages, min(1, t("SkipStageChance")), guarded=False)
        outs, cur = {}, {}

        def table(rows, chance, scale):
            tw = sum(r["Weight"] for r in rows) or 1
            for r in rows:
                q = (r["MinQuantity"] + r["MaxQuantity"]) / 2
                outs[r["Item"]] = outs.get(r["Item"], 0) + chance * r["Weight"] / tw * q * scale

        c = rec.get("CurrencyReward")
        if c:
            ch = min(1, rec["BaseCurrencyLootChance"] * (1 + t("PercentCurrencyLootChance")))
            cur[c["Currency"]] = ch * (c["MinQuantity"] + c["MaxQuantity"]) / 2 * (1 + t("PercentRewardCurrency")) * successes
        table(rec["CommonDropTable"], min(1, rec["BaseCommonLootChance"] * (1 + t("PercentCommonLootChance"))), successes)
        table(rec["RareDropTable"], min(1, rec["BaseRareLootChance"] * (1 + t("PercentRareLootChance"))), successes)
        table(rec["CompletionDropTable"], 1.0, 1)
        return dict(ms=per_success * successes, xp=self.xp(a), inputs={}, outputs=outs, currency=cur,
                    detail=f"{iv}ms, {win:.0%} success, {stages} stage(s)",
                    note="takes damage on failure; food not counted" if win < 1 else None)

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
    for _ in range(max_iter):
        changed = False
        for item, plist in producers.items():
            for key, q in plist:
                r = rates[key]
                cost = r["ms"]
                for inp, n in r["inputs"].items():
                    cost += n * best[inp][0]
                    if cost == INF:
                        break
                unit = cost / q
                if unit < best[item][0] - 1e-9:
                    best[item] = (unit, key)
                    changed = True
        if not changed:
            break
    return best


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
            elif src == "passive":
                passive[inp] += n * mult
            elif src is None or src in stack or len(stack) > 12:
                return inp
            else:
                missing = visit(src, n * mult / rates[src]["outputs"][inp], stack + (k,))
                if missing:
                    return missing
        steps[k] = steps.get(k, 0.0) + mult
        return None

    missing = visit(key, 1.0, ())
    return (None, None, None, missing) if missing else (steps, shop, passive, None)


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

    rates, passive_rows, blocked = {}, [], defaultdict(list)
    for a in actions:
        if a["category"] and "Quest" in a["category"]:
            continue  # one-off quest objectives
        ok, missing, unknown = reqs.action(a)
        if not ok:
            if not a["demo_locked"]:
                blocked[a["skill"]].append((a, missing, unknown))
            continue
        if a["type"] in PASSIVE_TYPES:
            passive_rows.append(a)
            continue
        r = calc.compute(a)
        if r is None or not r["ms"]:
            continue
        r.update(key=a["key"], skill=a["skill"], name=a["name"], level_req=a["level_req"] or 1,
                 xp_split={a["skill"]: r["xp"]})
        rates[a["key"]] = r

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
    sources = {}
    for a in passive_rows if args.passive_free else []:
        for o in a["outputs"]:
            sources[o["item"]] = ("passive", 0.0)
    if not args.no_shop:
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

    best = solve_chains(rates, sources)
    # a combat input nothing can supply (e.g. food you cannot cook) is dropped from the chain, not the fight
    uncosted = defaultdict(set)
    for key, r in rates.items():
        if r["skill"] == "Combat":
            for inp in [i for i in r["inputs"] if best[i][1] is None]:
                del r["inputs"][inp]
                uncosted[key].add(item_name(inp))
    if uncosted:
        best = solve_chains(rates, sources)
    rows, no_source = [], defaultdict(list)
    for key, r in rates.items():
        steps, shop, passive, missing = expand(key, rates, best)
        if missing:
            no_source[r["skill"]].append((r["name"], item_name(missing)))
            continue
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
                name=sr["name"], skill=sr["skill"], final=k == key, per_hr=c * f,
                share=c * sr["ms"] / total_ms, secs_each=sr["ms"] / 1000,
                xp_hr={sk: v * c * f for sk, v in sr["xp_split"].items()},
                uses=[(n * c * f, item_name(it)) for it, n in sr["inputs"].items() if n > 0],
                makes=makes[:4], detail=sr["detail"], note=sr.get("note")))
        shop_rows, shop_cost = [], 0.0
        for it, n in shop.items():
            net[it] += n
            shop_cost += n * sources[it][1]
            shop_rows.append(dict(item=item_name(it), per_hr=n * f, gp_hr=n * sources[it][1] * f))
        for it, n in passive.items():
            net[it] += n
        sold = sorted(((q * price(it) * f, q * f, item_name(it)) for it, q in net.items()
                       if q > 1e-9 and price(it) > 0), reverse=True)
        sell_value = sum(v for v, _, _ in sold)
        own_xp = (sum(r["xp_split"].values()) * steps[key] * f if r["skill"] == "Combat"
                  else xp_by_skill.get(r["skill"], 0.0))
        rows.append(dict(
            skill=r["skill"], name=r["name"], key=key, level_req=r["level_req"],
            xp_hr=own_xp, xp_total_hr=sum(xp_by_skill.values()), xp_by_skill=dict(xp_by_skill),
            gp_hr=sell_value + coins * f - shop_cost * f, sell_hr=sell_value, coins_hr=coins * f,
            shop_hr=shop_cost * f, other_currencies=dict(other_cur),
            sold=[dict(gp_hr=v, per_hr=q, item=n) for v, q, n in sold],
            steps=step_rows, shop=shop_rows,
            passive=[dict(item=item_name(it), per_hr=n * f) for it, n in passive.items()],
            final_share=steps[key] * r["ms"] / total_ms, per_hr=steps[key] * f,
            seconds_each=r["ms"] / 1000, detail=r["detail"], note=r.get("note"), combat=r.get("combat"),
            loadout=r.get("loadout"), recorded=recorded(state, r), uncosted=sorted(uncosted.get(key, ()))))

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
