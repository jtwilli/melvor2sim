"""Gold from combat drops: for each rich enemy, the gear tier and level each style (melee, ranged, magic)
needs to farm it safely, and the gold per hour there.

A "tier" is a level L with standard gear for one style: the best weapon (and ammo) and armour you can make
with Smithing, Fletching, Crafting or Runecrafting whose requirements are at most L. The style's skills,
Defence and Hitpoints are set to L (or yours, if higher); everything else (bonuses, food) is your save's.
"""
from collections import defaultdict

import calculator as calc
import combat as cmb

NS = cmb.NS
HOUR = 3_600_000
TIERS = (10, 20, 30, 40, 50, 60, 70, 80, 90, 99)
STYLE_SKILLS = {"Melee": ("Attack", "Strength", "Defence", "Hitpoints"),
                "Ranged": ("Ranged", "Defence", "Hitpoints"),
                "Magic": ("Magic", "Defence", "Hitpoints")}
ARMOUR_SKILL = {"Melee": None, "Ranged": "Ranged", "Magic": "Magic"}   # besides Defence
ARMOUR_SLOTS = ("Helmet", "Torso", "Legs", "Boots", "Gloves", "Offhand")
MAKERS = {"Smithing", "Fletching", "Crafting", "Runecrafting"}
SCAN_KILLS = 15          # quick scan of every enemy at the top tier
PICK_KILLS = 15          # choosing the weapon and stance for a tier
CURVE_KILLS = 30         # each enemy at each tier
CANDIDATES = 15          # richest enemies whose tier curves are worked out


