"""Every way to boost one skill, with what it gives and how to get it (the panel's "Boost?" tab).

A booster is anything with a bonus that applies to the skill: toolbelt items and potions, food, shop upgrades,
Mastery Guild ranks, Astrology nodes, pets, equipment sets and temporary events. For each one this works out:
  - the boost itself, in words (from the game data);
  - its effect on your current best action in the skill (the same action recomputed with the bonus added);
  - every way to get it (Thieving drop, combat drop, recipe, shop, opening, upgrade, quest reward), the levels it
    needs and, where you can get it now, how long it takes with your levels and the chosen combat loadout.
"""
import math
import re
from collections import defaultdict

import calculator as calc
import combat as cmb

NS = calc.NS
HOUR = 3_600_000
GOLD = NS + "GoldPieces"
SCAN_KILLS = 60
T90 = math.log(10)          # a drop with chance p per try: 90% of the time it comes within ln(10)/p tries

KINDS = {   # source_type -> (section, label)
    "Item": ("items", "Item"), "ShopPurchase": ("shop", "Shop upgrade"),
    "MasteryGuildBonus": ("mastery", "Mastery Guild"), "AstrologyNode": ("astrology", "Astrology node"),
    "AstrologyConstellation": ("astrology", "Astrology"), "Pet": ("other", "Pet"),
    "EquipmentSetBonus": ("other", "Equipment set"), "FiremakingBonfire": ("other", "Bonfire"),
    "RareBonusesEvent": ("other", "Rare event"), "AstrologyBonusesEvent": ("other", "Astrology event"),
    "ThievingDistraction": ("other", "Distraction"),
    "AgilityTechnique": ("other", "Agility technique"), "HuntingJournalBonus": ("other", "Hunting journal"),
}
SECTIONS = [("items", "Items, potions and food"), ("shop", "Shop upgrades"), ("mastery", "Mastery Guild ranks"),
            ("astrology", "Astrology"), ("other", "Pets, sets and events")]


def words(stat):
    s = re.sub(r"^(Percent|Flat)", "", stat)
    s = re.sub(r"(?<=[a-z])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])", " ", s).lower()
    return {"xp gained": "XP", "x p gained": "XP"}.get(s, s).replace("xp ", "XP ")


def boost_text(rows, short_name):
    """[bonus rows] -> '+5% doubling chance, -3% interval (Oak Tree)'."""
    parts = []
    for b in rows:
        v, stat = b["value"], b["stat"]
        pct = stat.startswith("Percent") or stat.endswith("Chance") or "Percent" in stat
        num = f"{v * 100:+.3g}%" if pct else f"{v:+g}"
        text = f"{num} {words(calc_stat(stat))}"
        ctx = {k: v for k, v in (b.get("context") or {}).items()
               if k != "Component" and isinstance(v, str) and not v.startswith("Placeholder")}
        if ctx:
            text += " (" + ", ".join(short_name(v) for v in ctx.values() if isinstance(v, str)) + ")"
        if b.get("condition") or b.get("scaling") or b.get("from_context"):
            text += " (conditional)"
        parts.append(text)
    return ", ".join(dict.fromkeys(parts))


def calc_stat(stat):
    return calc.GLOBAL_STATS.get(stat, stat)


def hrs(h):
    if h is None:
        return "-"
    if h < 1 / 60:
        return "under 1 min"
    if h < 1:
        return f"{h * 60:.0f} min"
    return f"{h:.1f} h" if h < 48 else f"{h / 24:.1f} days"


