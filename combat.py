"""Combat simulation for calculator.py and sim_server.py.

A seeded simulation of the game's fight rules (docs/FORMULAS.md, Combat):
stat modifiers from gear, set bonuses, prayers, food, stance, combat triangle and
elemental bonuses; attack selection incl. special attacks and multi-hits; hit roll,
damage roll, crits, resistance, ward, armour and armour-break; stuns, damage over
time, turn-counting effects and end-of-turn healing; auto-eat and death; XP per
point of damage plus kill bonus, Prayer XP and Slayer-task XP.

Everything a fight depends on is a "setup" (see setup_from_state), so the same
engine runs your saved loadouts and any what-if setup from the simulator page.

Not simulated (named on the rows they affect): stacking / charging / counting /
random-magnitude effects, lifesteal and reflect, modifiers with conditions.
"""
import copy
import functools
import json
import math
import random
import zlib
from pathlib import Path

NS = "MelvorBase:"
SPAWN_MS = 3000
KILLS = 300                 # simulated kills per fight
FIGHT_CAP_MS = 900_000      # a single kill taking longer than this is "not viable"
SURVIVE_CAP_MS = 4 * 3_600_000
COMBAT_LEVELS = ["Attack", "Strength", "Defence", "Hitpoints", "Ranged", "Magic", "Prayer", "Slayer"]


def short(i):
    return i[len(NS):] if isinstance(i, str) and i.startswith(NS) else i


@functools.lru_cache(maxsize=None)
def _objects(tables, name):
    """Game objects by id. Cached: the simulator builds many engines and never edits these."""
    path = Path(tables) / "objects" / f"{name}.json"
    if not path.exists():
        return {}
    return {NS + o["Id"]: o for o in json.loads(path.read_text(encoding="utf-8"))}


def norm_mod(b):
    """Game bonus dict -> {stat, type, value, ctx, cond}."""
    ctx = {k: v for k, v in (b.get("LocalContext") or {}).items() if k != "Component" and v is not None}
    return {"stat": b["Stat"]["Name"], "type": short(b["Stat"].get("StatsType")), "value": b["Value"], "ctx": ctx,
            "cond": bool(b.get("Condition") or b.get("Scaling") or b.get("FromContext"))}


def total(mods, stat, **q):
    """Sum of modifier values for `stat` whose context matches the query."""
    t = 0.0
    for m in mods:
        if m["stat"] != stat:
            continue
        for k, v in m["ctx"].items():
            if q.get(k) != v:
                break
        else:
            t += m["value"]
    return t


# ---------------------------------------------------------------- setups

def loadout_ids(state):
    lo = state.get("loadouts") or {}
    ids = sorted(set(lo.get("slots") or {}) | set(state.get("equipment_loadouts") or {}))
    return [i for i in ids if (lo.get("slots") or {}).get(i, {}).get("unlocked", True)] or [None]


def setup_from_state(state, loadout_id=None):
    """Everything one fight depends on, as saved for a loadout (None or the worn one = what you wear now)."""
    lo = state.get("loadouts") or {}
    current = lo.get("current")
    is_current = loadout_id is None or loadout_id == current
    equipment = state["equipment"] if is_current else (state.get("equipment_loadouts") or {}).get(loadout_id, {})
    pr = state.get("prayers") or {}
    if is_current:
        prayers = [v["active"]["prayer"] for v in (pr.get("slots") or {}).values() if v.get("active") and v["active"].get("state")]
    else:
        prayers = list(((pr.get("loadouts") or {}).get(loadout_id) or {}).values())
    sp = state.get("spells") or {}
    spell = ((sp.get("loadouts") or {}).get(loadout_id) or {}).get("spell") or (sp.get("spell") if is_current else None)
    fd = state.get("food") or {}
    food = (fd.get("loadouts") or {}).get(loadout_id) or (fd.get("food") if is_current else None) or fd.get("food")
    stance = (state.get("loadout_stances") or {}).get(loadout_id)
    name = ((lo.get("slots") or {}).get(loadout_id) or {}).get("name") or loadout_id or "Equipped"
    return {
        "loadout": loadout_id, "name": name, "worn": is_current,
        "equipment": {k: dict(v) for k, v in equipment.items()},
        "stance": stance, "spell": spell, "prayers": prayers,
        "food": food, "food_qty": (state.get("bank") or {}).get(food, 0) if food else 0,
        "levels": {},
    }


def with_levels(state, levels):
    """A copy of the save state with some skill levels overridden (for what-if setups)."""
    if not levels:
        return state
    st = copy.copy(state)
    st["skills"] = {k: dict(v) for k, v in state["skills"].items()}
    for k, v in levels.items():
        st["skills"].setdefault(k, {"xp": 0})["level"] = int(v)
    return st


# ---------------------------------------------------------------- fighters