class CombatGP:
    def __init__(self, world, actions):
        self.w = world
        it = world.items
        self.weapon_types = cmb._objects(calc.TABLES, "WeaponType")
        self.stances = cmb._objects(calc.TABLES, "AttackStance")
        made = defaultdict(list)                    # item -> [(skill, level)]
        for a in actions:
            if a["category"] and "Quest" in a["category"]:
                continue
            if a["skill"] in MAKERS:
                for o in a["outputs"]:
                    made[o["item"]].append((a["skill"], a["level_req"] or 1))
        self.made = made
        self.gear = {s: {"weapons": [], "ammo": [], "armour": defaultdict(list)} for s in STYLE_SKILLS}
        for iid, item in it.items():
            caps = item.get("Capabilities") or {}
            eq = caps.get(NS + "Equipment")
            if not eq or iid not in made:
                continue
            reqs = {cmb.short(r["Data"]["Component"]["Entity"]): r["Data"]["Level"]
                    for r in eq.get("Requirements") or [] if cmb.short(r["Type"]) == "Level"}
            if any(cmb.short(r["Type"]) != "Level" for r in eq.get("Requirements") or []):
                continue
            level, skills = max(reqs.values(), default=1), set(reqs)
            slot = cmb.short(eq["Type"])
            if NS + "Weapon" in caps:
                wt = self.weapon_types.get(caps[NS + "Weapon"]["Type"]) or {}
                st = self.stances.get((wt.get("Stances") or [None])[0]) or {}
                style = cmb.short(st.get("CombatStyle"))
                if style in STYLE_SKILLS and skills <= set(STYLE_SKILLS[style]):
                    self.gear[style]["weapons"].append((level, iid, caps[NS + "Weapon"], eq))
            elif slot == "Ammo" and skills <= {"Ranged"}:
                self.gear["Ranged"]["ammo"].append((level, iid, eq.get("AmmoType")))
            elif slot in ARMOUR_SLOTS and "Defence" in skills:
                for style, extra in ARMOUR_SKILL.items():
                    if skills == ({"Defence", extra} if extra else {"Defence"}):
                        self.gear[style]["armour"][slot].append((level, iid, eq))
        self._cache = None

    # ------------------------------------------------------------ gear for one tier
    @staticmethod
    def _stat_sum(eq):
        st = eq.get("Stats") or {}
        return (sum(x["Value"] for x in st.get("Stats") or [] if x["Value"] > 0 and "Interval" not in x["Stat"])
                + sum(x["Value"] for x in (st.get("PowerRatings") or []) + (st.get("ProtectionRatings") or [])))

    def _armour(self, style, L):
        out = {}
        for slot, items in self.gear[style]["armour"].items():
            ok = [x for x in items if x[0] <= L]
            if ok:
                best = max(ok, key=lambda x: (x[0], self._stat_sum(x[2])))
                out[slot] = best[1]
        return out

    def _ammo(self, ammo_type, L):
        ok = [x for x in self.gear["Ranged"]["ammo"] if x[2] == ammo_type and x[0] <= L]
        return max(ok)[1] if ok else None

    def _weapon_options(self, style, L):
        """The highest-level weapon of each weapon type usable at L."""
        best = {}
        for lv, iid, wcap, eq in self.gear[style]["weapons"]:
            if lv > L:
                continue
            t = wcap["Type"]
            if t not in best or (lv, self._stat_sum(eq)) > (best[t][0], self._stat_sum(best[t][3])):
                best[t] = (lv, iid, wcap, eq)
        return list(best.values())

    def _setup(self, style, L, weapon, food):
        _, iid, wcap, eq = weapon
        equipment = {s: {"item": i, "qty": 1} for s, i in self._armour(style, L).items()}
        if NS + "Offhand" in (eq.get("OccupiesSlots") or []) or "2H" in iid:
            equipment.pop("Offhand", None)
        equipment["Weapon"] = {"item": iid, "qty": 10 ** 6 if eq.get("CanEquipQuantity") else 1}
        if wcap.get("RequiredAmmoType"):
            ammo = self._ammo(wcap["RequiredAmmoType"], L)
            if not ammo:
                return None
            equipment["Ammo"] = {"item": ammo, "qty": 10 ** 6}
        return {"name": f"{style} {L}", "worn": False, "loadout": None, "equipment": equipment, "stance": None,
                "spell": None, "prayers": [], "food": food, "food_qty": 10 ** 6, "levels": {}}

    def _engine(self, style, L, setup, kills):
        w = self.w
        cur = {k: v["level"] for k, v in w.state["skills"].items()}
        st = cmb.with_levels(w.state, {s: max(cur.get(s, 1), L) for s in STYLE_SKILLS[style]})
        eng = cmb.Combat(calc.TABLES, st, calc.Requirements(st), w.items, w.active, w.xp_mult, setup,
                         setup["name"], kills=kills)
        eng.area_ok = lambda area: True             # area unlocks are reported separately, not required here
        eng.enemy_playable = lambda e: not any(cmb.short(r["Type"]) == "DemoLocked" for r in e.get("Requirements") or [])
        return eng

    def _gp(self, r):
        """Gold per hour after the supplies a fight uses (food, ammo, runes at their sell price)."""
        kph = HOUR / r["ms"]
        loot = sum(q * self.w.price(i) for i, q in r["outputs"].items()) + r["currency"].get(NS + "GoldPieces", 0)
        uses = sum(n * self.w.price(i) for i, n in r["inputs"].items())
        return (loot - uses) * kph

    def _tier(self, style, L, ref, food):
        """(setup, stance) for a tier: the weapon type and stance that kill the reference enemy fastest."""
        best = None
        for wpn in self._weapon_options(style, L):
            setup = self._setup(style, L, wpn, food)
            if not setup:
                continue
            eng = self._engine(style, L, setup, PICK_KILLS)
            if not eng.weapon:
                continue
            for sid in eng.stance_options():
                r = eng.fight(ref[0], ref[1], sid, survival=False)
                if r and (best is None or r["ms"] < best[0]):
                    best = (r["ms"], setup, sid)
        return (best[1], best[2]) if best else (None, None)

    # ------------------------------------------------------------ main
    def curves(self, food):
        """{enemy id: {area, name, styles: {style: {tier: row}}}} for the richest enemies; cached per save."""
        key = (id(self.w.state), food)
        if self._cache and self._cache[0] == key:
            return self._cache[1]
        # 1. quick scan of every enemy at the top tier, each style with a default weapon
        ref = None
        rich = {}
        tiers = {}
        for style in STYLE_SKILLS:
            opts = self._weapon_options(style, 99)
            setup = next((s for s in (self._setup(style, 99, o, food) for o in sorted(opts, key=lambda o: -o[0])) if s),
                         None)
            if not setup:
                continue
            eng = self._engine(style, 99, setup, SCAN_KILLS)
            fights, _ = eng.rates(None, [eng.stance_options()[0]], survival=False)
            for r in fights.values():
                g = self._gp(r)
                eid, aid = r["combat"]["enemy_id"], r["combat"]["area_id"]
                if g > rich.get(eid, (0,))[0]:
                    rich[eid] = (g, aid, r["enemy"], r["area"])
        top = sorted(rich.items(), key=lambda kv: -kv[1][0])[:CANDIDATES]
        if not top:
            self._cache = (key, {})
            return {}
        ref = (top[0][1][1], top[0][0])            # weapons are picked against the richest enemy
        out = {eid: {"area_id": v[1], "name": v[2], "area": v[3], "styles": {}} for eid, v in top}
        # 2. each tier's gear, then every candidate enemy at each tier (top down; stop after two failures)
        for style in STYLE_SKILLS:
            for L in TIERS:
                tiers[(style, L)] = self._tier(style, L, ref, food)
            for eid, info in out.items():
                rows, misses = {}, 0
                for L in reversed(TIERS):
                    setup, sid = tiers[(style, L)]
                    r = None
                    if setup:
                        eng = self._engine(style, L, setup, CURVE_KILLS)
                        r = eng.fight(info["area_id"], eid, sid, survival=True)
                    safe = bool(r) and r["combat"]["survival"]["verdict"] != "death"
                    rows[L] = dict(gp=self._gp(r), kills=HOUR / r["ms"], safe=safe,
                                   weapon=self.w.name(setup["equipment"]["Weapon"]["item"]),
                                   stance=self.stances[sid]["DisplayName"],
                                   loot=sorted(((q * HOUR / r["ms"] * self.w.price(i), self.w.name(i))
                                                for i, q in r["outputs"].items()), reverse=True)[:3]) if r else None
                    misses = 0 if safe else misses + 1
                    if misses >= 2:
                        break
                info["styles"][style] = rows
        data = {"enemies": out, "tiers": {f"{s}|{L}": self._describe(*tiers[(s, L)]) for s, L in tiers}}
        self._cache = (key, data)
        return data

    def _describe(self, setup, sid):
        if not setup:
            return None
        eq = setup["equipment"]
        made = defaultdict(int)
        for s in eq.values():
            easiest = {}
            for sk, lv in self.made.get(s["item"], []):
                easiest[sk] = min(easiest.get(sk, lv), lv)
            for sk, lv in easiest.items():
                made[sk] = max(made[sk], lv)
        armour = [self.w.name(eq[s]["item"]) for s in ARMOUR_SLOTS if s in eq]
        return {"weapon": self.w.name(eq["Weapon"]["item"]),
                "ammo": self.w.name(eq["Ammo"]["item"]) if "Ammo" in eq else None,
                "stance": self.stances[sid]["DisplayName"], "armour": armour,
                "made_with": dict(made)}
