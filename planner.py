"""Levelling planner: the fastest route from your current level to a target level.

At every level the planner picks the action with the most XP per hour in the skill,
counting the time to gather and craft its ingredients from nothing (the same chains as
the report). XP earned in other skills along the way raises those levels too, so later
steps can use what they unlock.

A focus can restrict the final action to a category (e.g. Fletching > Arrows) and/or to
what its products are used for (combat, consumables, an ingredient for another skill).
Ingredients still come from the fastest source.

Combat skills are planned from a combat setup (gear, stance, food, prayers). Fights are
re-evaluated at a handful of checkpoints rather than every level, because each scan
simulates every enemy.
"""
import math
import time

import calculator as calc
import combat as cmb

NS = cmb.NS
HOUR = 3_600_000
MAX_LEVEL = 99          # every skill's level cap in the game
COMBAT_SKILLS = ("Attack", "Strength", "Defence", "Hitpoints", "Ranged", "Magic", "Prayer", "Slayer")
COMBAT_CHECKPOINTS = 10    # enemy scans per combat plan
SCAN_KILLS = 40            # quick scan of every enemy
TOP_CANDIDATES = 3         # re-simulated in full before choosing


def _xp_table():
    xp, tot = [0, 0], 0
    for lvl in range(1, MAX_LEVEL + 1):
        tot += math.floor(lvl + 300 * 2 ** (lvl / 7))
        xp.append(tot // 4)
    return xp


XP = _xp_table()   # XP[L] = total XP needed to reach level L (checked against every skill in a real save)


def level_for(xp):
    lvl = 1
    while lvl < MAX_LEVEL and XP[lvl + 1] <= xp:
        lvl += 1
    return lvl


USE_LABELS = {
    "combat": "Used in combat (gear, ammo, runes, food)",
    "gear": "Combat gear you can wear",
    "ammo": "Ammo",
    "runes": "Runes for spells",
    "food": "Food",
    "consumable": "Consumables (used up: food, ammo, runes, bait, traps...)",
    "tools": "Skilling tools (toolbelt)",
}
PRAYER_POINTS = "PrayerPoints"
CONSUMABLE_CAPS = {"Food", "FishingBait", "HuntingTrap", "Compost", "Bury"}
# items a skill uses up without them being a recipe ingredient
CAP_FEEDS = {"HuntingTrap": "Hunting", "FishingBait": "Fishing", "Compost": "Farming", "Bury": "Prayer"}


class Planner:
    def __init__(self, world):
        self.w = world
        self.actions = [a for a in calc.load(calc.TABLES / "actions.json")
                        if not (a["category"] and "Quest" in a["category"]) and not a.get("quest_gates")]
        self.reqs = calc.Requirements(world.state)          # category tables load once
        self.rates = calc.Rates(world.state, world.bonuses, self.reqs)
        self.by_key = {a["key"]: a for a in self.actions}
        self.food_override = None       # a Thieving food chosen in the panel instead of the saved one
        self._tech_xp = None            # Agility technique XP while an Agility route is being planned
        self._tech_cap = {t: o.get("LevelCap") or 40 for t, o in calc.objects("AgilityTechnique").items()} \
            if (calc.TABLES / "objects" / "AgilityTechnique.json").exists() else {}
        self._item_tags = self._tag_items()
        self._names = self._category_names()

    # ------------------------------------------------------------ focus: what the final action makes
    def _tag_items(self):
        """item -> set of use tags (see USE_LABELS, plus 'skill:<name>' for ingredients)."""
        runes = {c["Item"] for s in calc.load(calc.TABLES / "objects" / "MagicSpell.json") for c in s["RuneCosts"]}
        tags = {}
        for a in self.actions:
            if a["demo_locked"]:
                continue
            for i in a["inputs"]:
                tags.setdefault(i["item"], set()).add("skill:" + a["skill"])
        for iid, it in self.w.items.items():
            caps = {cmb.short(k): v for k, v in (it.get("Capabilities") or {}).items()}
            t = tags.setdefault(iid, set())
            eq = caps.get("Equipment")
            if eq and eq.get("AmmoType"):
                t |= {"ammo", "consumable", "combat"}
            elif eq:
                t |= {"gear", "combat"}
            if iid in runes:
                t |= {"runes", "consumable", "combat"}
            if "Food" in caps:
                t |= {"food", "combat"}
            if caps.keys() & CONSUMABLE_CAPS:
                t.add("consumable")
            for cap, skill in CAP_FEEDS.items():
                if cap in caps:
                    t.add("skill:" + skill)
            if "Toolbelt" in caps:
                t.add("tools")
        return tags

    def _category_names(self):
        names = {}
        for table in ("RecipeCategory", "RecipeSubcategory", "FishingLocation", "ThievingLocation", "HuntingLocation",
                      "WoodcuttingRecipeCategory", "MiningRecipeCategory"):
            for oid, o in calc.objects(table).items():
                dn = o.get("DisplayName")
                names.setdefault(oid, dn.get("Singular") if isinstance(dn, dict) else (dn or cmb.short(oid)))
        return names

    def action_tags(self, a):
        out = set()
        for o in a["outputs"]:
            out |= self._item_tags.get(o["item"], set())
        return out

    @staticmethod
    def _cat_keys(a):
        keys = [a["category"]] if a["category"] else []
        if a["category"] and a["subcategory"]:
            keys.append(f"{a['category']}|{a['subcategory']}")
        return keys

    def matches(self, a, focus):
        if not focus:
            return True
        if focus.get("category") and focus["category"] not in self._cat_keys(a):
            return False
        if focus.get("use") and focus["use"] not in self.action_tags(a):
            return False
        return True

    def focus_options(self, skill):
        """Categories and uses that some action in this skill can satisfy, with the level each first unlocks."""
        cats, uses = {}, {}
        for a in self.actions:
            if a["skill"] != skill or a["demo_locked"] or a["type"] in calc.PASSIVE_TYPES:
                continue
            lv = a["level_req"] or 1
            for k in self._cat_keys(a):
                c = cats.setdefault(k, {"id": k, "count": 0, "level": lv})
                c["count"] += 1
                c["level"] = min(c["level"], lv)
            for t in self.action_tags(a):
                u = uses.setdefault(t, {"id": t, "count": 0, "level": lv})
                u["count"] += 1
                u["level"] = min(u["level"], lv)
        for k, c in cats.items():
            cat, _, sub = k.partition("|")
            c["label"] = self._names.get(cat, cmb.short(cat)) + (f" › {self._names.get(sub, cmb.short(sub))}" if sub else "")
            c["sub"] = bool(sub)
        for t, u in uses.items():
            u["label"] = USE_LABELS.get(t) or f"Ingredient for {t.split(':', 1)[1]}"
        order = list(USE_LABELS)
        return {
            "categories": sorted(cats.values(), key=lambda c: (c["id"].split("|")[0], c["sub"], c["label"])),
            "uses": sorted(uses.values(), key=lambda u: (order.index(u["id"]) if u["id"] in order else 99, u["label"])),
        }

    def focus_label(self, skill, focus):
        if not focus:
            return None
        opts = self.focus_options(skill)
        parts = [next((c["label"] for c in opts["categories"] if c["id"] == focus.get("category")), None),
                 next((u["label"] for u in opts["uses"] if u["id"] == focus.get("use")), None)]
        return " + ".join(p for p in parts if p) or None

    # ------------------------------------------------------------ shared
    def skills(self):
        """Skills the planner can route, with current level, XP and focus options."""
        st = self.w.state
        doable = {a["skill"] for a in self.actions
                  if not a["demo_locked"] and a["type"] not in calc.PASSIVE_TYPES}
        out = []
        for k, v in st["skills"].items():
            if k in doable or k in COMBAT_SKILLS:
                out.append({"skill": k, "level": v["level"], "xp": v["xp"], "combat": k in COMBAT_SKILLS,
                            "focus": None if k in COMBAT_SKILLS else self.focus_options(k)})
        return sorted(out, key=lambda x: (x["combat"], x["skill"]))

    def _start(self, skill, target):
        st = self.w.state
        if skill not in st["skills"]:
            raise ValueError(f"unknown skill {skill}")
        xp = {k: float(v["xp"]) for k, v in st["skills"].items()}
        cur = st["skills"][skill]["level"]
        target = int(target)
        if not cur < target <= MAX_LEVEL:
            raise ValueError(f"target must be between {cur + 1} and {MAX_LEVEL}")
        return xp, cur, target

    @staticmethod
    def _levels(xp):
        return {k: level_for(v) for k, v in xp.items()}

    def _base(self):
        """The save state, with the Thieving food swapped if a different one was chosen for this plan."""
        if not self.food_override:
            return self.w.state
        st = dict(self.w.state)
        st["thieving_food"] = self.food_override
        st["agility_food"] = self.food_override
        return st

    def _state_at(self, xp):
        st = cmb.with_levels(self._base(), self._levels(xp))
        if self._tech_xp is not None:          # Agility plan: techniques at their planned XP
            st = dict(st)
            st["agility_techniques"] = {t: {"xp": v, "level": calc.technique_level(v, self._tech_cap.get(t, 40))}
                                        for t, v in self._tech_xp.items()}
        return st

    # ------------------------------------------------------------ skilling
    def plan(self, skill, target, no_shop=False, setup=None, focus=None, use_bank=False, assume_tools=False, food=None,
             stealth=0):
        t0 = time.time()
        self.reqs.assume_tools = bool(assume_tools)
        self.food_override = food or None
        self.rates.extra_stealth = int(stealth or 0)
        try:
            res = self._plan(skill, target, no_shop, setup, focus, use_bank)
        finally:
            self.reqs.assume_tools = False
            self.food_override = None
            self.rates.extra_stealth = 0
        if stealth and skill == "Thieving" and not res.get("error"):
            res.setdefault("notes", []).insert(0, f"Includes +{int(stealth)} what-if stealth (gear or ranks you don't have yet).")
        if assume_tools and not res.get("error"):
            res.setdefault("notes", []).insert(0, "Assumes you equip any toolbelt item an action needs (for example the "
                                                  "Fire Wizard Hat and Robes for the Mage Tower).")
        res["seconds"] = round(time.time() - t0, 1)
        return res

    def _plan(self, skill, target, no_shop, setup, focus, use_bank):
        focus = {k: v for k, v in (focus or {}).items() if v and k in ("category", "use")} or None
        if focus and skill not in COMBAT_SKILLS:
            opts = self.focus_options(skill)
            if focus.get("category") and focus["category"] not in {c["id"] for c in opts["categories"]}:
                raise ValueError(f"{skill} has no category {focus['category']!r}")
            if focus.get("use") and focus["use"] not in {u["id"] for u in opts["uses"]}:
                raise ValueError(f"nothing in {skill} is used for {focus['use']!r}")
        if skill in COMBAT_SKILLS:
            res = self._plan_combat(skill, target, setup)
        elif use_bank:
            res = self._plan_with_bank(skill, target, no_shop, focus)
        else:
            res = self._plan_skilling(skill, target, no_shop, focus)
        return res

    # ------------------------------------------------------------ look ahead
    def look_ahead(self, skill, up_to=60, assume_tools=True, food=None, stealth=0):
        """Every action in `skill` that unlocks by level `up_to`, costed at the level it unlocks and at `up_to`
        (from a standing start, as in the report). Other skills stay at your current levels."""
        self.food_override = food or None
        self.rates.extra_stealth = int(stealth or 0)
        try:
            return self._look_ahead(skill, up_to, assume_tools)
        finally:
            self.food_override = None
            self.rates.extra_stealth = 0
            self.reqs.state = self.rates.state = self.w.state

    def _look_ahead(self, skill, up_to, assume_tools):
        w = self.w
        gold = NS + "GoldPieces"
        cur = w.state["skills"].get(skill, {}).get("level", 1)
        up_to = max(cur, min(MAX_LEVEL, int(up_to)))
        acts = [a for a in self.actions if a["skill"] == skill and not a["demo_locked"]
                and a["type"] not in calc.PASSIVE_TYPES and (a["level_req"] or 1) <= up_to]
        at_level = {}
        for lvl in sorted({max(cur, a["level_req"] or 1) for a in acts} | {up_to}):
            st = cmb.with_levels(self._base(), {skill: lvl})
            if skill == "Agility" and lvl == up_to:
                # the target column assumes every technique trained to its cap (they level as you run courses)
                st = dict(st)
                st["agility_techniques"] = {t: {"xp": calc.technique_xp_for(c), "level": c} for t, c in self._tech_cap.items()}
            self.reqs.state, self.reqs.assume_tools = st, assume_tools
            self.rates.state, self.rates.reqs = st, self.reqs
            try:
                rates, passive, blocked = calc.action_rates(self.actions, st, w.bonuses, self.reqs, self.rates)
                sources = calc.zero_time_sources(self.reqs, gold, passive_rows=passive, rates=rates)
                best = calc.solve_chains(rates, sources)
                if calc.drop_unsourced_supplies(rates, best):
                    best = calc.solve_chains(rates, sources)
                rows = {}
                for a in acts:
                    if a["key"] in rates:
                        row, _ = calc.chain_row(a["key"], rates, best, sources, w.name, w.price, gold)
                        rows[a["key"]] = (row, rates[a["key"]])
                at_level[lvl] = rows
                # what else, besides the level, each action still needs (checked without assuming tools)
                self.reqs.assume_tools = False
                if lvl == up_to:
                    extra = {a["key"]: [m for m in self.reqs.action(a)[1] if not m.startswith(skill + " ")] for a in acts}
            finally:
                self.reqs.assume_tools = False
        self.reqs.state = self.rates.state = w.state
        self.rates.reqs = self.reqs
        out = []
        for a in sorted(acts, key=lambda a: ((a["level_req"] or 1), a["name"])):
            lr = a["level_req"] or 1
            first, last = at_level.get(max(cur, lr), {}).get(a["key"]), at_level.get(up_to, {}).get(a["key"])

            def figures(pair):
                if not pair or not pair[0]:
                    return None
                row, r = pair
                th = r.get("thieving") or r.get("agility") or {}
                per_hr = row["per_hr"]
                return {"xp_hr": row["xp_hr"], "gp_hr": row["gp_hr"], "success": th.get("success"),
                        "food_hr": th.get("food_per_completion", 0.0) * per_hr if th else None,
                        "deaths_hr": th.get("deaths_per_hr"), "note": r.get("note"),
                        "chain": [s["name"] for s in row["steps"]]}

            out.append({"key": a["key"], "name": a["name"], "level": lr, "unlocked": lr <= cur and not extra.get(a["key"]),
                        "needs": extra.get(a["key"], []), "at_unlock": figures(first), "at_target": figures(last)})
        self.rates.state = self._base()
        food_id, _ = (self.rates.thieving_food() if skill == "Thieving" else
                      self.rates.agility_food() if skill == "Agility" else (None, 0))
        return {"skill": skill, "level": cur, "up_to": up_to, "actions": out,
                "food": w.name(food_id) if food_id else None}

    # ------------------------------------------------------------ skilling, using what is in the bank
    def _draw(self, key, count, inv, rates, best, acc, depth=0):
        """Do `key` `count` times, taking ingredients from `inv` first and gathering / buying the rest.
        Fills acc (steps in the order you would do them, bought, stock used); False if an ingredient has no source."""
        for inp, n in rates[key]["inputs"].items():
            q = n * count
            if q <= 0:
                continue
            take = min(inv.get(inp, 0.0), q)
            if take > 0:
                inv[inp] -= take
                acc["stock"][inp] = acc["stock"].get(inp, 0.0) + take
                q -= take
            if q <= 1e-9:
                continue
            src = best[inp][1]
            if src == "shop":
                acc["bought"][inp] = acc["bought"].get(inp, 0.0) + q
            elif src == "passive":
                continue
            elif src is None or depth > 12:
                return False
            else:
                times = q / rates[src]["outputs"][inp]
                if not self._draw(src, times, inv, rates, best, acc, depth + 1):
                    return False
                for o, oq in rates[src]["outputs"].items():      # by-products go into stock
                    if o != inp:
                        inv[o] = inv.get(o, 0.0) + oq * times
        acc["steps"][key] = acc["steps"].get(key, 0.0) + count
        return True

    def _level_with_stock(self, key, need, skill, inv, rates, best):
        """Fewest completions of `key` that earn `need` XP in `skill`, given the stock. Ingredients already in
        stock skip their own steps, so XP per completion varies; found by bisection (XP rises with completions)."""
        own = rates[key]["xp_split"].get(skill, 0.0)
        if own <= 0:
            return None

        def run(count):
            acc = {"steps": {}, "bought": {}, "stock": {}}
            stock = dict(inv)
            if not self._draw(key, count, stock, rates, best, acc):
                return None
            gained = sum(c * rates[k]["xp_split"].get(skill, 0.0) for k, c in acc["steps"].items())
            return gained, acc, stock

        lo, hi = 0.0, need / own
        if run(hi) is None:
            return None
        for _ in range(40):
            mid = (lo + hi) / 2
            if run(mid)[0] >= need:
                hi = mid
            else:
                lo = mid
        gained, acc, stock = run(hi)
        ms = sum(c * rates[k]["ms"] for k, c in acc["steps"].items())
        return {"key": key, "count": hi, "ms": ms, "acc": acc, "stock": stock}

    def _plan_with_bank(self, skill, target, no_shop, focus):
        w = self.w
        xp, cur, target = self._start(skill, target)
        gold = NS + "GoldPieces"
        start_levels = self._levels(xp)
        inv = {k: float(v) for k, v in (w.state.get("bank") or {}).items()}
        steps = []
        for lvl in range(cur, target):
            st = self._state_at(xp)
            self.reqs.state = st
            self.rates.state, self.rates.reqs = st, self.reqs
            rates, passive, _ = calc.action_rates(self.actions, st, w.bonuses, self.reqs, self.rates)
            sources = calc.zero_time_sources(self.reqs, gold, no_shop, passive, rates=rates)
            best = calc.solve_chains(rates, sources)
            if calc.drop_unsourced_supplies(rates, best):
                best = calc.solve_chains(rates, sources)
            need = XP[lvl + 1] - max(xp[skill], XP[lvl])
            pick = in_focus = None
            for key, r in rates.items():
                if r["skill"] != skill or r.get("converter"):
                    continue
                cand = self._level_with_stock(key, need, skill, inv, rates, best)
                if cand is None:
                    continue
                if pick is None or cand["ms"] < pick["ms"]:
                    pick = cand
                if self.matches(self.by_key[key], focus) and (in_focus is None or cand["ms"] < in_focus["ms"]):
                    in_focus = cand
            outside = bool(focus) and in_focus is None
            pick = in_focus or pick
            if pick is None:
                return self._stuck(skill, cur, target, lvl, steps, start_levels, xp, focus,
                                   f"Nothing in {skill} can be trained at level {lvl} with what you have unlocked.")
            acc, key, hours = pick["acc"], pick["key"], pick["ms"] / HOUR
            inv = pick["stock"]
            gained = {}
            for k, c in acc["steps"].items():
                for sk, v in rates[k]["xp_split"].items():
                    gained[sk] = gained.get(sk, 0.0) + v * c
            for sk, v in gained.items():
                if sk != skill:
                    xp[sk] = xp.get(sk, 0.0) + v
            xp[skill] = float(XP[lvl + 1])
            made = {i: q * pick["count"] for i, q in rates[key]["outputs"].items()}
            for i, q in made.items():                         # what you make is available to later steps
                inv[i] = inv.get(i, 0.0) + q
            cost = sum(q * sources[i][1] for i, q in acc["bought"].items())
            steps.append({
                "level": lvl, "key": key, "name": rates[key]["name"], "xp_hr": need / hours, "hours": hours,
                "outside_focus": outside, "xp": gained,
                "gp": sum(q * w.price(i) for i, q in made.items()) - cost,
                "chain": [{"name": rates[k]["name"], "skill": rates[k]["skill"], "final": k == key, "times": c,
                           "makes": {w.name(i): q * c for i, q in rates[k]["outputs"].items() if q * c >= 0.05}}
                          for k, c in acc["steps"].items()],
                "bought": {w.name(i): q for i, q in acc["bought"].items()},
                "from_bank": {w.name(i): q for i, q in acc["stock"].items()},
            })
        res = self._result(skill, cur, target, steps, start_levels, xp, kind="skill", focus=focus)
        res["used_bank"] = True
        res["notes"].insert(0, "Ingredients are taken from your bank (and from what earlier steps made) before gathering "
                               "or buying more. Selling the results is not counted in the time.")
        return res

    def _plan_skilling(self, skill, target, no_shop, focus):
        if skill == "Agility":
            self._tech_xp = {t: v["xp"] for t, v in (self.w.state.get("agility_techniques") or {}).items()}
        try:
            return self._plan_skilling_levels(skill, target, no_shop, focus)
        finally:
            self._tech_xp = None

    @staticmethod
    def _level_parts(cur, target, skill, xp):
        """(level, part number) until each level is reached: a level can take several parts (Agility re-plans)."""
        for lvl in range(cur, target):
            part = 0
            while xp[skill] < XP[lvl + 1] - 1e-6:
                yield lvl, part
                part += 1
                if part > 8:          # safety: never loop forever on a rounding edge
                    xp[skill] = float(XP[lvl + 1])

    def _tech_gain(self, key, rates, laps):
        return {t: v * laps for t, v in (rates[key].get("technique_xp") or {}).items()}

    def _plan_skilling_levels(self, skill, target, no_shop, focus):
        w = self.w
        xp, cur, target = self._start(skill, target)
        gold = NS + "GoldPieces"
        start_levels = self._levels(xp)
        steps = []
        for lvl, part in self._level_parts(cur, target, skill, xp):
            st = self._state_at(xp)
            self.reqs.state = st
            self.rates.state, self.rates.reqs = st, self.reqs
            rates, passive, _ = calc.action_rates(self.actions, st, w.bonuses, self.reqs, self.rates)
            sources = calc.zero_time_sources(self.reqs, gold, no_shop, passive, rates=rates)
            best = calc.solve_chains(rates, sources)
            if calc.drop_unsourced_supplies(rates, best):
                best = calc.solve_chains(rates, sources)
            row = in_focus = None
            for key, r in rates.items():
                if r["skill"] != skill or r.get("converter"):
                    continue
                cand, missing = calc.chain_row(key, rates, best, sources, w.name, w.price, gold)
                if not cand or cand["xp_hr"] <= 0:
                    continue
                if row is None or cand["xp_hr"] > row["xp_hr"]:
                    row = cand
                if self.matches(self.by_key[key], focus) and (in_focus is None or cand["xp_hr"] > in_focus["xp_hr"]):
                    in_focus = cand
            outside = bool(focus) and in_focus is None
            row = in_focus or row
            if row is None:
                return self._stuck(skill, cur, target, lvl, steps, start_levels, xp, focus,
                                   f"Nothing in {skill} can be trained at level {lvl} with what you have unlocked.")
            need = XP[lvl + 1] - max(xp[skill], XP[lvl])
            hours = need / row["xp_hr"]
            if self._tech_xp is not None and part < 3:
                # techniques level up as you run courses: if one would gain 2+ levels, stop halfway and re-plan
                gain = self._tech_gain(row["key"], rates, row["per_hr"] * hours)
                if any(calc.technique_level(self._tech_xp.get(t, 0) + g) - calc.technique_level(self._tech_xp.get(t, 0)) >= 2
                       for t, g in gain.items()):
                    hours /= 2
            for sk, v in row["xp_by_skill"].items():
                if sk != skill:
                    xp[sk] = xp.get(sk, 0.0) + v * hours
            xp[skill] = min(float(XP[lvl + 1]), max(xp[skill], XP[lvl]) + row["xp_hr"] * hours)
            if self._tech_xp is not None:
                for t, g in self._tech_gain(row["key"], rates, row["per_hr"] * hours).items():
                    self._tech_xp[t] = self._tech_xp.get(t, 0.0) + g
            steps.append({
                "level": lvl, "key": row["key"], "name": row["name"], "xp_hr": row["xp_hr"], "hours": hours,
                "outside_focus": outside,
                "xp": {k: v * hours for k, v in row["xp_by_skill"].items()}, "gp": row["gp_hr"] * hours,
                # totals for this level: times each step is done, and what it makes
                "chain": [{"name": s["name"], "skill": s["skill"], "final": s["final"], "times": s["per_hr"] * hours,
                           "makes": {nm: q * hours for q, nm in s["makes"]}} for s in row["steps"]],
                "bought": {s["item"]: s["per_hr"] * hours for s in row["shop"]},
            })
        return self._result(skill, cur, target, steps, start_levels, xp, kind="skill", focus=focus)

    # ------------------------------------------------------------ combat
    def _engine(self, setup, xp, kills):
        w = self.w
        st = cmb.with_levels(w.state, dict(self._levels(xp), **{k: v for k, v in (setup.get("levels") or {}).items()
                                                                  if k not in xp}))
        reqs = calc.Requirements(st)
        return cmb.Combat(calc.TABLES, st, reqs, w.items, w.active, w.xp_mult, setup, "plan", kills=kills)

    def _plan_combat(self, skill, target, setup):
        xp, cur, target = self._start(skill, target)
        if not setup:
            raise ValueError("pick a combat setup first")
        setup = dict(setup, levels={})          # levels come from the plan, not the page's what-if boxes
        if skill == "Prayer" and not setup.get("prayers"):
            return {"error": "Prayer XP only comes from active prayers. Tick at least one prayer, then plan again."}
        start_levels = self._levels(xp)
        probe = self._engine(setup, xp, SCAN_KILLS)
        if not probe.weapon:
            return {"error": "No weapon equipped. Pick a weapon to plan combat."}
        stances = [s for s in probe.stance_options()
                   if skill in ("Hitpoints", "Prayer", "Slayer")
                   or skill in [cmb.short(g["Component"]["Entity"]) for g in probe.stances[s]["ExperienceGain"]]]
        if not stances:
            return {"error": f"This weapon has no stance that trains {skill}. Pick a different weapon."}

        span = target - cur
        every = max(1, math.ceil(span / COMBAT_CHECKPOINTS))
        steps, lvl, notes, ammo_seen = [], cur, set(), None
        while lvl < target:
            fast = self._engine(setup, xp, SCAN_KILLS)
            fights, _ = fast.rates(None, stances, survival=False)
            ranked = sorted((r for r in fights.values() if r["combat"]["survival"]["verdict"] != "death"),
                            key=lambda r: -r["xp_split"].get(skill, 0) / r["ms"])
            full = self._engine(setup, xp, cmb.KILLS)
            best = None
            for r in ranked[:TOP_CANDIDATES]:
                c = r["combat"]
                x = full.fight(c["area_id"], c["enemy_id"], c["stance_id"], True)
                if not x or x["combat"]["survival"]["verdict"] == "death":
                    continue
                if best is None or x["xp_split"].get(skill, 0) / x["ms"] > best["xp_split"].get(skill, 0) / best["ms"]:
                    best = x
            if best is None or not best["xp_split"].get(skill):
                why = (f"No enemy you can reach gives {skill} XP without a risk of dying at level {lvl}."
                       if fights else f"No enemy can be fought at level {lvl} with this setup.")
                return self._stuck(skill, cur, target, lvl, steps, start_levels, xp, None, why)
            kph = HOUR / best["ms"]
            summary = self.w.summarize(best)
            use_hr = {i: q * kph for i, q in best["inputs"].items() if q > 0}
            if best["combat"].get("prayer_points"):
                use_hr[PRAYER_POINTS] = best["combat"]["prayer_points"] * kph
            ammo = full.ammo if full.weapon_cap.get("RequiredAmmoType") else None
            xp_hr = {k: v * kph for k, v in best["xp_split"].items()}
            surv = best["combat"]["survival"]
            stop = min(target, lvl + every)
            for lv in range(lvl, stop):
                need = XP[lv + 1] - max(xp[skill], XP[lv])
                hours = need / xp_hr[skill]
                for sk, v in xp_hr.items():
                    if sk != skill:
                        xp[sk] = xp.get(sk, 0.0) + v * hours
                xp[skill] = float(XP[lv + 1])
                steps.append({
                    "level": lv, "key": best["key"], "name": f"{best['enemy']} ({best['stance']})",
                    "xp_hr": xp_hr[skill], "hours": hours, "xp": {k: v * hours for k, v in xp_hr.items()},
                    "gp": summary["gp_hr"] * hours, "area": best["area"],
                    "food_hr": surv["food_per_hr"], "food_used": surv["food_per_hr"] * hours,
                    "verdict": surv["verdict"], "kills": kph * hours,
                    "loot": {x["item"]: x["per_hr"] * hours for x in summary["loot"][:6] if x["per_hr"] * hours >= 0.5},
                    "used": {i: v * hours for i, v in use_hr.items()},
                    "bought": {}, "chain": [],
                })
            ammo_seen = ammo_seen or ammo
            lvl = stop
        notes.add(f"Fights are re-chosen every {every} level{'s' if every > 1 else ''} "
                  "(each check simulates every enemy); XP/hr is held between checks, so times are slightly pessimistic.")
        if skill == "Slayer":
            notes.add("Slayer XP assumes every kill is on a slayer task.")
        res = self._result(skill, cur, target, steps, start_levels, xp, kind="combat")
        res["notes"] = sorted(notes) + res["notes"]
        res["food"] = self.w.name(setup["food"]) if setup.get("food") else None
        res["food_have"] = int(setup.get("food_qty") or 0)
        res["food_used"] = sum(s.get("food_used", 0) for s in steps)
        res["supplies"] = self._supplies(steps, setup, ammo_seen)
        res["prayers"] = list(probe.active_prayers)
        short_of = [s for s in res["supplies"] if s["runs_out"]]
        for s in reversed(short_of):
            res["notes"].insert(0, f"You need about {s['total']:,.0f} {s['name']} but have {s['have']:,.0f}: it runs out "
                                   f"after {s['runs_out']['hours']:.1f} h (around level {s['runs_out']['level']}). "
                                   "Top up before you start.")
        return res

    def _supplies(self, steps, setup, ammo):
        """Every consumable the plan uses: per hour, total, what you have, and when it would run out."""
        st = self.w.state
        bank = st.get("bank") or {}
        runes = self._runes()
        have = {PRAYER_POINTS: (st.get("currencies") or {}).get("PrayerPoints", 0)}
        if setup.get("food"):
            have[setup["food"]] = setup.get("food_qty") or 0
        for slot in (setup.get("equipment") or {}).values():   # ammo sits in an equipment slot
            if slot and slot.get("item") == ammo:
                have[ammo] = slot.get("qty", 0) + bank.get(ammo, 0)
        order, totals = [], {}
        for s in steps:
            for i, q in s.get("used", {}).items():
                if i not in totals:
                    order.append(i)
                totals[i] = totals.get(i, 0) + q
        hours = sum(s["hours"] for s in steps) or 1
        out = []
        for i in order:
            h = have.get(i, bank.get(i, 0))
            kind = ("prayer" if i == PRAYER_POINTS else "food" if i == setup.get("food")
                    else "ammo" if i == ammo else "rune" if i in runes else "item")
            runs_out, used = None, 0.0
            for s in steps:
                q = s.get("used", {}).get(i, 0)
                if q and used + q > h:
                    frac = (h - used) / q
                    runs_out = {"level": s["level"], "hours": sum(x["hours"] for x in steps[:steps.index(s)]) + frac * s["hours"]}
                    break
                used += q
            out.append({"id": i, "name": "Prayer points" if i == PRAYER_POINTS else self.w.name(i), "kind": kind,
                        "per_hr": totals[i] / hours, "total": totals[i], "have": h, "runs_out": runs_out})
        return out

    def _runes(self):
        if not hasattr(self, "_rune_ids"):
            self._rune_ids = {c["Item"] for s in calc.load(calc.TABLES / "objects" / "MagicSpell.json") for c in s["RuneCosts"]}
        return self._rune_ids

    # ------------------------------------------------------------ output
    @staticmethod
    def _add(into, more):
        for k, v in more.items():
            into[k] = into.get(k, 0) + v

    def _segments(self, steps):
        """Merge consecutive levels spent on the same action; counts become totals for the whole range."""
        segs = []
        for s in steps:
            if segs and segs[-1]["key"] == s["key"] and segs[-1]["outside_focus"] == s.get("outside_focus", False):
                g = segs[-1]
                g["to"] = s["level"] + 1
                for k in ("hours", "gp", "food_used", "kills"):
                    g[k] = g.get(k, 0) + s.get(k, 0)
                self._add(g["xp"], s["xp"])
                self._add(g["bought"], s["bought"])
                self._add(g.setdefault("from_bank", {}), s.get("from_bank", {}))
                self._add(g.setdefault("loot", {}), s.get("loot", {}))
                self._add(g.setdefault("used", {}), s.get("used", {}))
                by_name = {c["name"]: c for c in g["chain"]}
                for c in s["chain"]:
                    if c["name"] in by_name:
                        by_name[c["name"]]["times"] += c["times"]
                        self._add(by_name[c["name"]]["makes"], c["makes"])
                    else:   # the chain switched an ingredient source part-way
                        g["chain"].insert(len(g["chain"]) - 1, dict(c, makes=dict(c["makes"])))
                g["xp_hr_end"] = s["xp_hr"]
                if s.get("verdict") in ("runs_out", "eats"):
                    g["verdict"] = s["verdict"]
            else:
                segs.append(dict(s, **{"from": s["level"], "to": s["level"] + 1, "xp": dict(s["xp"]),
                                       "bought": dict(s["bought"]), "loot": dict(s.get("loot") or {}),
                                       "from_bank": dict(s.get("from_bank") or {}),
                                       "used": dict(s.get("used") or {}),
                                       "outside_focus": s.get("outside_focus", False),
                                       "chain": [dict(c, makes=dict(c["makes"])) for c in s["chain"]],
                                       "xp_hr_start": s["xp_hr"], "xp_hr_end": s["xp_hr"]}))
        for g in segs:
            g.pop("level", None)
        return segs

    def _result(self, skill, cur, target, steps, start_levels, xp, kind, stuck=None, focus=None):
        segs = self._segments(steps)
        for g in segs:
            g["xp_hr"] = g["xp"].get(skill, 0) / g["hours"] if g["hours"] else 0
        end_levels = self._levels(xp)
        gained = [{"skill": k, "from": start_levels.get(k, 1), "to": v}
                  for k, v in end_levels.items() if v > start_levels.get(k, 1) and k != skill]
        total_xp = {}
        for s in steps:
            for k, v in s["xp"].items():
                total_xp[k] = total_xp.get(k, 0) + v
        notes = []
        outside = [g for g in segs if g["outside_focus"]]
        if outside:
            lv = ", ".join(f"{g['from']}-{g['to']}" for g in outside)
            notes.append(f"Nothing in your focus is unlocked at levels {lv}, so the best action overall is used there "
                         "until something in the focus unlocks.")
        notes.append("Mastery, pool and other bonuses you would earn on the way are not added, so real times "
                     "should be a little shorter.")
        for i, g in enumerate(segs, 1):
            if g.get("gp", 0) < -50_000:
                notes.append(f"Top up: step {i} ({g['name']}) spends about {-g['gp']:,.0f} gp more than it earns, mostly "
                             "in shop purchases. Tick 'Never buy from the shop' to plan without them.")
        if skill == "Herblore":
            notes.append("Herbs come from Farming, which grows on its own: they are counted as free here, so keep "
                         "enough planted (or use the bank option).")
        made, bought, loot, from_bank = {}, {}, {}, {}
        for g in segs:
            for c in g["chain"]:
                if c["final"]:
                    self._add(made, c["makes"])
            self._add(bought, g["bought"])
            self._add(loot, g.get("loot") or {})
            self._add(from_bank, g.get("from_bank") or {})
        top = lambda d: [{"item": k, "qty": v} for k, v in sorted(d.items(), key=lambda kv: -kv[1]) if v >= 0.5]
        return {
            "kind": kind, "skill": skill, "from": cur, "to": target, "reached": steps[-1]["level"] + 1 if steps else cur,
            "xp_needed": XP[target] - self.w.state["skills"][skill]["xp"],
            "hours": sum(s["hours"] for s in steps), "gp": sum(s["gp"] for s in steps),
            "segments": segs, "other_levels": sorted(gained, key=lambda x: x["skill"]), "xp_total": total_xp,
            "made": top(made), "bought": top(bought), "loot": top(loot), "from_bank": top(from_bank),
            "kills": sum(s.get("kills", 0) for s in steps),
            "focus": self.focus_label(skill, focus) if focus else None,
            "stuck": stuck, "notes": notes,
        }

    def _stuck(self, skill, cur, target, lvl, steps, start_levels, xp, focus, why):
        kind = "combat" if skill in COMBAT_SKILLS else "skill"
        return self._result(skill, cur, target, steps, start_levels, xp, kind, stuck=why, focus=focus)
