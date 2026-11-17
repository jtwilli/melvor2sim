"""View models for the in-game panel (mod/panel.gd).

The panel is a thin client: it sends a view name and a few parameters, and draws whatever
comes back. Every view has the same shape, with all numbers already formatted as text:

    {"title": str, "kpis": [{"v": str, "l": str}], "banners": [{"kind": "bad|warn|ok", "text": str}],
     "tables": [{"title": str, "note": str, "columns": [{"label": str, "num": bool}], "rows": [[str, ...]]}]}
"""
import re
import time

import calculator as calc
import combat as cmb
import boosters as boost_mod
import combat_gp as cgp_mod
import planner as plan_mod

NS = cmb.NS
HOUR = 3_600_000
SCAN_KILLS = 60
RELOAD_AFTER_S = 120
PASSIVE_NOTE = " Farming and Ranching produce that no other action makes (herbs, for example) is counted as free: it grows on its own."
MIN_STOCK_HOURS = 1.0           # bank stock is used for a chain only if it lasts at least this long
COMPARE_BUDGET_MS = 1_800_000   # simulated fight time per target and stance in the Compare tab (30 min)


def num(v):
    if v is None:
        return "-"
    a = abs(v)
    return f"{v:,.0f}" if a >= 100 else f"{v:.0f}" if a >= 10 else f"{v:.1f}"


def cnt(v):
    return "-" if v is None else f"{v:,.0f}" if v >= 10 else f"{v:.1f}"


def hrs(h):
    if h is None:
        return "-"
    if h < 1:
        return f"{max(1, round(h * 60))} min"
    return f"{h:.1f} h" if h < 48 else f"{h / 24:.1f} days"


def col(label, is_num=False):
    return {"label": label, "num": is_num}


def items(d, limit=5):
    """{'Iron Ore': 215.2, ...} -> '215 Iron Ore, ...' (largest first)."""
    parts = [f"{cnt(q)} {k}" for k, q in sorted(d.items(), key=lambda kv: -kv[1]) if q >= 0.5]
    return ", ".join(parts[:limit]) + (f" +{len(parts) - limit} more" if len(parts) > limit else "")