class Boosters:
    def __init__(self, world, actions):
        self.w = world
        self.actions = actions
        O = lambda t: calc.objects(t) if (calc.TABLES / "objects" / f"{t}.json").exists() else {}
        self.sources = defaultdict(list)                 # (type, id) -> bonus rows
        for b in calc.load(calc.TABLES / "bonus_sources.json"):
            self.sources[(b["source_type"], b["source_id"])].append(b)
        self.shop = {s["id"]: s for s in calc.load(calc.TABLES / "shop_purchases.json")}
        self.upgrades = calc.load(calc.TABLES / "item_upgrades.json")
        self.enemies = O("Enemy")
        self.areas = O("CombatArea")
        self.thieving = O("ThievingRecipe")
        self.quests = O("Quest")
        self.mg_bonus, self.mg_rank = O("MasteryGuildBonus"), O("MasteryGuildRank")
        self.constellations, self.nodes = O("AstrologyConstellation"), O("AstrologyNode")
        self.pets, self.sets = O("Pet"), O("EquipmentSetBonus")
        self.raw = {t: O(t) for t in ("FiremakingBonfire", "RareBonusesEvent", "ThievingDistraction",
                                      "AgilityTechnique", "AstrologyBonusesEvent", "HuntingJournalBonus")}
        self.node_constellation = {}
        for cid, c in self.constellations.items():
            for n in (c.get("MinorNodes") or []) + (c.get("MajorNodes") or []):
                self.node_constellation[n] = cid
        self.dropped_by = defaultdict(list)              # item -> [(enemy id, chance per kill, qty)]
        for eid, e in self.enemies.items():
            d = e.get("Drops") or {}
            for x in d.get("FixedItems") or []:
                self.dropped_by[x["Item"]].append((eid, 1.0, (x["MinQuantity"] + x["MaxQuantity"]) / 2))
            for tb in d.get("ItemTables") or []:
                tw = sum(x["Weight"] for x in tb["Table"]) or 1
                for x in tb["Table"]:
                    self.dropped_by[x["Item"]].append((eid, tb["Chance"] * x["Weight"] / tw,
                                                       (x["MinQuantity"] + x["MaxQuantity"]) / 2))
        ev = calc.TABLES / "astrology_events.json"
        self.astro_events = calc.load(ev) if ev.exists() else {}
        self.opened_from = defaultdict(list)             # item -> [(container item, chance)]
        for iid, it in world.items.items():
            cap = (it.get("Capabilities") or {}).get(NS + "Openable")
            if not cap:
                continue
            for x in cap.get("AlwaysDrops") or []:
                self.opened_from[x["Item"]].append((iid, 1.0))
            tw = sum(x["Weight"] for x in cap.get("DropTable") or []) or 1
            for x in cap.get("DropTable") or []:
                self.opened_from[x["Item"]].append((iid, x["Weight"] / tw))

    # ------------------------------------------------------------ helpers
    def name(self, oid):
        if oid in self.node_constellation:
            return f"{self._plain(self.node_constellation[oid])} {self._plain(oid)}"
        return self._plain(oid)

    def _plain(self, oid):
        if oid in self.w.items:
            return self.w.name(oid)
        for table in (self.enemies, self.areas, self.constellations, self.nodes, self.pets, self.sets,
                      self.mg_bonus, self.mg_rank, self.quests, *self.raw.values()):
            if oid in table:
                dn = table[oid].get("DisplayName")
                return dn.get("Singular", cmb.short(oid)) if isinstance(dn, dict) else (dn or cmb.short(oid))
        if oid in self.shop:
            return self.shop[oid]["name"]
        a = next((a for a in self.actions if a["id"] == oid), None)
        return a["name"] if a else cmb.short(oid)

    def _needs(self, requirements):
        """Raw requirement list -> (met, [unmet labels], highest skill level asked for)."""
        unmet, top = [], 0
        for r in requirements or []:
            data = r.get("Data") or {}
            if cmb.short(r["Type"]) == "Level":
                top = max(top, data.get("Level", 0))
            ok, label = self.reqs.check_raw(r)
            if ok is not True:
                unmet.append(label if ok is False else label + "?")
        return not unmet, unmet, top

    @staticmethod
    def _chance(p):
        return "always" if p >= 0.9999 else f"1 in {1 / p:,.0f}" if p < 0.1 else f"{p:.0%}"

    # ------------------------------------------------------------ the view
    def build(self, skill, setup, rates, sources, best_chain, best_key, gp_hr, best_rows, section=None, hide_have=False):
        w = self.w
        self.reqs = calc.Requirements(w.state)
        self.rates, self.best_chain, self.gp_hr, self.best_rows = rates, best_chain, gp_hr, best_rows
        self.setup, self._fights = setup, {}
        active = {(b["source_type"], b["source_id"]) for b in w.active}
        bank = w.state.get("bank", {})
        toolbelt = {s["item"] for slots in w.state.get("toolbelts", {}).values() for s in slots.values()}
        worn = {s["item"] for s in w.state.get("equipment", {}).values()}
        found = []
        for (stype, sid), rows in self.sources.items():
            mine = [b for b in rows if b["entity"] == skill
                    or (b["entity"] is None and b["stat"] in calc.GLOBAL_STATS)]
            if not mine or stype not in KINDS or (section and KINDS[stype][0] != section):
                continue
            if stype == "Item" and not any(b["path"].startswith(("Capabilities.Toolbelt", "Capabilities.Food",
                                                                 "Capabilities.Equipment")) for b in mine):
                continue
            sec, label = KINDS[stype]
            have = (stype, sid) in active or sid in toolbelt or sid in worn
            owned = bank.get(sid, 0) if stype == "Item" else 0
            ways = self._ways(stype, sid, rows)
            ok_now = have or any(wy["met"] for wy in ways)
            level = min((wy["level"] for wy in ways if wy["met"]), default=None)
            if level is None:
                level = min((wy["level"] for wy in ways), default=0)
            fastest = min((wy["hours"] for wy in ways if wy["met"] and wy["hours"] is not None), default=None)
            if hide_have and have:
                continue
            found.append(dict(section=sec, kind=label, id=sid, name=self.name(sid), rows=mine, have=have,
                              owned=owned, ways=ways, ok=ok_now, level=level, hours=fastest))
        effect = self._effects(skill, best_key, found, active)
        tables = []
        for sec, title in SECTIONS:
            items = [f for f in found if f["section"] == sec]
            if not items:
                continue
            items.sort(key=lambda f: (f["level"], not f["ok"], f["name"]))
            rows, dim, good = [], [], []
            for f in items:
                how = "; ".join(wy["text"] for wy in f["ways"][:3]) or "no known source"
                if len(f["ways"]) > 3:
                    how += f" (+{len(f['ways']) - 3} more)"
                needs = sorted({n for wy in f["ways"] for n in wy["needs"]}) if not f["ok"] else []
                status = ("have it" if f["have"] else f"in bank x{f['owned']:,}" if f["owned"]
                          else "can get now" if f["ok"] else "locked")
                rows.append([f["name"], f["kind"], boost_text(f["rows"], self.name), effect.get(f["id"], "-"),
                             how, ", ".join(needs[:3]) or ("-" if f["ok"] else "?"),
                             hrs(f["hours"]) if f["ok"] and not f["have"] else "-", status])
                dim.append(not f["ok"])
                good.append(f["have"] or f["owned"] > 0)
            tables.append({"title": f"{title} ({len(items)})",
                           "columns": [{"label": c, "num": c in ("Time to get",)} for c in
                                       ("Booster", "Type", "Boost", "Effect", "How to get it", "Needs", "Time to get",
                                        "Status")],
                           "rows": rows, "dim": dim, "good": good})
        return found, tables

    # ------------------------------------------------------------ ways to get one booster
    def _ways(self, stype, sid, rows):
        if stype == "Item":
            return self._item_ways(sid)
        if stype == "ShopPurchase":
            return [self._shop_way(sid)] if sid in self.shop else []
        if stype == "MasteryGuildBonus":
            return [self._mastery_way(sid)]
        if stype in ("AstrologyNode", "AstrologyConstellation"):
            return [self._astrology_way(stype, sid)]
        if stype == "Pet":
            return [self._pet_way(sid)]
        if stype == "EquipmentSetBonus":
            s = self.sets.get(sid) or {}
            n = min((b.get("ItemCount", 0) for b in s.get("Bonuses") or []), default=0)
            pieces = ", ".join(self.name(i) for i in s.get("Items") or [])
            return [dict(text=f"wear {n} of: {pieces}", needs=[], met=True, level=0, hours=None)]
        obj = next((t[sid] for t in self.raw.values() if sid in t), {})
        if stype == "ThievingDistraction" and obj.get("LinkedShopPurchase") in self.shop:
            way = self._shop_way(obj["LinkedShopPurchase"])
            way["text"] = "distraction, placed at one location; " + way["text"]
            return [way]
        met, unmet, top = self._needs(obj.get("Requirements") or obj.get("UnlockRequirements"))
        text = {"FiremakingBonfire": "light it in Firemaking (lasts a while)", "RareBonusesEvent": "random rare event",
                "AstrologyBonusesEvent": "random Astrology event", "ThievingDistraction": "buy, then place at a location",
                "ThievingHeistBonus": "Thieving heist", "AgilityTechnique": "train the Agility technique",
                "HuntingJournalBonus": "Hunting journal level"}.get(stype, stype)
        cost = (obj.get("Costs") or {}).get("Currencies") or []
        if cost:
            text += f", costs {cost[0]['Quantity']:,} gp"
        return [dict(text=text, needs=unmet, met=met, level=top, hours=None)]

    def _item_ways(self, iid):
        ways = []
        # recipes and Thieving: every action that outputs it
        for a in self.actions:
            outs = [o for o in a["outputs"] if o["item"] == iid]
            if not outs or a["demo_locked"] or (a["category"] and "Quest" in a["category"]):
                continue
            ok, missing, unknown = self.reqs.action(a)
            r = self.rates.get(a["key"])
            p = sum(o["chance"] for o in outs)
            hours = None
            if ok and r and r["outputs"].get(iid, 0) > 0:
                steps, _, _, miss = calc.expand(a["key"], self.rates, self.best_chain)
                if not miss:
                    total = sum(c * self.rates[k]["ms"] for k, c in steps.items())
                    hours = total / r["outputs"][iid] / HOUR
            if a["skill"] == "Thieving":
                text = f"Thieving: {a['name']}, {self._chance(p)} per pickpocket"
            elif p < 0.9999:
                text = f"{a['skill']}: {a['name']}, {self._chance(p)} per action"
            else:
                text = f"{a['skill']} {a['level_req'] or 1}: make it ({a['name']})"
            if hours is not None and p < 0.5:
                text += f" (90% within {hrs(hours * T90)})"
            ways.append(dict(text=text, needs=missing + [u + "?" for u in unknown], met=ok,
                             level=a["level_req"] or 1, hours=hours))
        # combat drops, timed with the chosen loadout
        for eid, p, qty in self.dropped_by.get(iid, []):
            ways.append(self._combat_way(eid, iid, p, qty))
        # Astrology: item events (sightings) rolled on each study
        ways += self._astro_ways(iid)
        # shop
        for s in self.shop.values():
            if any(x["Item"] == iid for x in s["provides_items"]):
                ways.append(self._shop_way(s["id"]))
        # opening containers
        for box, p in self.opened_from.get(iid, []):
            have = self.w.state.get("bank", {}).get(box, 0)
            ways.append(dict(text=f"open {self.name(box)} ({self._chance(p)})" + (f", you have {have:,}" if have else ""),
                             needs=[], met=bool(have), level=0, hours=None))
        # upgrades
        bank = self.w.state.get("bank", {})
        for u in self.upgrades:
            if u["item"] == iid:
                met, unmet, top = self._needs(u.get("requirements"))
                parts = (u.get("costs") or {}).get("Items") or []
                cost = ", ".join(f"{x['Quantity']} {self.name(x['Item'])}" for x in parts)
                short_of = [self.name(x["Item"]) for x in parts if bank.get(x["Item"], 0) < x["Quantity"]]
                ways.append(dict(text=f"upgrade from {cost}", needs=unmet + [f"{n} (part)" for n in short_of],
                                 met=met and not short_of, level=top, hours=None))
        # quest rewards
        for qid, q in self.quests.items():
            if any(x["Item"] == iid for x in (q.get("Rewards") or {}).get("Items") or []):
                st = (self.w.state.get("quests") or {}).get(qid) or {}
                done = st.get("status") == calc.QUEST_DONE
                ways.append(dict(text=f"quest reward: {self.name(qid)}" + (" (done)" if done else ""),
                                 needs=[] if done else [f"quest {self.name(qid)}"], met=not done, level=0, hours=None))
        return ways

    def _astro_ways(self, iid):
        level = self.w.state["skills"].get("Astrology", {}).get("level", 1)

        def walk(eid, chance, depth=0):
            e = self.astro_events.get(eid)
            if not e or depth > 4:
                return 0.0, 0.0
            if e["kind"] == "AstrologyItemEvent":
                return (chance, e["BaseQuantity"] + level * e["LevelScalingQuantity"] / 2) if e["Item"] == iid else (0.0, 0.0)
            p, q = 0.0, 0.0
            if e["kind"] == "AstrologyTableEvent":
                tw = sum(x["Weight"] for x in e.get("Events") or []) or 1
                for x in e.get("Events") or []:
                    pp, qq = walk(x["Event"], chance * x["Weight"] / tw, depth + 1)
                    p, q = p + pp, max(q, qq)
            return p, q

        cons, hits = [], []
        for a in self.actions:
            if a["type"] != "AstrologyConstellation" or a["demo_locked"]:
                continue
            cons.append(a)
            p, q = 0.0, 0.0
            for eid in (a.get("raw_extra") or {}).get("events") or []:
                e = self.astro_events.get(eid)
                if e:
                    pp, qq = walk(eid, e["Chance"])
                    p, q = p + pp, max(q, qq)
            if p > 0:
                hits.append((a, p, q))
        if not hits:
            return []
        every = len(hits) == len(cons) and len({round(p, 12) for _, p, _ in hits}) == 1
        out = []
        for a, p, q in ([min(hits, key=lambda h: h[0]["level_req"] or 1)] if every else hits[:4]):
            ok, missing, unknown = self.reqs.action(a)
            where = "any constellation" if every else a["name"]
            out.append(dict(text=f"Astrology: {where}, {self._chance(p)} per study (about {q:,.0f} each)",
                            needs=missing + [u + "?" for u in unknown], met=ok, level=a["level_req"] or 1, hours=None))
        return out

    def where_from(self, iid):
        """Every way to get an item, untimed (no fights simulated): for the GP planner."""
        self.reqs = calc.Requirements(self.w.state)
        self.rates, self.best_chain, self.gp_hr, self.setup, self._fights = {}, {}, 0.0, None, {}
        return self._item_ways(iid)

    def shop_way(self, pid):
        self.reqs = calc.Requirements(self.w.state)
        self.best_chain, self.gp_hr = {}, 0.0
        return self._shop_way(pid)

    def _combat_way(self, eid, iid, p, qty):
        e = self.enemies.get(eid) or {}
        area = next((aid for aid, a in self.areas.items() if eid in (a.get("Enemies") or [])
                     and cmb.short(a.get("Category")) != "Quests"), None)
        aname = self.name(area) if area else "a dungeon or event"
        met, unmet, top = self._needs((self.areas.get(area) or {}).get("Requirements")) if area else (False, ["dungeon"], 0)
        if any(cmb.short(r["Type"]) == "DemoLocked" for r in e.get("Requirements") or []):
            met, unmet = False, ["not in the game yet"]
        text = f"Combat: {self.name(eid)} ({aname}), {self._chance(p)} per kill"
        hours = None
        if met and area and self.setup is not None:
            r = self._fight(eid)
            if r and r["outputs"].get(iid, 0) > 0:
                kills_hr = HOUR / r["ms"]
                hours = 1 / (r["outputs"][iid] / max(qty, 1) * kills_hr) if r["outputs"][iid] else None
                text += f", {kills_hr:,.0f} kills/hr with this loadout"
                if hours and p < 0.5:
                    text += f" (90% within {hrs(hours * T90)})"
            elif r is None:
                text += " (this loadout can't kill it in time)"
                met, unmet = False, unmet + ["a stronger loadout"]
        return dict(text=text, needs=unmet, met=met, level=top, hours=hours)

    def _fight(self, eid):
        if eid not in self._fights:
            eng = self.w.planner._engine(dict(self.setup, levels={}),
                                         {k: float(v["xp"]) for k, v in self.w.state["skills"].items()}, SCAN_KILLS)
            fights = {}
            if eng.weapon:
                stances = [eng.saved_stance] if eng.saved_stance in eng.stance_options() else None
                fights, _ = eng.rates({eid}, stances, survival=False)
            self._fights[eid] = min(fights.values(), key=lambda r: r["ms"]) if fights else None
        return self._fights[eid]

    def _shop_way(self, pid):
        s = self.shop[pid]
        met, unmet, top = self._needs(s["requirements"])
        cost = (s["costs"].get("Currencies") or [])
        gp = sum(c["Quantity"] for c in cost if c["Currency"] == GOLD)
        other = ", ".join(f"{c['Quantity']:,} {cmb.short(c['Currency'])}" for c in cost if c["Currency"] != GOLD)
        items = ", ".join(f"{x['Quantity']} {self.name(x['Item'])}" for x in s["costs"].get("Items") or [])
        bought = self.w.state.get("shop_purchases", {}).get(pid, 0)
        text = "shop: " + ", ".join(x for x in (f"{gp:,} gp" if gp else "", other, items) if x)
        if bought:
            text += " (bought)"
        # time: gold at your best gold rate, items at their fastest chain time
        hours = gp / self.gp_hr if gp and self.gp_hr > 0 else 0.0
        for x in s["costs"].get("Items") or []:
            ms = self.best_chain[x["Item"]][0] if x["Item"] in self.best_chain else float("inf")
            have = self.w.state.get("bank", {}).get(x["Item"], 0)
            hours += max(0, x["Quantity"] - have) * ms / HOUR
        if bought or hours == float("inf") or (gp and self.gp_hr <= 0):
            hours = None
        return dict(text=text, needs=unmet, met=met, level=top, hours=hours)

    def _mastery_way(self, bid):
        b = self.mg_bonus.get(bid) or {}
        req = next((r for r in b.get("Requirements") or [] if cmb.short(r["Type"]) == "MasteryGuildRank"), None)
        if not req:
            return dict(text="Mastery Guild", needs=[], met=True, level=0, hours=None)
        data = req["Data"]
        skill = cmb.short(data["Component"]["Entity"])
        ok, label = self.reqs.check("MasteryGuildRank", skill, data)
        info = (self.w.state.get("mastery_ranks") or {}).get(skill) or {}
        pct = (info.get("rank_pct") or {}).get(data["Rank"])
        need = math.ceil(info["total_points"] * pct) if info and pct is not None else None
        text = f"{skill} Mastery Guild: reach {self.name(data['Rank'])}"
        if need is not None:
            text += f" ({info['points_min']}/{need} points)"
        return dict(text=text, needs=[] if ok else [label], met=bool(ok), level=0, hours=None)

    def _astrology_way(self, stype, sid):
        cid = sid if stype == "AstrologyConstellation" else self.node_constellation.get(sid)
        c = self.constellations.get(cid) or {}
        met, unmet, top = self._needs(c.get("Requirements"))
        saved = (self.w.state.get("astrology") or {}).get(cid) or {}
        know = saved.get("knowledge", 0.0)
        if stype == "AstrologyConstellation":
            return dict(text=f"study {self.name(cid)}: bonus per minor node", needs=unmet, met=met, level=top, hours=None)
        major = sid in (c.get("MajorNodes") or [])
        have = len(saved.get("major_nodes" if major else "minor_nodes", []))
        thresholds = c.get("MinorNodeUnlockKnowledge") or []
        if major:
            text = f"study {self.name(cid)}: a major node, after every {c.get('MinorNodesPerMajorNode', 4)} minor nodes"
        else:
            nxt = thresholds[have] if have < len(thresholds) else None
            text = (f"study {self.name(cid)}: pick this minor node"
                    + (f"; next node at {nxt:,} knowledge (you have {know:,.0f})" if nxt else ""))
        return dict(text=text, needs=unmet, met=met, level=top, hours=None)

    def _pet_way(self, pid):
        p = self.pets.get(pid) or {}
        trig = p.get("Trigger") or {}
        data = trig.get("Data") or {}
        skill = cmb.short((data.get("Component") or {}).get("Entity"))
        scale = data.get("ScaleFactor")
        if cmb.short(trig.get("Type")) == "ChancePerExperience" and scale and skill:
            xp_hr = max((r["xp_hr"] for r in self.best_rows if r["skill"] == skill), default=0)
            hours = scale / xp_hr if xp_hr else None
            return dict(text=f"found while training {skill} (on average one per {scale:,.0f} XP)", needs=[],
                        met=True, level=0, hours=hours)
        return dict(text="found by a special goal", needs=[], met=True, level=0, hours=None)

    # ------------------------------------------------------------ effect on your best action
    def _effects(self, skill, best_key, found, active):
        """+X% XP/hr and output on the skill's current best action, with the booster's bonus added."""
        if not best_key:
            return {}
        w = self.w
        a = next((a for a in self.actions if a["key"] == best_key), None)
        if not a:
            return {}
        rates_obj = w.planner.rates
        rates_obj.state, rates_obj.reqs = w.state, w.planner.reqs
        old_b = rates_obj.b

        def measure(bonuses):
            rates_obj.b = bonuses
            r = rates_obj.compute(a)
            return (r["xp"] / r["ms"], sum(r["outputs"].values()) / r["ms"]) if r and r["ms"] else None

        try:
            base = measure(old_b)
            out = {}
            for f in found:
                if f["have"] or base is None:
                    continue
                extra = [dict(b, effective_value=b["value"]) for b in f["rows"]
                         if not (b.get("condition") or b.get("scaling") or b.get("from_context"))]
                if not extra:
                    continue
                now = measure(calc.Bonuses(list(w.active) + extra, False))
                if not now:
                    continue
                dxp, dout = now[0] / base[0] - 1, (now[1] / base[1] - 1) if base[1] else 0
                bits = [f"{dxp:+.0%} XP" if abs(dxp) >= 0.005 else "", f"{dout:+.0%} items" if abs(dout) >= 0.005 else ""]
                out[f["id"]] = ", ".join(b for b in bits if b) or "none on " + a["name"]
            return {k: v + f" ({a['name']})" if not v.startswith("none") else v for k, v in out.items()}
        finally:
            rates_obj.b = old_b