class Side:
    """One fighter: base numbers plus everything needed to derive stats under a set of active effects."""

    def __init__(self, engine, name, is_player, stats, levels, power, protection, mods, stance, element,
                 damage_type, attacks, triggers, opponent_stance=None):
        self.eng, self.name, self.is_player = engine, name, is_player
        self.stats, self.levels, self.power, self.protection = stats, levels, power, protection
        self.mods, self.stance, self.element, self.damage_type = mods, stance, element, damage_type
        self.style = engine.styles[stance["CombatStyle"]]
        self.attacks, self.triggers = attacks, triggers
        self.q = {"CombatStyle": stance["CombatStyle"], "AttackStance": NS + stance["Id"], "Element": element}
        self._cache = {}
        self.opp_stance = opponent_stance
        self.opp_damage_type = None
        self.hp = None
        self.reset()

    def reset(self, keep_hp=False):
        self.effects = {}
        self.stun = 0
        self.stun_effect = None
        self.dots = {}
        self.eot_heal = None
        self.broken = False
        self.last_dealt = 0
        s = self.snap()
        if not keep_hp or self.hp is None:
            self.hp = s["hp"]
        self.ward = s["ward"]

    def snap(self):
        key = tuple(sorted(self.effects))
        s = self._cache.get(key)
        if s is None:
            s = self._cache[key] = self._derive(key)
        return s

    def _derive(self, effect_ids):
        eng, q = self.eng, self.q
        mods = self.mods + [m for eid in effect_ids for m in eng.effect_mods.get(eid, [])]
        T = lambda stat, **extra: total(mods, stat, **{**q, **extra})

        def stat(sid):
            v = (self.stats.get(sid, 0) + T("FlatCombatStatValue", CombatStat=sid)) * (1 + T("PercentCombatStatValue", CombatStat=sid))
            info = eng.combat_stats.get(sid, {})
            if not info.get("IsPercent"):
                v = round(v)
            if not info.get("AllowNegative", True):
                v = max(0.0, v)
            return v

        level = lambda lid: int(self.levels.get(lid, 1) + T("FlatCombatLevel", CombatLevel=lid))
        lvl = lambda ratios: sum(level(r["Level"]) * r["Ratio"] for r in ratios)
        style = self.style
        dark = 1 - 0.05 * max(0, min(20, eng.darkness if self.is_player else 0))

        f = style["MaxHitFormula"]
        d = f["Data"]
        L = lvl(d["Levels"])
        if short(f["Type"]) == "StrengthBonus":
            base_max = math.floor(22 + L + (L + 17) * stat(d["StrengthBonusStat"]) / 64)
        elif short(f["Type"]) == "DamageBonus":
            base_max = math.floor(stat(d["MaxHitStat"]) * (1 + stat(d["DamageBonusStat"])) * (1 + (L + 1) / 200))
        else:
            base_max = 0
        max_hit = max(1, math.floor((base_max * (1 + T("PercentMaxHit")) + T("FlatMaxHit")) * (1 + T("MaxHitCombatTriangle"))))
        min_hit = max(1, min(max_hit, math.floor(1 + T("FlatMinHit") + max_hit * T("MinHitBasedOnMaxHit"))))

        base_acc = math.floor((lvl(style["AccuracyRatingLevels"]) + 9) * (stat(self.stance["AccuracyStat"]) + 64))
        acc = max(1, int(math.floor(base_acc * (1 + T("PercentAccuracyRating"))) * dark))
        eva = 1
        if self.opp_stance:
            ostyle = eng.styles[self.opp_stance["CombatStyle"]]
            base_eva = math.floor((lvl(ostyle["EvasionLevels"]) + 9) * (stat(self.opp_stance["EvasionStat"]) + 64))
            eva = max(1, int(math.floor(base_eva * (1 + T("PercentEvasionRating")) + T("FlatEvasionRating")) * dark))

        interval = max(250.0, math.floor(int(stat(style["AttackIntervalStat"])) * (1 + T("PercentAttackInterval")) + T("FlatAttackInterval")))
        dt = self.damage_type
        power = max(1, int((self.power.get(dt, 0) + T("FlatPowerRating", DamageType=dt)) * (1 + T("PercentPowerRating", DamageType=dt))))
        odt = self.opp_damage_type or dt
        prot = max(0, int((self.protection.get(odt, 0) + T("FlatProtectionRating", DamageType=odt)) * (1 + T("PercentProtectionRating", DamageType=odt))))
        hp = max(1, math.floor(level(style["MaxHitpointsLevel"]) * 10 * (1 + T("PercentMaxHitpoints")) + T("FlatMaxHitpoints")))
        return dict(
            max_hit=max_hit, min_hit=min_hit, acc=acc, eva=eva, interval=interval, power=power, prot=prot, hp=hp,
            armour=max(0, int(stat(style["ArmourStat"]))), ward=max(0, int(stat(style["MaxWardStat"]))),
            crit=max(0.0, min(1.0, T("FlatCriticalHitChance"))), crit_mult=max(1.0, 1.5 + T("FlatCriticalHitMultiplier")),
            dealt=T("PercentDamageDealt"), taken=T("PercentDamageTaken"),
            res_flat=T("FlatResistance"), res_pct=T("PercentResistance"), res_tri=T("ResistanceCombatTriangle"),
            hit_flat=T("FlatHitChance"), negate=max(0.0, min(1.0, T("AttackDamageNegationChance"))), mods=mods)

    def avoid(self, effect_id):
        return total(self.snap()["mods"], "AvoidanceChance", **{**self.q, "Effect": effect_id})


def hit_chance(a, d):
    acc, eva = a["acc"], d["eva"]
    p = 1 - eva / 2 / acc if acc >= eva else acc / 2 / eva
    return max(0.0, min(1.0, p + a["hit_flat"]))


def resist(a, d):
    base = d["prot"] / (a["power"] + d["prot"]) if d["prot"] > 0 else 0.0
    return max(0.0, min(1.0, max(0.0, min(0.7, (base + d["res_flat"]) * (1 + d["res_pct"]))) * (1 + d["res_tri"])))