class GameViews:
    def __init__(self, world):
        self.w = world
        self.loaded_at = time.time()
        self._actions = None            # every action, quest ones included (the planner leaves those out)
        self._quest_defs = None
        self._names = None
        self._boosters = None

    # ------------------------------------------------------------ init
    def init(self, reload=False):
        """Everything the panel needs to build its controls. Re-reads the save if it is stale."""
        w = self.w
        if reload and time.time() - self.loaded_at > RELOAD_AFTER_S:
            w.reload()
            self.loaded_at = time.time()
        base = w.init()
        pl = w.planner_init()
        return {
            "character": pl["character"], "max_level": pl["max_level"],
            "skills": pl["skills"],
            "loadouts": [{"name": l["name"] + (" (worn)" if l["worn"] else ""), "worn": l["worn"],
                          "weapon": l["weapon"]} for l in pl["loadouts"]],
            "areas": [{"id": a["id"], "name": a["name"], "unlocked": a["unlocked"],
                       "enemies": [{"id": e["id"], "name": e["name"]} for e in a["enemies"]]} for a in base["areas"]],
            "masters": [{"id": m["id"], "name": m["name"], "unlocked": m["unlocked"], "blocked": m["blocked"],
                         "auto": m["auto_slayer"]} for m in base["masters"]],
            "dungeons": self._dungeons(),
            "foods": [{"id": f["id"], "name": f["name"], "heal": f["heal"], "owned": f["owned"]} for f in base["foods"]],
            "thieving_food": w.state.get("thieving_food"),
            "current_enemy": base["current_enemy"], "current_area": base["current_area"],
            "saved_at": w.state["header"].get("timestamp"),
        }

    def gear(self):
        """Everything the Compare tab needs to show and edit a setup: slots laid out as in the game, the items
        that fit each one, stances, spells, prayers, foods, the saved loadouts, and icon paths."""
        w = self.w
        base = w.init()
        O = lambda n: cmb._objects(calc.TABLES, n)
        levels = {k: v["level"] for k, v in w.state["skills"].items()}
        icons = {}

        def unmet(reqs):
            return ", ".join(f"{r['skill']} {r['level']}" for r in reqs if levels.get(r["skill"], 1) < r["level"])

        grid = {cmb.short(sid): s.get("GridPosition") or {} for sid, s in O("EquipmentSlot").items()}
        slots = []
        for s in base["slots"]:
            its = []
            for it in s["items"]:
                icons[it["id"]] = w.items.get(it["id"], {}).get("Icon")
                its.append({"id": it["id"], "name": it["name"], "owned": it["owned"], "worn": it["worn"],
                            "needs": unmet(it["reqs"]), "two_handed": it["two_handed"], "weapon_type": it["weapon_type"],
                            "ammo_type": it["ammo_type"], "ammo_needed": it["ammo_needed"]})
            g = grid.get(s["id"], {})
            slots.append({"id": s["id"], "name": s["name"], "x": g.get("X", 0), "y": g.get("Y", 0), "items": its})
        for f in base["foods"]:
            icons[f["id"]] = w.items.get(f["id"], {}).get("Icon")
        for table, key in (("Enemy", "Sprite"), ("Prayer", "Icon"), ("MagicSpell", "Icon"), ("AttackStance", "Icon")):
            for oid, o in O(table).items():
                if o.get(key):
                    icons[oid] = o[key]
        for d in self._dungeons():                    # a dungeon is shown with its boss's picture
            if d["boss"] in icons:
                icons[d["id"]] = icons[d["boss"]]
        open_slots = sum(1 for s in base["prayer_slots"] if not unmet(s["reqs"]))
        return {
            "slots": slots, "icons": {k: v for k, v in icons.items() if v},
            "stances": base["stances"], "weapon_types": base["weapon_types"],
            "spells": [{"id": s["id"], "name": s["name"], "element": s["element"], "needs": unmet(s["reqs"])} for s in base["spells"]],
            "prayers": [{"id": p["id"], "name": p["name"], "cost": p["cost"], "needs": unmet(p["reqs"])} for p in base["prayers"]],
            "prayer_slots": open_slots,
            "foods": [{"id": f["id"], "name": f["name"], "heal": f["heal"], "owned": f["owned"]} for f in base["foods"]],
            "loadouts": [{"name": l["name"] + (" (worn)" if l["worn"] else ""), "worn": l["worn"], "setup": l["setup"]}
                         for l in w.planner_init()["loadouts"]],
            "masters": [{"id": m["id"], "name": m["name"], "unlocked": m["unlocked"], "blocked": m["blocked"],
                         "enemies": [{"enemy": e, "area": a} for e, a, ok in self._master_enemies(m["id"]) if ok]}
                        for m in base["masters"]],
            "levels": {k: levels.get(k, 1) for k in ("Attack", "Strength", "Defence", "Hitpoints", "Ranged", "Magic", "Prayer")},
        }

    def _dungeons(self):
        """The dungeons available in this build: what you fight in order, and whether the save has them unlocked
        (None when the save cannot confirm a requirement)."""
        eng = self.w.engine(cmb.setup_from_state(self.w.state))
        out = []
        for did, d in eng.dungeons.items():
            waves = [(eng.enemies[e]["DisplayName"] if e in eng.enemies else cmb.short(e), n) for e, n in eng.dungeon_waves(did)]
            out.append({"id": did, "name": d["DisplayName"], "unlocked": eng.dungeon_unlocked(did),
                        "count": round(sum(n for _, n in waves)),
                        "enemies": ", ".join(f"{n:g}x {nm}" if n != 1 else nm for nm, n in waves),
                        "boss": eng.dungeon_waves(did)[-1][0] if waves else None})
        return out

    def _master_enemies(self, mid):
        eng = self.w.engine(cmb.setup_from_state(self.w.state))
        return eng.master_enemies(mid)

    # ------------------------------------------------------------ compare two setups
    METRICS = {"xp": "Combat XP / hr", "gp": "GP / hr", "net_gp": "GP / hr after supplies", "kills": "Kills / hr"}

    def _measure(self, eng, target, stance_rule, metric, food):
        """Best stance for this target under the metric -> flat figures for the panel, or None if it can't be killed."""
        did = target.get("dungeon")
        if did:
            if did not in eng.dungeons:
                return None
        else:
            eid, aid = target["enemy"], target.get("area") or eng.area_of(target["enemy"])
            if eid not in eng.enemies or aid not in eng.areas:
                return None
        stances = eng.stance_options()
        if stance_rule == "saved" and eng.saved_stance in stances:
            stances = [eng.saved_stance]
        skill = metric.split(":", 1)[1] if metric.startswith("xp:") else None

        def value(s):
            if skill:
                return s["xp_hr"].get(skill, 0.0)
            supplies = sum(u["gp_hr"] for u in s["uses"])
            return {"gp": s["gp_hr"], "net_gp": s["gp_hr"] - supplies, "kills": s["kills_hr"]}.get(metric, s["xp_hr_total"])

        best = None
        for sid in stances:
            # survival=False skips the slow time-to-death sub-simulation
            r = eng.dungeon(did, sid) if did else eng.fight(aid, eid, sid, survival=False)
            if r:
                s = self.w.summarize(r)
                if best is None or value(s) > value(best):
                    best = s
        if best is None:
            return None
        c, sv = best["combat"], best["combat"]["survival"]
        rel = (c.get("ci") or {}).get("xp" if metric.startswith("xp") else "kills") or 0.0
        v = value(best)
        # survival from the main run alone: one-shot deaths, and how long the food stock lasts
        if sv["deaths_per_hr"] > 0:
            kind, text = "bad", f"can die (about every {1 / sv['deaths_per_hr']:.1f} h)"
        elif c["damage_taken"] > 0 and not eng.food:
            kind, text = "bad", "takes damage with no food"
        elif sv["food_per_hr"] > 0:
            lasts = eng.food_qty / sv["food_per_hr"]
            kind, text = ("warn" if lasts < 8 else "ok"), f"{food or 'food'} lasts {hrs(lasts)}"
        else:
            kind, text = "ok", "safe"
        return {
            "value": v, "lo": v * (1 - rel), "hi": v * (1 + rel), "stance": best["stance"],
            "xp_hr": best["xp_hr_total"], "xp_by_skill": best["xp_hr"], "kills_hr": best["kills_hr"],
            "kill_s": best["seconds_per_kill"], "gp_hr": best["gp_hr"], "food_hr": sv["food_per_hr"],
            "prayer_hr": c.get("prayer_points", 0) * best["kills_hr"], "deaths_hr": sv["deaths_per_hr"],
            "hit": c["hit"], "max_hit": c["max_hit"], "interval": c["interval"], "player_hp": c["player_hp"],
            "enemy_hit": c["enemy_hit"], "enemy_max_hit": c["enemy_max_hit"], "damage_taken": c["damage_taken"],
            "survival": text, "survival_kind": kind,
            "uses": {u["item"]: u["per_hr"] for u in best["uses"]},
            "supplies_gp_hr": sum(u["gp_hr"] for u in best["uses"]),
            "net_gp_hr": best["gp_hr"] - sum(u["gp_hr"] for u in best["uses"]),
            "loot": {x["item"]: x["per_hr"] for x in best["loot"][:6]},
            "enemies_per_run": c.get("enemies_per_run"),
            # the skill levels this stance fights with, so a low level is visible next to the result
            "levels": ", ".join(f"{sk} {eng.reqs.level(sk)}" for sk in
                                dict.fromkeys(cmb.short(g["Component"]["Entity"])
                                              for g in eng.stances[c["stance_id"]]["ExperienceGain"]) if sk != "Hitpoints"),
        }

    def _cannot_wear(self, eng, setup):
        """Items in the setup whose level requirements this character (with any what-if levels) does not meet."""
        out = []
        for slot in (setup.get("equipment") or {}).values():
            it = self.w.items.get((slot or {}).get("item"))
            eq = ((it or {}).get("Capabilities") or {}).get(NS + "Equipment")
            if not eq:
                continue
            needs, _ = self.w.level_reqs(eq.get("Requirements"))
            short_of = [f"{r['skill']} {r['level']}" for r in needs if eng.reqs.level(r["skill"]) < r["level"]]
            if short_of:
                out.append(f"{self.w.name(slot['item'])} (needs {', '.join(short_of)})")
        return list(dict.fromkeys(out))

    def compare(self, p):
        """Setup A (and optionally B) against a batch of targets. The same random seed is used for both setups
        on each target, so differences come from the setups, not from luck."""
        w = self.w
        metric = p.get("metric") or "xp"
        rule = p.get("stance") or "best"
        engines, foods, warnings = {}, {}, {}
        for side in ("a", "b"):
            setup = p.get(side)
            if not setup:
                continue
            levels = {k: int(v) for k, v in (setup.get("levels") or {}).items()}
            eng = w.engine(dict(setup, levels=levels))
            eng.sim_budget_ms = COMPARE_BUDGET_MS
            if not eng.weapon:
                return {"error": f"Setup {side.upper()} has no weapon. Pick one to simulate combat."}
            engines[side] = eng
            warnings[side] = self._cannot_wear(eng, setup)
            foods[side] = w.name(setup["food"]) if setup.get("food") else None
        if "a" not in engines:
            return {"error": "Setup A is empty."}
        ref = engines["a"]
        rows = []
        for t in p.get("targets") or []:
            if t.get("dungeon"):
                did = t["dungeon"]
                if did not in ref.dungeons:
                    continue
                waves = ref.dungeon_waves(did)
                row = {"enemy": did, "area": "dungeon", "name": ref.dungeons[did]["DisplayName"], "dungeon": True,
                       "area_name": "Dungeon: " + ", ".join(
                           (f"{n:g}x " if n != 1 else "") + (ref.enemies[e]["DisplayName"] if e in ref.enemies else cmb.short(e))
                           for e, n in waves), "delta": None, "verdict": None}
                target = {"dungeon": did}
            else:
                eid = t["enemy"]
                aid = t.get("area") or ref.area_of(eid)
                if eid not in ref.enemies:
                    continue
                row = {"enemy": eid, "area": aid, "name": ref.enemies[eid]["DisplayName"], "dungeon": False,
                       "area_name": ref.areas[aid]["DisplayName"] if aid in ref.areas else "", "delta": None, "verdict": None}
                target = {"enemy": eid, "area": aid}
            for side, eng in engines.items():
                row[side] = self._measure(eng, target, rule, metric, foods[side])
            a, b = row.get("a"), row.get("b")
            if a and b and a["value"] > 0:
                row["delta"] = (b["value"] - a["value"]) / a["value"]
                row["verdict"] = "b" if b["lo"] > a["hi"] else "a" if b["hi"] < a["lo"] else "same"
            elif "b" in engines and (a or b):
                row["verdict"] = "b" if b and not a else "a" if a and not b else None
            rows.append(row)
        label = f"{metric.split(':', 1)[1]} XP / hr" if metric.startswith("xp:") else self.METRICS.get(metric, "Combat XP / hr")
        return {"rows": rows, "metric": metric, "metric_label": label, "sides": sorted(engines), "cannot_wear": warnings}

    def force_reload(self):
        self.w.reload()
        self.loaded_at = time.time()
        return self.init()

    def view(self, name, params):
        fn = {"best": self.best, "plan": self.plan, "combat": self.combat, "ahead": self.ahead,
              "produce": self.produce, "produce_items": self.produce_items, "boost": self.boost,
              "gp_plan": self.gp_plan, "combat_gp": self.combat_gp}.get(name)
        if not fn:
            return {"title": "", "banners": [{"kind": "bad", "text": f"Unknown view {name!r}"}], "kpis": [], "tables": []}
        try:
            return fn(params or {})
        except ValueError as ex:
            return {"title": "", "banners": [{"kind": "bad", "text": str(ex)}], "kpis": [], "tables": []}

    def _setup(self, index):
        los = self.w.planner_init()["loadouts"]
        if not los:
            raise ValueError("No loadouts found in the save.")
        if index is None or not 0 <= int(index) < len(los):
            index = next((i for i, l in enumerate(los) if l["worn"]), 0)
        return los[int(index)]

    # ------------------------------------------------------------ best XP / GP
    def best(self, p):
        w, pl = self.w, self.w.planner
        gold = NS + "GoldPieces"
        by_gp = p.get("rank") == "gp"
        metric = "gp_hr" if by_gp else "xp_hr"
        pl.reqs.state = w.state                      # the planner moves these while it steps through levels
        pl.rates.state, pl.rates.reqs = w.state, pl.reqs
        rates, passive, _ = calc.action_rates(pl.actions, w.state, w.bonuses, pl.reqs, pl.rates)
        use_bank = bool(p.get("use_bank", True))
        rows = list(self._chain_rows(rates, passive, use_bank=use_bank)[0].values())
        chain = lambda r: " > ".join(s["name"] for s in r["steps"]) if len(r["steps"]) > 1 else "-"
        stock = lambda r: (", ".join(f"{s['item']}" for s in r.get("stock") or [])
                           + (f" (lasts {hrs(r['stock_hours'])})" if r.get("stock_hours") else "")) or "-"
        levels = {k: v["level"] for k, v in w.state["skills"].items()}
        tables, skill = [], p.get("skill")
        if skill:
            mine = sorted((r for r in rows if r["skill"] == skill), key=lambda r: -r[metric])[:12]
            tables.append({
                "title": f"{skill} (level {levels.get(skill, 1)}): top actions by {'GP' if by_gp else 'XP'} per hour",
                "columns": [col("Action"), col("XP / hr", True), col("All-skill XP / hr", True), col("GP / hr", True), col("Chain"),
                            col("From your bank")],
                "rows": [[r["name"], num(r["xp_hr"]), num(r["xp_total_hr"]), num(r["gp_hr"]), chain(r), stock(r)]
                         for r in mine]})
        else:
            tops = {}
            for r in rows:
                if r["skill"] not in tops or r[metric] > tops[r["skill"]][metric]:
                    tops[r["skill"]] = r
            ordered = sorted(tops.values(), key=lambda r: -r[metric])
            tables.append({
                "title": f"Best action per skill by {'GP' if by_gp else 'XP'} per hour",
                "note": "Counts the time to gather and craft every ingredient from nothing." + PASSIVE_NOTE,
                "columns": [col("Skill"), col("Lvl", True), col("Do this"), col("XP / hr", True), col("GP / hr", True), col("Chain"),
                            col("From your bank")],
                "rows": [[r["skill"], str(levels.get(r["skill"], 1)), r["name"], num(r["xp_hr"]), num(r["gp_hr"]), chain(r),
                          stock(r)] for r in ordered]})
        banners = []
        if p.get("combat"):
            lo = self._setup(p.get("loadout"))
            eng = pl._engine(dict(lo["setup"], levels={}), {k: float(v["xp"]) for k, v in w.state["skills"].items()}, SCAN_KILLS)
            if not eng.weapon:
                banners.append({"kind": "warn", "text": f"{lo['name']} has no weapon, so combat was skipped."})
            else:
                stances = [eng.saved_stance] if eng.saved_stance in eng.stance_options() else None
                fights, _ = eng.rates(None, stances, survival=False)
                summ = sorted((w.summarize(r) | {"name": r["enemy"], "area": r["area"]} for r in fights.values()),
                              key=lambda s: -(s["gp_hr"] if by_gp else s["xp_hr_total"]))[:10]
                tables.append({
                    "title": f"Combat with {lo['name']} ({lo['weapon']}): top enemies by {'GP' if by_gp else 'XP'} per hour",
                    "note": "Quick scan (60 kills each). Use the Combat tab for food use and whether you can die.",
                    "columns": [col("Enemy"), col("Area"), col("Stance"), col("Kills / hr", True), col("Combat XP / hr", True),
                                col("GP / hr", True), col("Uses / hr"), col("GP after supplies", True), col("XP by skill")],
                    "rows": [[s["name"], s["area"], s["stance"], num(s["kills_hr"]), num(s["xp_hr_total"]), num(s["gp_hr"]),
                              ", ".join(f"{num(u['per_hr'])} {u['item']}" for u in s["uses"]) or "nothing",
                              num(s["gp_hr"] - sum(u["gp_hr"] for u in s["uses"])),
                              ", ".join(f"{k} {num(v)}" for k, v in sorted(s["xp_hr"].items(), key=lambda kv: -kv[1]))]
                             for s in summ]})
        if use_bank:
            banners.insert(0, {"kind": "ok", "text": "Ingredients already in your bank are used first (charged at their "
                                                     "sell price, as you could have sold them) when the stock lasts at "
                                                     f"least {MIN_STOCK_HOURS:g} h; otherwise they are gathered from nothing."})
        return {"title": "Best XP and gold per hour", "kpis": [], "banners": banners, "tables": tables}

    def _chain_rows(self, rates, passive, no_shop=False, use_bank=True):
        """key -> chain_row for every action. With use_bank, each chain takes what it needs from your bank when
        that stock lasts at least MIN_STOCK_HOURS of the chain; otherwise it gathers from nothing.
        Returns (rows, sources, best) for the bank-using solve (or the plain one)."""
        w, pl = self.w, self.w.planner
        gold = NS + "GoldPieces"
        plain_src = calc.zero_time_sources(pl.reqs, gold, no_shop, passive, rates=rates)
        plain = calc.solve_chains(rates, plain_src)
        if calc.drop_unsourced_supplies(rates, plain):
            plain = calc.solve_chains(rates, plain_src)
        # your bank, plus what is on your toolbelts (sightings logs are used up from there)
        stock = dict(w.state.get("bank") or {})
        for slots in (w.state.get("toolbelts") or {}).values():
            for sl in slots.values():
                tb = ((w.items.get(sl["item"]) or {}).get("Capabilities") or {}).get(NS + "Toolbelt") or {}
                if tb.get("ConsumesOn"):             # used-up items only, never a tool you keep
                    stock[sl["item"]] = stock.get(sl["item"], 0) + sl.get("qty", 0)
        st = dict(w.state, bank=stock)
        stock_src = calc.zero_time_sources(pl.reqs, gold, no_shop, passive, rates=rates,
                                           stock=stock if use_bank else None)
        stocked = calc.solve_chains(rates, stock_src) if use_bank else plain
        rows = {}
        for key, r in rates.items():
            if r.get("converter"):
                continue
            row = None
            if use_bank:
                row, _ = calc.chain_row(key, rates, stocked, stock_src, w.name, w.price, gold, st)
                if row and row["stock_hours"] is not None and row["stock_hours"] < MIN_STOCK_HOURS:
                    row = None
            if row is None:
                row, _ = calc.chain_row(key, rates, plain, plain_src, w.name, w.price, gold, w.state)
            if row:
                rows[key] = row
        return rows, (stock_src if use_bank else plain_src), stocked

    # ------------------------------------------------------------ look ahead
    def ahead(self, p):
        skill = p.get("skill")
        if not skill or skill in plan_mod.COMBAT_SKILLS:
            raise ValueError("Pick a skilling skill to look ahead.")
        up_to = int(p.get("up_to") or 60)
        r = self.w.planner.look_ahead(skill, up_to, p.get("assume_tools", True), p.get("food") or None,
                                      int(p.get("stealth") or 0))
        thieving = skill == "Thieving"
        risky = skill in ("Thieving", "Agility")       # failures that hurt, and food to heal them
        agility = skill == "Agility"
        cols = [col("Lvl", True), col("Action"), col("Status"),
                col("XP / hr, your techniques" if agility else "XP / hr at unlock", True),
                col("XP / hr, techniques maxed" if agility else f"XP / hr at {r['up_to']}", True), col("GP / hr", True)]
        if risky:
            cols += [col("Success", True), col("Food / hr", True), col("Risk")]
        cols.append(col("Notes"))
        rows, best_xp = [], None
        for a in r["actions"]:
            first, last = a["at_unlock"], a["at_target"]
            if last and (best_xp is None or last["xp_hr"] > best_xp[1]):
                best_xp = (a["name"], last["xp_hr"])
            status = ("unlocked" if a["unlocked"] else f"at level {a['level']}" if a["level"] > r["level"] else "")
            if a["needs"]:
                status = (status + "; " if status else "") + "needs " + ", ".join(n.replace("equip ", "") for n in a["needs"])
            row = [str(a["level"]), a["name"], status or "-",
                   num(first["xp_hr"]) if first else "-", num(last["xp_hr"]) if last else "-",
                   num(last["gp_hr"]) if last else "-"]
            if risky:
                f = first or {}
                death = f.get("deaths_hr") or 0.0
                every = 1 / death if death > 0 else None
                row += [f"{f['success']:.0%}" if f.get("success") is not None else "-",
                        num(f.get("food_hr")) if f.get("food_hr") else "-",
                        "safe" if every is None else f"can die (~every {every:.1f} h)" if every >= 1
                        else f"can die (~every {every * 60:.0f} min)"]
            src = last or first
            chain = " > ".join(dict.fromkeys(src["chain"][:-1])) if src and len(src["chain"]) > 1 else ""
            label = "food from: " if risky else "chain: "
            note = (first or {}).get("note") if agility else None
            row.append("; ".join(x for x in (note, label + chain if chain else "") if x) or "-")
            rows.append(row)
        banners = []
        if skill == "Agility":
            banners.append({"kind": "ok" if r["food"] else "bad",
                            "text": (f"Agility eats {r['food']} when a failed obstacle drops you to the auto-eat point; making "
                                     "it is counted in XP / hr. A cheap food (pick one in the food box) usually costs less "
                                     "time.") if r["food"] else
                                    "No Agility food is set, so failed obstacles will eventually kill you. Pick a food."})
            banners.append({"kind": "warn", "text": "Obstacle success depends on your technique levels (Climbing, Jumping, "
                                                    "...), not your Agility level. 'Your techniques' uses today's levels; "
                                                    "'techniques maxed' assumes each at its cap (40). Each course gives "
                                                    "technique XP per lap, so the Levelling planner raises them as it goes. "
                                                    "The Notes column says which technique levels make a course certain."})
        if thieving:
            banners.append({"kind": "ok" if r["food"] else "bad",
                            "text": (f"Thieving eats {r['food']} when a failure drops you to the auto-eat point; its fishing and "
                                     "cooking time is counted in XP / hr.") if r["food"] else
                                    "No Thieving food is set, so every failure is lost HP and you will die eventually. "
                                    "Pick a food in Thieving."})
            banners.append({"kind": "warn", "text": "Success chance rises with your Thieving level, so the 'at unlock' figures are "
                                                    f"the worst case and the 'at {r['up_to']}' figures the best."})
            banners.append({"kind": "warn", "text": "Stealth decides which NPC is best. Beyond your level and your food's bonus: "
                                                    "Balaclava +15 (from Thug), Thug Chief Ring +25 (Thug Chief), Bandit Gloves +30 "
                                                    "(Bandit), Thieving Mastery Rank 3 +60, Astrology +10 per node. Try them with "
                                                    "the extra stealth box."})
        title = f"{r['skill']} up to level {r['up_to']} (you are {r['level']})"
        kpis = [{"v": str(sum(1 for a in r["actions"] if a["unlocked"])), "l": "Unlocked now"},
                {"v": str(sum(1 for a in r["actions"] if not a["unlocked"])), "l": f"Still to unlock by {r['up_to']}"}]
        if best_xp:
            kpis.append({"v": num(best_xp[1]), "l": f"Best XP / hr at {r['up_to']}: {best_xp[0]}"})
        return {"title": title, "kpis": kpis, "banners": banners,
                "tables": [{"title": "Every action up to level %d" % r["up_to"],
                            "note": "Costed from a standing start: gathering and crafting the inputs is included. "
                                    "Tools a location needs are assumed equipped; the Status column says which." + PASSIVE_NOTE,
                            "columns": cols, "rows": rows}]}

    # ------------------------------------------------------------ GP planner
    GP_TOP = 10
    GP_RECHECK = 30                        # candidates re-costed at the exact levels they unlock at

    def _level_needs(self, a):
        """skill -> level an action needs (its own skill, plus Level requirements on it and its category)."""
        need = {}

        def put(sk, lv):
            if sk and lv:
                need[sk] = max(need.get(sk, 0), int(lv))
        put(a["skill"], a["level_req"])
        for r in a["other_requirements"]:
            if r["type"] == "Level":
                put(r.get("entity"), r.get("Level"))
        table = calc.CATEGORY_TABLE.get(a["skill"], "RecipeCategory")
        for r in self.w.planner.reqs.category_reqs.get(table, {}).get(a["category"], []):
            if calc.short(r["Type"]) == "Level":
                d = r.get("Data") or {}
                put(calc.short((d.get("Component") or {}).get("Entity")), d.get("Level"))
        return need

    def _gp_solve(self, levels, what_if, passive_free=False):
        """Self-sufficient chains (nothing bought, nothing from the bank) with these skill levels. With what_if,
        tools, shop unlocks, Astrology nodes, mastery ranks and 'do X first' counts are all treated as met."""
        w, pl = self.w, self.w.planner
        gold = NS + "GoldPieces"
        st = cmb.with_levels(w.state, levels)
        pl.reqs.state, pl.reqs.assume_tools, pl.reqs.assume_unlocks = st, what_if, what_if
        pl.rates.state, pl.rates.reqs = st, pl.reqs
        try:
            rates, passive, _ = calc.action_rates(pl.actions, st, w.bonuses, pl.reqs, pl.rates)
            src = calc.zero_time_sources(pl.reqs, gold, True, passive if passive_free else (), rates=rates)
            best = calc.solve_chains(rates, src)
            if calc.drop_unsourced_supplies(rates, best):
                best = calc.solve_chains(rates, src)
            rows = {}
            for key, r in rates.items():
                if r.get("converter"):
                    continue
                row, _ = calc.chain_row(key, rates, best, src, w.name, w.price, gold, st)
                if row and row["gp_hr"] > 0:
                    rows[key] = row
            return rows
        finally:
            pl.reqs.state, pl.reqs.assume_tools, pl.reqs.assume_unlocks = w.state, False, False
            pl.rates.state, pl.rates.reqs = w.state, pl.reqs

    def _gp_prereqs(self, a):
        """Actions an unmet 'do X first' requirement points at (run a course, make an item, observe an event)."""
        pl = self.w.planner
        table = calc.CATEGORY_TABLE.get(a["skill"], "RecipeCategory")
        raw = [(r["type"], r) for r in a["other_requirements"]]
        raw += [(calc.short(r["Type"]), r.get("Data") or {}) for r in pl.reqs.category_reqs.get(table, {}).get(a["category"], [])]
        out = []
        for kind, d in raw:
            if not kind.endswith("Statistic") or pl.reqs.check(kind, None, d)[0] is not False:
                continue
            stat = calc.short(d.get("Statistic") or "")
            if d.get("Course"):
                opts = [x for x in pl.actions if x["type"] == "AgilityCoursePath" and x["id"].startswith(d["Course"])]
            elif d.get("Event"):
                opts = [x for x in pl.actions if x["type"] == "AstrologyConstellation"
                        and d["Event"] in ((x.get("raw_extra") or {}).get("events") or [])]
            elif d.get("Item") and stat != "BankItemFound":
                opts = [x for x in pl.actions if any(o["item"] == d["Item"] for o in x["outputs"])]
            else:
                opts = []
            if opts:
                out.append(min(opts, key=lambda x: max(self._level_needs(x).values(), default=0)))
        return out

    def _gp_needs(self, row, by_key, cur):
        """What a chain still needs today: {skill: level} over every step (and whatever a 'do X first'
        requirement leads to), plus the other unmet requirements."""
        pl = self.w.planner
        levels, other, notes, seen = {}, [], [], set()
        todo = [by_key.get(s.get("key")) for s in row["steps"]]
        for x in row.get("passive") or []:          # Farming / Ranching produce counted as free
            if x.get("src") != "passive":
                continue
            makers = [a for a in pl.actions if a["type"] in calc.PASSIVE_TYPES
                      and any(o["item"] == x["id"] for o in a["outputs"])]
            if makers:
                m = min(makers, key=lambda a: a["level_req"] or 1)
                todo.append(m)
                each = sum(o["expected_qty"] for o in m["outputs"] if o["item"] == x["id"]) * HOUR / (m["interval_ms"] or HOUR)
                many = f" (about {x['per_hr'] / each:,.0f} {'plots' if m['skill'] == 'Farming' else 'animals'})" if each else ""
                label = f"{num(x['per_hr'])} {x['item']} / hr from {m['skill']}: {m['name']}{many}"
                if label not in notes:
                    notes.append(label)
        depth = {id(a): 0 for a in todo if a}
        while todo:
            a = todo.pop()
            if not a or a["key"] in seen:
                continue
            seen.add(a["key"])
            for sk, lv in self._level_needs(a).items():
                if lv > cur.get(sk, 1):
                    levels[sk] = max(levels.get(sk, 0), lv)
            _, missing, unknown = pl.reqs.action(a)
            for m in missing + [u + " (unverified)" for u in unknown]:
                head, _, tail = m.partition(" ")
                if (head in cur and tail.isdigit()) or m in other:
                    continue                          # a skill level: already in `levels`
                other.append(m)
            if depth.get(id(a), 0) < 3:
                for x in self._gp_prereqs(a):
                    depth[id(x)] = depth.get(id(a), 0) + 1
                    todo.append(x)
        return levels, other, notes

    def _gp_boosters(self):
        if self._boosters is None:
            self._boosters = boost_mod.Boosters(self.w, self._all_actions())
        return self._boosters

    def _gp_label(self, m):
        """A requirement label made readable, and how to get it where that is an item or a shop unlock."""
        bs = self._gp_boosters()
        split = lambda s: re.sub(r"(?<=[a-z])(?=[A-Z0-9])", " ", s)
        how = None
        if m.startswith("equip "):
            iid = NS + m[6:]
            ways = bs.where_from(iid)
            how = "; ".join(w_["text"] + (f" (needs {', '.join(w_['needs'])})" if w_["needs"] else "")
                            for w_ in sorted(ways, key=lambda w_: (not w_["met"], w_["level"]))[:2])
            return f"equip {bs.name(iid)}", how or "no source in the game data"
        if m.startswith("buy "):
            pid = NS + m[4:]
            if pid in bs.shop:
                s = bs.shop_way(pid)
                return f"buy {bs.name(pid)}", s["text"] + (f" (needs {', '.join(s['needs'])})" if s["needs"] else "")
            return split(m), None
        st = re.match(r"^(\w+) (\w+) ([\d,]+)/([\d,]+)( \(unverified\))?$", m)
        if st:
            stat, key, have, need, unv = st.groups()
            name = bs.name(NS + key)
            name = split(name) if " " not in name else name
            so_far = f" ({have} so far)" + (unv or "") if have != "0" else (unv or "")
            if stat == "BankItemFound":
                ways = bs.where_from(NS + key)
                how = "; ".join(w_["text"] for w_ in sorted(ways, key=lambda w_: (not w_["met"], w_["level"]))[:2])
                return f"find a {name}", how or "no source in the game data"
            if stat == "AgilityCourseCompleted":
                return f"complete {name} {need} times{so_far}", "run the Agility course (its levels are included)"
            if stat.endswith("ItemGained"):
                return f"make {need} {name} first{so_far}", "make them (their levels are included)"
            if stat == "AstrologyTimesEventObserved":
                return f"observe {name}" + (f" {need} times" if need != "1" else "") + so_far,                     "a random event while studying in Astrology"
            return f"{split(stat).lower()} for {name}: {need}{so_far}", None
        if m.startswith("finish quest "):
            return f"finish quest {bs.name(NS + m[13:])}", None
        if m.startswith("Astrology node "):
            return f"Astrology node {bs.name(NS + m[15:])}", "unlock it in Astrology"
        return split(m), None

    def gp_plan(self, p):
        w, pl = self.w, self.w.planner
        within = int(p.get("within") or 0)
        cur = {k: v["level"] for k, v in w.state["skills"].items()}
        cap = plan_mod.MAX_LEVEL
        by_key = {a["key"]: a for a in pl.actions}
        free = bool(p.get("passive"))
        now = self._gp_solve({}, False, free)
        best_now = max(now.values(), key=lambda r: r["gp_hr"], default=None)
        reach = within or cap
        wide = self._gp_solve({k: min(cap, v + reach) for k, v in cur.items()}, True, free)
        cands = []
        for key, row in wide.items():
            levels, other, _ = self._gp_needs(row, by_key, cur)
            if not levels and not other:
                continue                              # nothing locked about it
            if within and max((lv - cur.get(sk, 1) for sk, lv in levels.items()), default=0) > within:
                continue                              # a 'do X first' step needs more levels than the window
            mine = now.get(key)
            if mine and mine["gp_hr"] >= row["gp_hr"] * 0.99:
                continue                              # the locked step doesn't make it any better
            cands.append((row["gp_hr"], key))
        cands.sort(reverse=True)
        found, profiles = [], {}
        for _, key in cands[:self.GP_RECHECK]:
            levels = self._gp_needs(wide[key], by_key, cur)[0]
            prof = tuple(sorted(levels.items()))
            if prof not in profiles:                  # cost it at the levels it unlocks at, not the window's top
                profiles[prof] = self._gp_solve(dict(prof), True, free)
            row = profiles[prof].get(key) or wide[key]
            levels, other, notes = self._gp_needs(row, by_key, cur)
            if not levels and not other:
                continue
            if f_now := now.get(key):
                if f_now["gp_hr"] >= row["gp_hr"] * 0.99:
                    continue                          # costed at its unlock levels, it is no better than today
            gap = max((lv - cur.get(sk, 1) for sk, lv in levels.items()), default=0)
            if within and gap > within:
                continue
            found.append(dict(row=row, levels=levels, other=other, notes=notes, gap=gap, now=now.get(key)))
        found.sort(key=lambda f: -f["row"]["gp_hr"])
        top = found[:self.GP_TOP]
        base = best_now["gp_hr"] if best_now else 0.0
        rows, unlocks = [], {}
        for i, f in enumerate(top, 1):
            r = f["row"]
            other = []
            for m in f["other"]:
                label, how = self._gp_label(m)
                other.append(label)
                unlocks.setdefault(label, [how, []])[1].append(str(i))
            lv = ", ".join(f"{sk} {v} (+{v - cur.get(sk, 1)})"
                           for sk, v in sorted(f["levels"].items(), key=lambda kv: -(kv[1] - cur.get(kv[0], 1)))) or "-"
            gain = f"{(r['gp_hr'] / base - 1):+.0%}" if base > 0 else "-"
            chain = " > ".join(dict.fromkeys(s["name"] for s in r["steps"][:-1])) or "-"
            rows.append([str(i), r["skill"], r["name"], num(r["gp_hr"]), gain, num(r["xp_hr"]), lv,
                         ", ".join(other + f["notes"]) or "-", chain])
        label = f"within {within} levels" if within else "at any level"
        kpis = []
        if best_now:
            kpis.append({"v": num(base), "l": f"Best self-sufficient GP / hr now: {best_now['name']} ({best_now['skill']})"})
        if top:
            kpis.append({"v": num(top[0]["row"]["gp_hr"]), "l": f"Best locked {label}: {top[0]['row']['name']}"})
        kpis.append({"v": str(len(found)), "l": f"Locked methods found {label}" + (" (top checked)" if
                                                                               len(cands) > self.GP_RECHECK else "")})
        banners = [{"kind": "ok", "text": "Self-sufficient: every ingredient is gathered or crafted by you (nothing bought, "
                                          "nothing taken from the bank), and the product is sold. Combat is not included."},
                   {"kind": "warn", "text": "Each method is costed at the levels it unlocks at; your other skills stay "
                                            "where they are. 'Also needs' lists tools, shop unlocks, Astrology nodes, "
                                            "mastery ranks and 'do X first' counts, which are assumed met (the time to "
                                            "do them is not counted)."}]
        banners.append({"kind": "warn", "text": "Farming and Ranching produce is counted as free, with no limit on how much "
                                                "you grow. 'Also needs' says how many plots or animals that takes."} if free else
                       {"kind": "ok", "text": "Farming and Ranching produce is left out (it grows at its own pace), so "
                                              "methods that need herbs, dragonscales and the like are not listed. Tick "
                                              "'Count farming as free' to include them."})
        if not top:
            banners.insert(0, {"kind": "warn", "text": f"No locked method {label} beats what it makes today."})
        return {"title": f"GP planner: locked self-sufficient methods {label}", "kpis": kpis, "banners": banners,
                "tables": [{"title": f"Top {len(top)} by GP per hour",
                            "note": "Levels needed covers every step of the chain, with how many levels away each is." +
                                    PASSIVE_NOTE,
                            "columns": [col("#"), col("Skill"), col("Method"), col("GP / hr", True), col("vs best now", True),
                                        col("XP / hr", True), col("Levels needed"), col("Also needs"), col("Chain")],
                            "rows": rows}] + ([{
                    "title": "How to unlock them (besides levels)",
                    "columns": [col("Needs"), col("For #"), col("How to get it")],
                    "rows": [[k, ", ".join(v[1]), v[0] or "-"] for k, v in unlocks.items()]}] if unlocks else [])}

    # ------------------------------------------------------------ combat GP planner
    def combat_gp(self, p):
        """The richest enemies to farm for drops, and the gear tier and level each style needs."""
        w = self.w
        if getattr(self, "_cgp", None) is None:
            self._cgp = cgp_mod.CombatGP(w, self._all_actions())
        within = int(p.get("within") or 0)
        food = self._setup(None)["setup"].get("food")
        data = self._cgp.curves(food)
        cur = {k: v["level"] for k, v in w.state["skills"].items()}
        mine = {s: min(cur.get(k, 1) for k in ks) for s, ks in cgp_mod.STYLE_SKILLS.items()}
        areas = calc.objects("CombatArea")
        reqs = calc.Requirements(w.state)
        good = lambda r: r and r["safe"] and r["gp"] > 0
        found = []
        for eid, e in data.get("enemies", {}).items():
            best, froms = None, {}
            for style, rows in e["styles"].items():
                ok = sorted(L for L, r in rows.items() if good(r))
                froms[style] = (ok[0], rows[ok[0]]["gp"]) if ok else None
                reach = 99 if not within else min(99, mine[style] + within)
                for L in ok:
                    if L <= reach and (best is None or rows[L]["gp"] > best[2]["gp"]):
                        best = (style, L, rows[L])
            if best:
                found.append((e, best, froms))
        found.sort(key=lambda f: -f[1][2]["gp"])
        rows, used = [], {}
        for i, (e, (style, L, r), froms) in enumerate(found[:self.GP_TOP], 1):
            need = []
            for q in (areas.get(e["area_id"]) or {}).get("UnlockRequirements") or []:
                ok, label = reqs.check_raw(q)
                if ok is not True:
                    need.append(self._gp_label(label)[0] + ("" if ok is False else " (unverified)"))
            fmt = lambda s: ("-" if not froms.get(s) else
                             f"{froms[s][0]}{' (now)' if froms[s][0] <= mine[s] else ''}: {num(froms[s][1])}")
            used[f"{style}|{L}"] = (style, L)
            rows.append([str(i), e["name"], e["area"], num(r["gp"]), f"{style} {L} ({r['weapon']}, {r['stance']})",
                         fmt("Melee"), fmt("Ranged"), fmt("Magic"), ", ".join(need) or "-",
                         ", ".join(f"{n} {num(g)}" for g, n in r["loot"]) or "-"])
        gear = []
        for k, (style, L) in sorted(used.items(), key=lambda kv: (kv[1][0], kv[1][1])):
            t = data["tiers"].get(k)
            if t:
                gear.append([f"{style} {L}", f"{t['weapon']} ({t['stance']})" + (f" + {t['ammo']}" if t["ammo"] else ""),
                             ", ".join(t["armour"]) or "-",
                             ", ".join(f"{sk} {lv}" for sk, lv in sorted(t["made_with"].items())) or "-"])
        label = f"within {within} levels" if within else "at any level"
        kpis = [{"v": f"{mine['Melee']} / {mine['Ranged']} / {mine['Magic']}",
                 "l": "Your melee / ranged / magic tier (lowest of the style's skills, Defence and Hitpoints)"}]
        if found:
            e, (style, L, r), _ = found[0]
            kpis.append({"v": num(r["gp"]), "l": f"Best {label}: {e['name']} with {style} {L}"})
        banners = [
            {"kind": "ok", "text": "A tier is a level L with the best standard gear you can make for that style at L "
                                   "(Smithing, Fletching, Crafting or Runecrafting): the style's skills, Defence and "
                                   "Hitpoints at L (or yours, if higher). Your bonuses and food are kept. Prayers, "
                                   "rings, amulets and capes are left off."},
            {"kind": "ok", "text": "GP / hr is loot and coins less the food, ammo and runes the fight uses, all at "
                                   "their sell price. 'From' is the lowest tier where you don't die and still make "
                                   "gold; it says '(now)' when your levels are already there."},
            {"kind": "warn", "text": f"Only the {cgp_mod.CANDIDATES} richest enemies are worked out (simulated, so "
                                     "small differences are noise). The first run takes about half a minute; "
                                     "after that it is kept until the save reloads."}]
        if not found:
            banners.insert(0, {"kind": "warn", "text": f"No enemy pays {label} with standard gear."})
        tables = [{"title": f"Top {len(rows)} enemies for gold {label}",
                   "columns": [col("#"), col("Enemy"), col("Area"), col("GP / hr", True), col("Best way"),
                               col("Melee from"), col("Ranged from"), col("Magic from"), col("Area needs"),
                               col("Top drops (gp / hr)")],
                   "rows": rows}]
        if gear:
            tables.append({"title": "The gear for each tier used above",
                           "columns": [col("Tier"), col("Weapon"), col("Armour"), col("Made with")], "rows": gear})
        return {"title": f"Combat GP planner: {label}", "kpis": kpis, "banners": banners, "tables": tables}

    # ------------------------------------------------------------ levelling planner
    def plan(self, p):
        w = self.w
        skill, target = p.get("skill"), int(p.get("target") or 0)
        combat = skill in plan_mod.COMBAT_SKILLS
        setup = self._setup(p.get("loadout"))["setup"] if combat else None
        focus = None if combat else {"category": p.get("category") or None, "use": p.get("use") or None}
        r = w.planner.plan(skill, target, bool(p.get("no_shop")), setup, focus, bool(p.get("use_bank")) and not combat,
                           bool(p.get("assume_tools")) and not combat, p.get("food") or None, int(p.get("stealth") or 0))
        if r.get("error"):
            return {"title": "", "kpis": [], "tables": [], "banners": [{"kind": "bad", "text": r["error"]}]}
        banners = []
        if r["stuck"]:
            banners.append({"kind": "bad", "text": f"Stopped at level {r['reached']}: {r['stuck']}"})
        for n in r["notes"]:
            banners.append({"kind": "bad" if "Top up" in n else "warn", "text": n})
        kpis = [{"v": hrs(r["hours"]), "l": "Total time"}, {"v": num(r["xp_needed"]), "l": f"{skill} XP needed"},
                {"v": str(len(r["segments"])), "l": "Steps"},
                {"v": num(r["kills"]), "l": "Kills"} if combat else {"v": num(r["gp"]), "l": "GP value made"}]
        tables, run, route, detail = [], 0.0, [], []
        names = {s["id"]: s["name"] for s in r.get("supplies", [])}
        for i, g in enumerate(r["segments"], 1):
            run += g["hours"]
            lv = f"{g['from']} > {g['to']}"
            rate = (f"{num(g['xp_hr_start'])} > {num(g['xp_hr_end'])}"
                    if abs(g["xp_hr_end"] - g["xp_hr_start"]) / g["xp_hr_start"] > 0.02 else num(g["xp_hr"]))
            other = ", ".join(f"{k} +{num(v)}" for k, v in sorted(g["xp"].items(), key=lambda kv: -kv[1])
                              if k != skill and v >= 1)
            name = g["name"] + (" (outside your focus)" if g["outside_focus"] else "")
            route.append([str(i), lv, name, rate, hrs(g["hours"]), hrs(run), other or "-"])
            if combat:
                detail.append([str(i), lv, "Kills", cnt(g["kills"]), f"{num(g['kills'] / g['hours'])} / hr"])
                for iid, q in sorted(g["used"].items(), key=lambda kv: -kv[1]):
                    detail.append(["", "", names.get(iid, iid), cnt(q), f"{num(q / g['hours'])} / hr"])
                if g.get("loot"):
                    detail.append(["", "", "Loot", items(g["loot"]), ""])
            else:
                for j, c in enumerate(g["chain"]):
                    detail.append([str(i) if j == 0 else "", lv if j == 0 else "", c["skill"], c["name"],
                                   f"{cnt(c['times'])}x", items(c["makes"]) or "-"])
                if g.get("from_bank"):
                    detail.append(["", "", "Bank", "Take from stock", "", items(g["from_bank"], 8)])
                if g["bought"]:
                    detail.append(["", "", "Shop", "Buy", "", items(g["bought"])])
        if combat and r.get("supplies"):
            tables.append({
                "title": "Supplies", "note": ("Prayers on: " + ", ".join(r["prayers"])) if r.get("prayers") else "No prayers on.",
                "columns": [col("Item"), col("Type"), col("Per hour", True), col("Needed", True), col("You have", True), col("")],
                "rows": [[s["name"], s["kind"], num(s["per_hr"]), cnt(s["total"]), cnt(s["have"]),
                          f"runs out after {hrs(s['runs_out']['hours'])} (level {s['runs_out']['level']})" if s["runs_out"]
                          else "enough"] for s in r["supplies"]]})
        tables.append({"title": "Route",
                       "columns": [col("#"), col("Levels"), col("Do this"), col(f"{skill} XP / hr", True), col("Time", True),
                                   col("Total", True), col("XP in other skills")], "rows": route})
        if combat:
            tables.append({"title": "What each step uses",
                           "columns": [col("#"), col("Levels"), col("Uses"), col("This step", True), col("Rate", True)],
                           "rows": detail})
        else:
            tables.append({"title": "What to do in each step", "note": "Totals for the step's whole level range.",
                           "columns": [col("#"), col("Levels"), col("Skill"), col("Action"), col("Times", True), col("Makes")],
                           "rows": detail})
        ends = []
        if r["made"]:
            ends.append(["You end up with", ", ".join(f"{cnt(x['qty'])} {x['item']}" for x in r["made"][:8])])
        if r.get("from_bank"):
            ends.append(["Taken from your bank and earlier steps", ", ".join(f"{cnt(x['qty'])} {x['item']}" for x in r["from_bank"][:10])])
        if r["bought"]:
            ends.append(["Bought from the shop", ", ".join(f"{cnt(x['qty'])} {x['item']}" for x in r["bought"][:8])])
        if r["other_levels"]:
            ends.append(["Other skills levelled", ", ".join(f"{o['skill']} {o['from']} > {o['to']}" for o in r["other_levels"])])
        if ends:
            tables.append({"title": "Totals", "columns": [col(""), col("")], "rows": ends})
        title = f"{skill} {r['from']} > {r['reached']}" + (f" (aimed for {r['to']})" if r["reached"] < r["to"] else "")
        return {"title": title + (f"  [{r['focus']}]" if r.get("focus") else ""), "kpis": kpis, "banners": banners,
                "tables": tables}

    # ------------------------------------------------------------ production per hour
    def _all_actions(self):
        if self._actions is None:
            self._actions = calc.load(calc.TABLES / "actions.json")
        return self._actions

    def _now_rates(self, quests, no_shop=False, combat=False, loadout=None):
        """Every action available now (plus running quest actions), the zero-time sources and the best chains."""
        w, pl = self.w, self.w.planner
        gold = NS + "GoldPieces"
        pl.reqs.state = w.state                      # the planner moves these while it steps through levels
        pl.rates.state, pl.rates.reqs = w.state, pl.reqs
        rates, passive, _ = calc.action_rates(self._all_actions(), w.state, w.bonuses, pl.reqs, pl.rates, quests=quests)
        banners = []
        if combat:
            lo = self._setup(loadout)
            eng = pl._engine(dict(lo["setup"], levels={}), {k: float(v["xp"]) for k, v in w.state["skills"].items()},
                             SCAN_KILLS)
            if eng.weapon:
                stances = [eng.saved_stance] if eng.saved_stance in eng.stance_options() else None
                fights = eng.rates(None, stances, survival=False)[0]
                for r in fights.values():
                    r["name"] = r["name"].removesuffix(" - plan")     # the planner's engine label
                rates.update(fights)
            else:
                banners.append({"kind": "warn", "text": f"{lo['name']} has no weapon, so combat drops were left out."})
        sources = calc.zero_time_sources(pl.reqs, gold, no_shop, passive, rates=rates)
        best = calc.solve_chains(rates, sources)
        if calc.drop_unsourced_supplies(rates, best):
            best = calc.solve_chains(rates, sources)
        return rates, sources, best, banners

    def produce_items(self, p):
        """The item list for the Production tab: what you can make now, and what unlocks later."""
        w, pl = self.w, self.w.planner
        rates, _, _, _ = self._now_rates(True)
        now = {}
        for r in rates.values():
            for it, q in r["outputs"].items():
                if q > 1e-12:
                    now.setdefault(it, set()).add(bool(r.get("quest")))
        later = {}
        for a in self._all_actions():
            if a["demo_locked"] or (a["category"] and "Quest" in a["category"]) or a.get("quest_gates"):
                continue
            ok, missing, unknown = pl.reqs.action(a)
            if ok:
                continue
            lvl, label = a["level_req"] or 0, (missing or unknown or ["locked"])[0]
            for o in a["outputs"]:
                if o["item"] not in now and (o["item"] not in later or lvl < later[o["item"]][0]):
                    later[o["item"]] = (lvl, label)
        # enemy drops: only counted when the tab's "Include combat drops" is on
        drops = set()
        for e in cmb._objects(calc.TABLES, "Enemy").values():
            d = e.get("Drops") or {}
            drops |= {x["Item"] for x in d.get("FixedItems") or []}
            drops |= {x["Item"] for tb in d.get("ItemTables") or [] for x in tb["Table"]}
        bank = w.state.get("bank", {})
        out, icons = [], {}
        for it in set(now) | set(later) | drops:
            if it not in w.items:
                continue
            icons[it] = w.items[it].get("Icon")
            note = "quest only" if now.get(it) == {True} else ""
            if it not in now and it in drops:
                note = "combat drop"
            out.append({"id": it, "name": w.name(it), "owned": int(bank.get(it, 0)), "worn": False,
                        "have": it in now or it in drops, "needs": "" if it in now or it in drops else later[it][1],
                        "note": note})
        out.sort(key=lambda x: (not x["have"], x["name"]))
        return {"items": out, "icons": {k: v for k, v in icons.items() if v}}

    def _quests(self):
        if self._quest_defs is None:
            self._quest_defs = calc.objects("Quest")
            self._names = {a["id"]: a["name"] for a in self._all_actions()}
            for eid, e in cmb._objects(calc.TABLES, "Enemy").items():
                dn = e.get("DisplayName")
                self._names[eid] = dn.get("Singular", cmb.short(eid)) if isinstance(dn, dict) else (dn or cmb.short(eid))
        return self._quest_defs

    GOAL_VERBS = {"FiremakingBurnRecipe": "burn", "WoodcuttingCutTree": "cut", "MiningMineRock": "mine",
                  "ArtisanCreateRecipe": "make", "ActionGainItem": "get", "CombatKillEnemy": "kill"}

    def _chain_quests(self, steps, rates):
        """Quest notes for the quest actions in a chain, once each."""
        notes = []
        for k in steps:
            if rates[k].get("quest"):
                note = self._quest_note(rates[k]["quest"])
                if note not in notes:
                    notes.append(note)
        return notes

    def _quest_note(self, gate):
        """'quest Tree Trimming, step 3 (burn Treant Remains 17/25)' for a quest action's gate. Reaching the goals
        does not close the step: it stays open (and its actions with it) until you hand the step in."""
        qdef = self._quests().get(gate["Quest"]) or {}
        prog = (self.w.state.get("quests") or {}).get(gate["Quest"]) or {"steps": []}
        si = gate["Step"]
        sdefs = qdef.get("Steps") or []
        sdef = sdefs[si] if si < len(sdefs) else {}
        sst = prog["steps"][si] if si < len(prog["steps"]) else {"tasks": []}
        goals = []
        for ti, task in enumerate(sdef.get("TasksInOrder") or []):
            tst = sst["tasks"][ti] if ti < len(sst["tasks"]) else {"status": 0, "goals": {}}
            if tst["status"] == 2:
                continue
            for g in task.get("Goals") or []:
                d = g["Data"]
                done = min(tst["goals"].get(d["Id"], {}).get("progress", 0), d["MaxProgress"])
                what = d.get("Recipe") or d.get("Item") or d.get("Enemy")
                name = (self._names.get(what) or self.w.name(what)) if what else ""
                goals.append(f"{self.GOAL_VERBS.get(cmb.short(g['Type']), 'do')} {name} {cnt(done)}/{d['MaxProgress']}")
            for c in (task.get("Costs") or {}).get("Items") or []:
                goals.append(f"hand in {c['Quantity']} {self.w.name(c['Item'])}")
        return (f"quest {qdef.get('DisplayName', cmb.short(gate['Quest']))}, step {si + 1}"
                + (f" ({'; '.join(goals)})" if goals else ""))

    def produce(self, p):
        w = self.w
        gold = NS + "GoldPieces"
        item = p.get("item") or ""
        quests = bool(p.get("quests", True))
        rates, sources, best, banners = self._now_rates(quests, bool(p.get("no_shop")), bool(p.get("combat")),
                                                        p.get("loadout"))
        tables, kpis, title = [], [], "Production per hour"
        if item:
            name = w.name(item)
            title = f"Making {name}"
            found, blocked = [], {}
            for key, r in rates.items():
                if r["outputs"].get(item, 0) <= 1e-12:
                    continue
                steps, _shop, _passive, missing = calc.expand(key, rates, best)
                if missing:
                    blocked[r["name"]] = w.name(missing)
                    continue
                total_ms = sum(c * rates[k]["ms"] for k, c in steps.items())
                f = HOUR / total_ms
                net = sum(c * (rates[k]["outputs"].get(item, 0) - rates[k]["inputs"].get(item, 0))
                          for k, c in steps.items()) * f
                if net <= 1e-9:
                    continue
                row, _ = calc.chain_row(key, rates, best, sources, w.name, w.price, gold)
                found.append((net, key, steps, row, self._chain_quests(steps, rates)))
            found.sort(key=lambda x: -x[0])
            have = w.state.get("bank", {}).get(item, 0)
            if found:
                net, key, steps, row, notes = found[0]
                kpis = [{"v": num(net), "l": f"{name} per hour (best)"},
                        {"v": f"{3600 / net:.1f} s" if net < 3600 else f"{3_600_000 / net:.0f} ms", "l": "per item"},
                        {"v": num(row["xp_total_hr"]), "l": "XP / hr on the way (all skills)"},
                        {"v": cnt(have), "l": "in your bank"}]
                if notes:
                    after = next((x for x in found[1:] if not x[4]), None)
                    banners.append({"kind": "warn", "text":
                        "The best way uses a quest action, " + "; ".join(notes) + ". It stays open until you hand that "
                        "quest step in." + (f" After that the best is {after[3]['name']} at {num(after[0])} / hr." if after else "")})
            else:
                banners.append({"kind": "bad", "text": f"Nothing you can do now makes {name}."
                                + (" Producers that lack an input: " + ", ".join(f"{a} (no {m})" for a, m in list(blocked.items())[:5])
                                   if blocked else "")})
            if item in sources and sources[item][0] == "shop":
                banners.append({"kind": "ok", "text": f"The shop also sells {name} for {num(sources[item][1])} gp each (no time)."})
            tables.append({
                "title": f"Ways to make {name}",
                "note": "Each row counts the time to gather and make every ingredient from nothing, side products included." + PASSIVE_NOTE,
                "columns": [col("Do this"), col("Skill"), col(f"{name} / hr", True), col("XP / hr (all)", True),
                            col("GP / hr if sold", True), col("Chain"), col("Notes")],
                "rows": [[row["name"], row["skill"], num(n), num(row["xp_total_hr"]), num(row["gp_hr"]),
                          " > ".join(s["name"] for s in row["steps"]) if len(row["steps"]) > 1 else "-",
                          "; ".join(notes) or "-"]
                         for n, key, steps, row, notes in found[:15]]})
            if found:
                row = found[0][3]
                tables.append({
                    "title": f"Best chain: {row['name']}",
                    "columns": [col("Skill"), col("Step"), col("Times / hr", True), col("Time share", True),
                                col("Uses / hr"), col("Makes / hr")],
                    "rows": [[s["skill"], s["name"], cnt(s["per_hr"]), f"{s['share']:.0%}",
                              ", ".join(f"{cnt(q)} {n}" for q, n in s["uses"]) or "-",
                              ", ".join(f"{cnt(q)} {n}" for q, n in s["makes"]) or "-"] for s in row["steps"]]
                    + [["Shop", "Buy", "", "", ", ".join(f"{cnt(x['per_hr'])} {x['item']}" for x in row["shop"]), ""]
                       for _ in [0] if row["shop"]]})
        # the quest actions open right now, on their own
        qrows = []
        for key, r in rates.items():
            if not r.get("quest"):
                continue
            row, _ = calc.chain_row(key, rates, best, sources, w.name, w.price, gold)
            if row:
                qrows.append([r["name"], r["skill"], cnt(row["per_hr"]),
                              ", ".join(f"{cnt(q * row['per_hr'])} {w.name(i)}" for i, q in r["outputs"].items()),
                              num(row["xp_hr"]), num(row["gp_hr"]), self._quest_note(r["quest"])])
        if qrows:
            tables.append({"title": "Quest actions open now",
                           "note": "No limit while the quest step is open: reaching its goals doesn't close it, handing "
                                   "the step in does, and these actions go with it.",
                           "columns": [col("Action"), col("Skill"), col("Times / hr", True), col("Makes / hr"),
                                       col("XP / hr", True), col("GP / hr", True), col("Quest step")],
                           "rows": qrows})
        elif quests:
            banners.append({"kind": "ok", "text": "No quest actions are open in this save right now."})
        if not item:
            banners.insert(0, {"kind": "ok", "text": "Pick an item to see the fastest way to make it."})
        return {"title": title, "kpis": kpis, "banners": banners, "tables": tables}


    # ------------------------------------------------------------ Boost?
    def boost(self, p):
        """Every booster for one skill: what it gives, its effect on your best action, how to get it and how long."""
        w, pl = self.w, self.w.planner
        skill = p.get("skill")
        if not skill:
            raise ValueError("Pick a skill.")
        gold = NS + "GoldPieces"
        if self._boosters is None:
            self._boosters = boost_mod.Boosters(w, self._all_actions())
        pl.reqs.state = w.state
        pl.rates.state, pl.rates.reqs = w.state, pl.reqs
        rates, passive, _ = calc.action_rates(pl.actions, w.state, w.bonuses, pl.reqs, pl.rates)
        sources = calc.zero_time_sources(pl.reqs, gold, passive_rows=passive, rates=rates)
        best = calc.solve_chains(rates, sources)
        if calc.drop_unsourced_supplies(rates, best):
            best = calc.solve_chains(rates, sources)
        rows = []
        for key, r in rates.items():
            if not r.get("converter"):
                row, _ = calc.chain_row(key, rates, best, sources, w.name, w.price, gold)
                if row:
                    rows.append(row)
        mine = [r for r in rows if r["skill"] == skill]
        top = max(mine, key=lambda r: r["xp_hr"], default=None)
        gp_hr = max((r["gp_hr"] for r in rows), default=0)
        setup = self._setup(p.get("loadout"))["setup"]
        found, tables = self._boosters.build(skill, setup, rates, sources, best, top["key"] if top else None, gp_hr, rows,
                                             p.get("section") or None, bool(p.get("hide_have")))
        have = sum(1 for f in found if f["have"])
        can = sum(1 for f in found if f["ok"] and not f["have"])
        kpis = [{"v": str(len(found)), "l": f"Boosters for {skill}"}, {"v": str(have), "l": "You have"},
                {"v": str(can), "l": "You can get now"},
                {"v": str(len(found) - have - can), "l": "Locked (shown grey)"}]
        banners = []
        if top:
            banners.append({"kind": "ok", "text": f"Effect is measured on your best {skill} action now, {top['name']} "
                                                  f"({num(top['xp_hr'])} XP/hr). Times use your levels, your bank and the "
                                                  "loadout above for combat drops; shop prices are turned into time at your "
                                                  f"best gold rate ({num(gp_hr)} gp/hr)."})
        return {"title": f"Boost {skill}", "kpis": kpis, "banners": banners, "tables": tables}

    # ------------------------------------------------------------ combat
    @staticmethod
    def _survival(sv, food):
        v = sv["verdict"]
        if v == "death" and sv["deaths_per_hr"] > 0:
            return "bad", f"can die (about every {1 / sv['deaths_per_hr']:.1f} h)"
        if v == "death":
            return "bad", f"no food: dies after {num(sv['minutes_without_food'])} min"
        if v == "runs_out":
            return ("warn" if sv["hours_food_lasts"] < 8 else "ok"), f"{food or 'food'} lasts {hrs(sv['hours_food_lasts'])}"
        return "ok", "eats, safe" if v == "eats" else "safe"

    def combat(self, p):
        w = self.w
        lo = self._setup(p.get("loadout"))
        setup = dict(lo["setup"], levels={})
        food = w.name(setup["food"]) if setup.get("food") else None
        slayer = p.get("mode") == "slayer"
        if slayer:
            masters = w.init()["masters"]
            m = next((x for x in masters if x["id"] == p.get("master")), None)
            if not m:
                raise ValueError("Pick a slayer master.")
            target = {"kind": "slayer", "master": m["id"], "stance": setup.get("stance"), "blocked": m["blocked"]}
        else:
            if not p.get("enemy"):
                raise ValueError("Pick an enemy.")
            target = {"kind": "enemy", "enemy": p["enemy"], "area": p.get("area") or None, "stance": None}
        r = w.simulate({"setup": setup, "target": target})
        if r.get("error"):
            return {"title": "", "kpis": [], "tables": [], "banners": [{"kind": "bad", "text": r["error"]}]}
        banners, tables = [], []
        if slayer:
            kpis = [{"v": num(r["xp_hr_total"]), "l": "Combat XP / hr"}, {"v": num(r["gp_hr"]), "l": "GP / hr"},
                    {"v": num(r["food_hr"]), "l": f"{food or 'Food'} / hr"}, {"v": f"{r['seconds_per_kill']:.1f}s", "l": "Average kill"}]
            if r["stuck"]:
                banners.append({"kind": "bad", "text": "Auto-slayer would stall on: " + ", ".join(r["stuck"]) + ". Block them."})
            if r["deaths"]:
                banners.append({"kind": "bad", "text": "You can die fighting: " + ", ".join(r["deaths"])})
            rows = []
            for x in r["rows"]:
                if "seconds" not in x:
                    rows.append([x["name"], x["area"], "-", "-", "-", "-", "blocked" if x["blocked"] else "cannot kill"])
                    continue
                gain = ("blocked" if x["blocked"] else
                        f"{(x['if_blocked'] - r['xp_hr_total']) / r['xp_hr_total']:+.1%}" if x.get("if_blocked") else "-")
                rows.append([x["name"], x["area"], f"{x['seconds']:.1f}s", num(x["xp_hr"]), gain,
                             f"{x.get('share', 0):.0%}", self._survival(x["survival"], food)[1]])
            tables.append({"title": f"{r['master']} auto-slayer, {r['stance']} stance: enemies, weakest XP first",
                           "note": f"Tasks of {r['task_length'][0]}-{r['task_length'][1]} kills. Saved blocks are applied.",
                           "columns": [col("Enemy"), col("Area"), col("Time / kill", True), col("XP / hr on it", True),
                                       col("If blocked", True), col("Share of time", True), col("Survival")], "rows": rows})
            return {"title": f"{r['master']} with {lo['name']}", "kpis": kpis, "banners": banners, "tables": tables}
        if not r["results"]:
            return {"title": r["enemy"], "kpis": [], "tables": [],
                    "banners": [{"kind": "bad", "text": f"Can't kill {r['enemy']} with this loadout in under 15 minutes."}]}
        best = max(r["results"], key=lambda s: s["xp_hr_total"])
        kind, text = self._survival(best["combat"]["survival"], food)
        if kind != "ok" or "lasts" in text:
            banners.append({"kind": kind, "text": f"{best['stance']}: {text}"})
        kpis = [{"v": num(best["xp_hr_total"]), "l": f"Combat XP / hr ({best['stance']})"}, {"v": num(best["kills_hr"]), "l": "Kills / hr"},
                {"v": num(best["gp_hr"]), "l": "GP / hr"},
                {"v": f"{best['combat']['hit']:.0%}", "l": f"Hit chance, max hit {best['combat']['max_hit']}"}]
        pray = lambda s: s["combat"].get("prayer_points", 0) * s["kills_hr"]
        tables.append({"title": "By stance", "columns": [col("Stance"), col("Kills / hr", True), col("Combat XP / hr", True),
                                                          col("GP / hr", True), col("Food / hr", True),
                                                          col("Prayer pts / hr", True), col("Survival")],
                       "rows": [[s["stance"] + (" (saved)" if s["stance_id"] == r["saved_stance"] else ""), num(s["kills_hr"]),
                                 num(s["xp_hr_total"]), num(s["gp_hr"]), num(s["combat"]["survival"]["food_per_hr"]),
                                 num(pray(s)), self._survival(s["combat"]["survival"], food)[1]] for s in r["results"]]})
        tables.append({"title": f"{best['stance']}: XP per hour", "columns": [col("Skill"), col("XP / hr", True)],
                       "rows": [[k, num(v)] for k, v in sorted(best["xp_hr"].items(), key=lambda kv: -kv[1])]})
        if best["uses"]:
            tables.append({"title": f"{best['stance']}: used per hour", "columns": [col("Item"), col("Per hour", True)],
                           "rows": [[u["item"], num(u["per_hr"])] for u in best["uses"]]})
        if best["loot"]:
            tables.append({"title": f"{best['stance']}: loot per hour", "columns": [col("Item"), col("Per hour", True), col("GP / hr", True)],
                           "rows": [[x["item"], num(x["per_hr"]), num(x["gp_hr"])] for x in best["loot"][:10]]})
        if r.get("history"):
            h = r["history"]
            banners.append({"kind": "ok", "text": f"Your save has {h['kills']:,} recorded kills at {h['seconds']:.1f}s each; "
                                                  f"the simulation says {best['seconds_per_kill']:.1f}s."})
        return {"title": f"{r['enemy']} ({r['area']}) with {lo['name']}", "kpis": kpis, "banners": banners, "tables": tables}