class Fight:
    """Repeated kills of one enemy. Follows the game's attack and auto-eat rules.
    Player HP carries over between kills (the game has no natural regeneration)."""

    def __init__(self, engine, player, enemy, rng, food=None, food_left=math.inf):
        self.eng, self.p, self.e, self.rng = engine, player, enemy, rng
        self.food, self.food_left = food, food_left
        self.unsim = set()
        self.dmg_xp = 0
        self.prayer_xp = 0.0
        self.taken = 0.0
        self.eaten = 0
        self.deaths = 0
        self.died = False
        self.p_turns = self.e_turns = self.stunned_turns = 0
        self.casts = 0

    # -- damage --
    def roll_formula(self, fdef, who):
        f, s = fdef["Formula"], who.snap()
        kind, pct = short(f["Type"]), f["Data"].get("Percent", 1)
        if kind == "NormalDamage":
            lo = int(s["min_hit"] * pct)
            return self.rng.randint(lo, max(lo, int(s["max_hit"] * pct)))
        if kind == "MaxHit":
            return int(s["max_hit"] * pct)
        if kind == "CurrentHitpoints":
            return int(who.hp * pct)
        if kind == "MaxHitpoints":
            return int(s["hp"] * pct)
        self.unsim.add(f"damage formula {kind}")
        return 0

    def modify(self, raw, src, tgt, attack=True):
        a, d = src.snap(), tgt.snap()
        num = raw * (1 + a["dealt"]) * (1 + d["taken"]) * (1 - resist(a, d))
        if attack and d["ward"] > 0:
            num *= 1 - tgt.ward / d["ward"]
        if not tgt.broken:
            num -= d["armour"]
        return int(max(num, 1.0))

    def damage(self, tgt, amount):
        if not tgt.is_player:
            dealt = min(amount, tgt.hp)
            tgt.hp -= dealt
            return dealt
        self.taken += amount
        if amount >= tgt.hp:
            # death; the run would stop here. Revive so the kill rate can still be measured.
            self.deaths += 1
            self.died = True
            tgt.hp = tgt.snap()["hp"]
            return amount
        tgt.hp -= amount
        self.auto_eat(tgt)
        return amount

    def auto_eat(self, p):
        f = self.food
        if not f or self.food_left <= 0:
            return
        max_hp = p.snap()["hp"]
        if p.hp > round(f["threshold"] * max_hp):
            return
        n = max(1, math.floor((f["limit"] * max_hp - p.hp) / f["heal"]))
        n = int(min(n, self.food_left))
        p.hp = min(max_hp, p.hp + n * f["heal"])
        if self.rng.random() >= f["keep"]:
            self.eaten += n
            self.food_left -= n

    # -- effects --
    def apply(self, cfg, owner, other, applies_to, t):
        tgt = owner if applies_to == "Self" else other
        src = owner
        kind, data = short(cfg["Type"]), cfg["Data"]
        eid = data.get("Effect")
        if self.rng.random() < tgt.avoid(eid):
            return
        conf = data.get("Configuration") or {}
        eng = self.eng
        if kind == "Stun":
            if tgt.stun > 0:
                return
            eff = eng.stun_effects.get(eid, {})
            tgt.stun = conf.get("Turns") or eff.get("DefaultTurns", 1)
            tgt.stun_effect = eid
            tgt.effects[eid] = 10 ** 6
            tgt.next = t + tgt.snap()["interval"]
        elif kind == "TurnCounting":
            eff = eng.turn_effects.get(eid, {})
            tgt.effects[eid] = conf.get("Turns") or eff.get("DefaultTurns", 1)
        elif kind == "DamageOverTime":
            if eid in tgt.dots:
                return
            eff = eng.dot_effects.get(eid)
            if not eff:
                self.unsim.add(f"effect {short(eid)}")
                return
            if "PercentDamageDealtPerProc" in eff:
                per = max(1, int(src.last_dealt * eff["PercentDamageDealtPerProc"]))
            else:
                per = self.modify(max(1, self.roll_formula(eff["DamagePerProc"], src)), src, tgt, attack=False)
            tgt.dots[eid] = [t + eff["ProcInterval"], eff["ProcInterval"], eff["ProcCount"], per]
        elif kind == "EndOfTurnHealing":
            eff = eng.heal_effects.get(eid)
            if eff:
                tgt.eot_heal = eff["HealingPerTurn"]
        elif kind == "EndOfTurnDamage":
            eff = eng.eot_damage_effects.get(eid)
            if eff:
                self.damage(tgt, self.modify(max(1, self.roll_formula(eff["DamagePerTurn"], src)), src, tgt, attack=False))
        else:
            self.unsim.add(f"{kind} effect {short(eid)}")

    def fire(self, owner, other, trig, trig_type, t):
        for tr in owner.triggers:
            if tr["trigger"] == trig and (trig_type is None or tr["type"] in (None, trig_type)):
                if self.rng.random() < tr["chance"]:
                    self.apply(tr["effect"], owner, other, tr["applies_to"], t)

    def end_turn(self, c, o, t):
        for eid in [k for k in c.effects if k != c.stun_effect]:
            c.effects[eid] -= 1
            if c.effects[eid] <= 0:
                del c.effects[eid]
                rem = self.eng.turn_effects.get(eid, {}).get("RemovalEffect")
                if rem:
                    self.apply(rem, c, o, "Self", t)
        if c.eot_heal and not c.is_player:
            c.hp = min(c.snap()["hp"], c.hp + self.roll_formula(c.eot_heal, c))

    def hit(self, c, o, atk, hg, t):
        for ef in atk.get("PreHitEffects") or []:
            if self.rng.random() < ef["Chance"]:
                self.apply(ef["Effect"], c, o, ef["AppliesTo"], t)
        a, d = c.snap(), o.snap()
        landed = (not atk.get("IsAvoidable", True)) or self.rng.random() < hit_chance(a, d)
        c.last_dealt = 0
        if not landed:
            return
        raw = sum(self.roll_formula(dm, c if dm["Character"] == "Attacker" else o) for dm in hg["Damage"])
        if a["crit"] and self.rng.random() < a["crit"]:
            raw = int(raw * a["crit_mult"])
        if d["negate"] and self.rng.random() < d["negate"]:
            raw = 0
        if raw > 0:
            mod = self.modify(raw, c, o)
            dealt = self.damage(o, mod)
            c.last_dealt = dealt
            if c.is_player and dealt > 0:
                self.dmg_xp += dealt
                if self.eng.prayer_cost:
                    self.prayer_xp += math.sqrt(3 + 0.2 * dealt * self.eng.prayer_cost)
            if o.ward > 0:
                o.ward -= 1
            if d["armour"] > 0 and mod >= d["armour"]:
                o.broken = True
        for ef in atk.get("OnHitEffects") or []:
            if self.rng.random() < ef["Chance"]:
                self.apply(ef["Effect"], c, o, ef["AppliesTo"], t)
        self.fire(c, o, "OnAttack", "OnHit", t)
        self.fire(o, c, "WhenAttacked", "WhenHit", t)

    def turn(self, c, o, t):
        if c.is_player:
            self.p_turns += 1
        else:
            self.e_turns += 1
        if c.stun > 0:
            if c.is_player:
                self.stunned_turns += 1
            c.stun -= 1
            self.end_turn(c, o, t)
            if c.stun <= 0:
                eid, c.stun_effect = c.stun_effect, None
                c.effects.pop(eid, None)
                rem = self.eng.stun_effects.get(eid, {}).get("RemovalEffect")
                if rem:
                    self.apply(rem, c, o, "Self", t)
            c.next = t + c.snap()["interval"]
            return None
        r, acc, atk = self.rng.random(), 0.0, c.attacks[-1][0]
        for cand, chance in c.attacks:
            acc += chance
            if r < acc:
                atk = cand
                break
        if c.is_player:
            self.casts += 1
        first = True
        for hg in atk["Hits"]:
            for _ in range(hg["Count"]):
                if not first:
                    t += hg["Interval"]
                first = False
                self.hit(c, o, atk, hg, t)
                if self.e.hp <= 0:
                    return t
        self.end_turn(c, o, t)
        c.next = t + c.snap()["interval"]
        return None

    def kill(self):
        """Simulate one kill; returns fight time in ms or None if it takes longer than FIGHT_CAP_MS."""
        p, e = self.p, self.e
        p.reset(keep_hp=True)
        e.reset()
        p.next, e.next = p.snap()["interval"], e.snap()["interval"]
        self.fire(p, e, "AtStartOfFight", None, 0)
        self.fire(e, p, "AtStartOfFight", None, 0)
        while True:
            t_dot, dot_owner, dot_id = math.inf, None, None
            for side in (e, p):
                for eid, dv in side.dots.items():
                    if dv[0] < t_dot:
                        t_dot, dot_owner, dot_id = dv[0], side, eid
            t = min(p.next, e.next, t_dot)
            if t > FIGHT_CAP_MS:
                return None
            if t == t_dot and t_dot <= min(p.next, e.next):
                dv = dot_owner.dots[dot_id]
                self.damage(dot_owner, dv[3])
                dv[0] += dv[1]
                dv[2] -= 1
                if dv[2] <= 0:
                    del dot_owner.dots[dot_id]
                if e.hp <= 0:
                    died = t
                    break
                continue
            c, o = (p, e) if p.next <= e.next else (e, p)
            died = self.turn(c, o, t)
            if died is not None:
                break
        self.fire(e, p, "OnDeath", None, died)
        for dv in list(p.dots.values()):     # damage over time still ticking on you after the kill
            for _ in range(dv[2]):
                self.damage(p, dv[3])
        p.dots.clear()
        return died


class Combat:
    """Fights for one setup (gear, stance, spell, prayers, food, level overrides)."""

    def __init__(self, tables, state, reqs, items, active_bonuses, xp_multiplier, setup=None, label=None):
        setup = setup or setup_from_state(state)
        self.setup = setup
        self.state, self.reqs, self.items, self.xp_mult = state, reqs, items, xp_multiplier
        self.label = label if label is not None else setup.get("name")
        O = lambda n: _objects(tables, n)
        self.areas, self.enemies, self.stances = O("CombatArea"), O("Enemy"), O("AttackStance")
        self.styles, self.spells, self.weapon_types = O("CombatStyle"), O("MagicSpell"), O("WeaponType")
        self.attacks, self.passives, self.combat_stats = O("Attack"), O("CombatPassive"), O("CombatStat")
        self.prayers, self.masters = O("Prayer"), O("SlayerMaster")
        self.triangle = {(t["PlayerStyle"], t["EnemyStyle"]): [norm_mod(b) for b in t["Bonuses"]["Component"]]
                         for t in O("CombatTriangleBonus").values()}
        self.elemental = {(t["PlayerElement"], t["EnemyElement"]): [norm_mod(b) for b in t["Bonuses"]["Component"]]
                          for t in O("ElementalBonus").values()}
        self.stun_effects, self.turn_effects = O("StunEffect"), O("TurnCountingEffect")
        self.dot_effects = {**O("DamageOverTimeEffect"), **O("BleedEffect")}
        self.heal_effects, self.eot_damage_effects = O("EndOfTurnHealingEffect"), O("EndOfTurnDamageEffect")
        self.effect_mods = {eid: [norm_mod(b) for b in (eff.get("Modifiers") or [])]
                            for table in (self.stun_effects, self.turn_effects) for eid, eff in table.items()}
        self.levels = {NS + k: v["level"] for k, v in state["skills"].items()}
        for k, v in (setup.get("levels") or {}).items():
            self.levels[NS + k] = int(v)
        self.darkness = 0

        # ---- gear ----
        equipment = setup.get("equipment") or {}
        worn = {}
        for slot, s in equipment.items():
            if not s or not s.get("item"):
                continue
            if slot != "Weapon" and s["item"] == (equipment.get("Weapon") or {}).get("item"):
                continue  # two-handed weapon also shown in the off-hand slot
            worn[slot] = s
        self.stats, self.power, self.protection = {}, {}, {}
        self.weapon = self.weapon_type = self.ammo = None
        self.weapon_cap = {}
        mods, weapon_attacks = [], []
        for slot, s in worn.items():
            caps = self.items.get(s["item"], {}).get("Capabilities") or {}
            eq = caps.get(NS + "Equipment") or {}
            st = eq.get("Stats") or {}
            for x in st.get("Stats") or []:
                self.stats[x["Stat"]] = self.stats.get(x["Stat"], 0) + x["Value"]
            for x in st.get("PowerRatings") or []:
                self.power[x["DamageType"]] = self.power.get(x["DamageType"], 0) + x["Value"]
            for x in st.get("ProtectionRatings") or []:
                self.protection[x["DamageType"]] = self.protection.get(x["DamageType"], 0) + x["Value"]
            for b in (eq.get("Bonuses") or {}).get("Component") or []:
                if short(((b.get("LocalContext") or {}).get("Component") or {}).get("Entity")) == "Combat":
                    mods.append(norm_mod(b))
            weapon_attacks += [(a["Attack"], a["Chance"]) for a in eq.get("Attacks") or []]
            if NS + "Weapon" in caps:
                self.weapon, self.weapon_cap = s["item"], caps[NS + "Weapon"]
                self.weapon_type = self.weapon_types.get(self.weapon_cap["Type"])
            if slot == "Ammo":
                self.ammo = s["item"]
        self.weapon_attacks = weapon_attacks
        worn_ids = {s["item"] for s in worn.values()}
        for sb in O("EquipmentSetBonus").values():
            count = sum(1 for i in sb["Items"] if i in worn_ids)
            for tier in sb["Bonuses"]:
                if count >= tier["ItemCount"]:
                    mods += [norm_mod(b) for b in (tier["Modifiers"] or {}).get("Component") or []]

        # ---- bonuses that are not gear: mastery, astrology, shop, combat toolbelt ----
        for b in active_bonuses:
            if b.get("entity") != "Combat" or b.get("via", "").startswith("equipment:"):
                continue
            mods.append({"stat": b["stat"], "type": b.get("stats_type"), "value": b.get("effective_value", b["value"]),
                         "ctx": dict(b.get("context") or {}),
                         "cond": bool(b.get("condition") or b.get("scaling") or b.get("from_context"))})

        # ---- prayers ----
        self.active_prayers, self.prayer_cost, self.prayer_per_turn = [], 0, 0
        for pid in setup.get("prayers") or []:
            p = self.prayers.get(pid)
            if not p:
                continue
            self.active_prayers.append(p["DisplayName"])
            mods += [norm_mod(b) for b in (p.get("Bonuses") or {}).get("Component") or []]
            cost = (p.get("Cost") or {}).get("Quantity", 0)
            self.prayer_cost += cost
            on = p.get("ConsumesOn") or {}
            if short(on.get("Type")) == "AttackTurn" and (on.get("Data") or {}).get("Character", {}).get("Component") == "Player":
                self.prayer_per_turn += cost

        # ---- food (its bonuses apply while you have it) ----
        self.food_item = setup.get("food")
        self.food_qty = setup.get("food_qty", 0) or 0
        fcap = (self.items.get(self.food_item, {}).get("Capabilities") or {}).get(NS + "Food") if self.food_item else None
        if fcap and fcap.get("Bonuses"):
            mods += [norm_mod(b) for b in (fcap["Bonuses"].get("Modifiers") or {}).get("Component") or []]

        self.skipped_mods = sum(1 for m in mods if m["cond"])
        self.base_mods = [m for m in mods if not m["cond"]]
        self.loot = {k: total(self.base_mods, k) for k in ("ItemDoublingChance", "CurrencyDoublingChance", "FlatLight")}
        self.food = None
        if fcap:
            T = lambda stat: total(self.base_mods, stat) + total(self.base_mods, "Global" + stat)
            heal = max(1, int(fcap["BaseHealing"] * (1 + T("PercentHealing")) + T("FlatHealing")))
            eff = max(0.01, 1 + T("FlatAutoEatEfficiency"))
            self.food = {"item": self.food_item, "heal": max(1, int(heal * eff)),
                         "threshold": max(0.0, min(1.0, 0.25 + T("FlatAutoEatThreshold"))),
                         "limit": max(0.0, min(1.0, 1.0 + T("FlatAutoEatLimit"))),
                         "keep": max(0.0, min(1.0, T("FoodPreservationChance")))}

        self.saved_stance = setup.get("stance")
        if not self.saved_stance and self.weapon_type and setup.get("worn"):
            self.saved_stance = (state.get("weapon_stances") or {}).get(NS + self.weapon_type["Id"])
        self.saved_spell = setup.get("spell")

    # ------------------------------------------------------------ helpers
    def stance_options(self):
        return list(self.weapon_type["Stances"]) if self.weapon_type else []

    def pick_spell(self):
        sp = self.spells.get(self.saved_spell) if self.saved_spell else None
        if sp:
            return sp, True
        elements = set(self.weapon_cap.get("Elements") or [])
        best = None
        for s in self.spells.values():
            if s.get("AttackOverride") or (elements and s["Element"] not in elements):
                continue
            if not all(self.reqs.check_raw(r)[0] for r in s.get("UnlockRequirements") or []):
                continue
            mh = next((x["Value"] for x in s["Stats"]["Stats"] if x["Stat"] == NS + "SpellMaxHit"), 0)
            if best is None or mh > best[0]:
                best = (mh, s)
        return (best[1], False) if best else (None, False)

    def normalized(self, attack_chances):
        """NormalizeAttackChances: the default attack fills whatever chance is left."""
        merged = {}
        for aid, ch in attack_chances:
            merged[aid] = merged.get(aid, 0) + ch
        s = sum(merged.values())
        out = [(self.attacks[a], c if s < 1 else c / s) for a, c in merged.items() if a in self.attacks]
        if s < 1:
            out.append((self.attacks[NS + "Normal"], 1 - s))
        return out or [(self.attacks[NS + "Normal"], 1.0)]

    def triggers_from(self, modifier_sets, component):
        out = []
        for ms in modifier_sets:
            for c in (ms or {}).get("CombatEffect") or []:
                if (c.get("Character") or {}).get("Component") != component or c.get("Condition") or c.get("Scaling"):
                    continue
                m = c["Modifier"]
                out.append({"trigger": short(m["Trigger"]["Type"]), "type": (m["Trigger"].get("Data") or {}).get("Type"),
                            "effect": m["Effect"], "applies_to": m["AppliesTo"], "chance": c.get("Chance", 1)})
        return out

    def area_ok(self, area):
        checks = [self.reqs.check_raw(r)[0] for r in area.get("UnlockRequirements") or []]
        if any(c is False for c in checks):
            return False
        return None if any(c is None for c in checks) else True

    def enemy_playable(self, enemy):
        return all(self.reqs.check_raw(r)[0] is not False for r in enemy.get("Requirements") or [])

    def area_of(self, eid):
        for aid, area in self.areas.items():
            if eid in area["Enemies"] and short(area["Category"]) != "Quests":
                return aid
        return None

    # ------------------------------------------------------------ main
    def rates(self, only_enemies=None, stances=None):
        out, skipped = {}, {"locked area": 0, "unverified requirement": 0}
        if not self.weapon or not self.weapon_type:
            return out, {"no weapon equipped": 1}
        seen = set()
        not_viable = "fights not viable (kill takes over 15 minutes, or no usable attack)"
        for aid, area in self.areas.items():
            if short(area["Category"]) == "Quests" or not area["Enemies"]:
                continue
            ok = self.area_ok(area)
            if ok is False:
                skipped["locked area"] += 1
                continue
            if ok is None:
                skipped["unverified requirement"] += 1
                continue
            for eid in area["Enemies"]:
                enemy = self.enemies.get(eid)
                if not enemy or (eid, aid) in seen or (only_enemies is not None and eid not in only_enemies):
                    continue
                seen.add((eid, aid))
                if not self.enemy_playable(enemy):
                    continue
                for sid in stances or self.weapon_type["Stances"]:
                    r = self.fight(aid, eid, sid)
                    if r:
                        out[r["key"]] = r
                    else:
                        skipped[not_viable] = skipped.get(not_viable, 0) + 1
        return out, {k: v for k, v in skipped.items() if v}

    def fight(self, aid, eid, sid, survival=True):
        """One enemy, one stance: a rate dict (same shape as skilling actions) or None if not viable."""
        area, enemy = self.areas[aid], self.enemies[eid]
        stance = self.stances[sid]
        magic = short(stance["CombatStyle"]) == "Magic"
        stats, spell, spell_saved = dict(self.stats), None, False
        attack_list = list(self.weapon_attacks)
        if magic:
            spell, spell_saved = self.pick_spell()
            if not spell:
                return None
            for x in spell["Stats"]["Stats"]:
                stats[x["Stat"]] = stats.get(x["Stat"], 0) + x["Value"]
            if spell.get("AttackOverride"):
                attack_list = [(spell["AttackOverride"], 1.0)]
            element = spell["Element"]
        else:
            element = (self.weapon_cap.get("Elements") or [NS + "Neutral"])[0]
        if not stats.get(self.styles[stance["CombatStyle"]]["AttackIntervalStat"]):
            return None

        e_stance = self.stances[enemy["Stances"][0]["Stance"]]
        dmg_type = self.weapon_cap.get("DamageType") or NS + "Normal"
        mods = (self.base_mods + [norm_mod(b) for b in (stance.get("Bonuses") or {}).get("Component") or []]
                + self.triangle.get((stance["CombatStyle"], e_stance["CombatStyle"]), [])
                + self.elemental.get((element, enemy.get("Element")), []))
        passives = [self.passives[p]["Modifiers"] for p in enemy.get("Passives") or [] if p in self.passives]
        area_mods = [(area.get("Effect") or {}).get("Modifiers")] + [
            self.passives[p]["Modifiers"] for p in area.get("Passives") or [] if p in self.passives]
        e_mods = [norm_mod(b) for ms in passives + area_mods for b in (ms or {}).get("Component") or []
                  if ((b.get("LocalContext") or {}).get("Component") or {}).get("Component") == "Enemy"]

        self.darkness = int(area.get("Darkness", 0) - self.loot["FlatLight"])

        def build():
            player = Side(self, "player", True, stats, self.levels, self.power, self.protection, mods, stance, element,
                          dmg_type, self.normalized(attack_list), self.triggers_from(area_mods, "Player"), e_stance)
            foe = Side(self, "enemy", False, {x["Stat"]: x["Value"] for x in enemy["Stats"]["Stats"]},
                       {x["Key"]: x["Value"] for x in enemy["Levels"]},
                       {x["DamageType"]: x["Value"] for x in enemy["Stats"]["PowerRatings"]},
                       {x["DamageType"]: x["Value"] for x in enemy["Stats"]["ProtectionRatings"]},
                       e_mods, e_stance, enemy.get("Element"), enemy["DamageType"],
                       self.normalized([(a["Attack"], a["Chance"]) for a in enemy.get("Attacks") or []]),
                       self.triggers_from(passives + area_mods, "Enemy"), stance)
            player.opp_damage_type, foe.opp_damage_type = enemy["DamageType"], dmg_type
            player._cache.clear()
            foe._cache.clear()
            player.hp = None
            player.reset()
            foe.reset()
            return player, foe

        seed = zlib.crc32(f"{eid}|{sid}|{self.label}".encode())
        player, foe = build()
        sim = Fight(self, player, foe, random.Random(seed), self.food)   # unlimited food: measures eating
        total_ms, kills = 0.0, 0
        for _ in range(KILLS):
            ms = sim.kill()
            if ms is None:
                break
            total_ms += ms
            kills += 1
        if kills < KILLS:
            return None
        fight_ms = total_ms / kills
        ms = fight_ms + SPAWN_MS
        kills_hr = 3_600_000 / ms
        casts = sim.casts / kills
        ps, es = player.snap(), foe.snap()
        hp = es["hp"]

        # ---- survival ----
        food_per_kill = sim.eaten / kills
        deaths_per_hr = sim.deaths / kills * kills_hr
        taken = sim.taken / kills
        surv = dict(food_per_hr=food_per_kill * kills_hr, deaths_per_hr=deaths_per_hr, food=self.food_item,
                    food_qty=self.food_qty, food_heal=(self.food or {}).get("heal"),
                    auto_eat_at=round((self.food or {}).get("threshold", 0.25) * ps["hp"]),
                    hours_food_lasts=None, minutes_after_food=None, minutes_without_food=None, verdict="safe")
        if survival and taken > 0:
            def time_to_death(start_hp):
                pl, fo = build()
                pl.hp = start_hp
                f2 = Fight(self, pl, fo, random.Random(seed + 1), None)
                t = 0.0
                while t < SURVIVE_CAP_MS:
                    k = f2.kill()
                    if k is None:
                        return None
                    if f2.died:
                        return t + k
                    t += k + SPAWN_MS
                return None
            # cheap bound first: if even steady damage can't kill you within the cap, skip simulating
            ms_per_hp = ms / taken
            if ps["hp"] * ms_per_hp < SURVIVE_CAP_MS:
                t_full = time_to_death(ps["hp"])
                surv["minutes_without_food"] = None if t_full is None else t_full / 60000
                if self.food and food_per_kill > 0:
                    t_low = time_to_death(max(1, surv["auto_eat_at"]))
                    surv["minutes_after_food"] = None if t_low is None else t_low / 60000
            if self.food and food_per_kill > 0:
                surv["hours_food_lasts"] = self.food_qty / surv["food_per_hr"] if surv["food_per_hr"] else None
        if deaths_per_hr > 0:
            surv["verdict"] = "death"          # a single hit can kill you from above the auto-eat threshold
        elif surv["minutes_without_food"] is not None and (not self.food or self.food_qty <= 0):
            surv["verdict"] = "death"          # no food and you take enough damage to die
        elif surv["hours_food_lasts"] is not None and surv["minutes_after_food"] is not None:
            surv["verdict"] = "runs_out"       # dies once the food stock is gone
        elif surv["food_per_hr"] > 0:
            surv["verdict"] = "eats"

        # ---- xp per kill ----
        xp_split = {}
        base_armour, base_ward = max(0, int(foe.stats.get(NS + "Armour", 0))), max(0, int(foe.stats.get(NS + "MaximumWard", 0)))
        for g in stance["ExperienceGain"]:
            skill = short(g["Component"]["Entity"])
            per_kill = sim.dmg_xp / kills * g["ExperiencePerDamage"] + g["ExperiencePerDamage"] * (4 * base_armour + 50 * base_ward)
            xp_split[skill] = per_kill * self.xp_mult(skill)
        if sim.prayer_xp:
            xp_split["Prayer"] = sim.prayer_xp / kills * self.xp_mult("Prayer")
        currency = {}
        on_task = [m for m in self.masters.values() if aid in (m.get("Areas") or [])
                   and all(self.reqs.check_raw(r)[0] is not False for r in m.get("UnlockRequirements") or [])]
        if on_task:
            xp_split["Slayer"] = (hp / 8 + 4) * self.xp_mult("Slayer")
            currency[on_task[0]["RewardCurrency"]] = max(1, int(0.1 * hp ** 1.15))

        # ---- drops ----
        drops = enemy.get("Drops") or {}
        idbl, cdbl = 1 + min(1, self.loot["ItemDoublingChance"]), 1 + min(1, self.loot["CurrencyDoublingChance"])
        outputs = {}
        for d in drops.get("FixedItems") or []:
            outputs[d["Item"]] = outputs.get(d["Item"], 0) + (d["MinQuantity"] + d["MaxQuantity"]) / 2 * idbl
        for tb in drops.get("ItemTables") or []:
            tw = sum(x["Weight"] for x in tb["Table"]) or 1
            for x in tb["Table"]:
                q = tb["Chance"] * x["Weight"] / tw * (x["MinQuantity"] + x["MaxQuantity"]) / 2 * idbl
                outputs[x["Item"]] = outputs.get(x["Item"], 0) + q
        for c in drops.get("FixedCurrencies") or []:
            currency[c["Currency"]] = currency.get(c["Currency"], 0) + (c["MinQuantity"] + c["MaxQuantity"]) / 2 * cdbl

        # ---- consumables per kill ----
        inputs = {}
        if spell:
            keep = min(1.0, total(self.base_mods, "RunePreservationChance", Element=spell["Element"], Spell=NS + spell["Id"]))
            for c in spell["RuneCosts"]:
                n = max(0, c["Quantity"] + total(self.base_mods, "FlatRuneCost", Rune=c["Item"], Element=spell["Element"],
                                                 Spell=NS + spell["Id"]))
                if n:
                    inputs[c["Item"]] = n * casts * (1 - keep)
        if self.weapon_cap.get("RequiredAmmoType") and self.ammo:
            inputs[self.ammo] = casts * (1 - min(1.0, total(self.base_mods, "AmmoPreservationChance")))
        if self.food_item and food_per_kill > 0:
            inputs[self.food_item] = inputs.get(self.food_item, 0) + food_per_kill

        flags = [f"not simulated: {', '.join(sorted(sim.unsim))}"] if sim.unsim else []
        if self.skipped_mods:
            flags.append(f"{self.skipped_mods} conditional bonus(es) ignored")
        if surv["verdict"] == "death":
            flags.append("you can die here" + (f" (about {1 / deaths_per_hr:.1f} h between deaths)" if deaths_per_hr else " (no food)"))
        elif surv["verdict"] == "runs_out" and surv["hours_food_lasts"] is not None and surv["hours_food_lasts"] < 24:
            flags.append(f"food runs out after {surv['hours_food_lasts']:.1f} h, then death in about {surv['minutes_after_food']:.0f} min")
        yours = self.saved_stance == sid
        name = enemy["DisplayName"]
        detail = (f"{hit_chance(ps, es):.0%} hit, hits {ps['min_hit']}-{ps['max_hit']}, turn {ps['interval']:.0f}ms, "
                  f"{casts:.1f} turns per kill + {SPAWN_MS}ms respawn"
                  + (f", casting {spell['DisplayName']}" if spell else "")
                  + (f", enemy armour {es['armour']}" if es["armour"] else "") + (f", ward {es['ward']}" if es["ward"] else ""))
        return dict(
            key=f"Combat/{short(eid)}@{short(aid)}/{short(sid)}/{self.label or 'worn'}", skill="Combat",
            name=f"{name} ({stance['DisplayName']})" + (f" - {self.label}" if self.label else ""),
            loadout=self.label, enemy=name, enemy_id=eid, area=area["DisplayName"], stance=stance["DisplayName"], level_req=1,
            ms=ms, xp=sum(xp_split.values()), xp_split=xp_split, inputs=inputs, outputs=outputs, currency=currency,
            detail=detail, note="; ".join(flags) or None,
            combat=dict(hit=hit_chance(ps, es), min_hit=ps["min_hit"], max_hit=ps["max_hit"], interval=ps["interval"], hp=hp,
                        armour=es["armour"], ward=es["ward"], attacks=casts, damage_taken=taken, player_hp=ps["hp"],
                        enemy_hit=hit_chance(es, ps), enemy_max_hit=es["max_hit"], stunned_share=sim.stunned_turns / max(1, sim.p_turns),
                        area=area["DisplayName"], area_id=aid, enemy=name, enemy_id=eid, stance=stance["DisplayName"],
                        stance_id=sid, your_stance=yours,
                        spell=spell["DisplayName"] if spell else None, your_spell=spell_saved,
                        prayers=list(self.active_prayers), prayer_points=self.prayer_per_turn * casts,
                        food=self.food_item, on_task=bool(on_task), survival=surv,
                        unsimulated=sorted(sim.unsim)))

    # ------------------------------------------------------------ auto-slayer
    def master_enemies(self, mid):
        """(enemy id, area id, area unlocked) for every enemy a master can assign."""
        m = self.masters[mid]
        out, seen = [], set()
        for aid in m.get("Areas") or []:
            area = self.areas.get(aid)
            if not area:
                continue
            for eid in area["Enemies"]:
                enemy = self.enemies.get(eid)
                if not enemy or eid in seen:
                    continue
                if any(short(r["Type"]) == "DemoLocked" for r in enemy.get("Requirements") or []):
                    continue
                seen.add(eid)
                out.append((eid, aid, self.area_ok(area) is not False))
        return out

    def slayer(self, mid, stance=None, blocked=()):
        """Auto-slayer: tasks are picked uniformly from the master's enemies (minus blocked) and task length
        does not depend on the enemy, so the long-run rate is total XP per kill / total time per kill."""
        m = self.masters[mid]
        stances = [stance] if stance else self.stance_options()
        enemies = self.master_enemies(mid)
        best = None
        for sid in stances:
            rows = []
            for eid, aid, unlocked in enemies:
                r = self.fight(aid, eid, sid) if unlocked else None
                rows.append(dict(enemy_id=eid, area_id=aid, unlocked=unlocked, rate=r, blocked=eid in blocked))
            active = [x for x in rows if not x["blocked"]]
            stuck = [x for x in active if not x["rate"]]
            tot_ms = sum(x["rate"]["ms"] for x in active if x["rate"])
            tot_xp = sum(x["rate"]["xp"] for x in active if x["rate"])
            score = -1 if stuck else (tot_xp / tot_ms if tot_ms else 0)
            if best is None or score > best[0]:
                best = (score, sid, rows)
        _, sid, rows = best
        return dict(master=mid, name=m["DisplayName"], stance_id=sid, stance=self.stances[sid]["DisplayName"],
                    rows=rows, task_length=(m["BaseTaskLength"], m["BaseTaskLength"] + 3 * self.levels.get(NS + "Slayer", 1)))
