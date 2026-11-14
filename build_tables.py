"""Normalize the extracted Melvor Idle 2 game data into calculator input tables.

Reads data/raw/Data/Packages/MelvorDemo.json (run unpack_melvor.py first) and
writes data/tables/. Base values only: nothing here applies player bonuses.

    python -I build_tables.py
"""
import csv
import json
import math
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).parent
SRC = ROOT / "data" / "raw" / "Data" / "Packages" / "MelvorDemo.json"
OUT = ROOT / "data" / "tables"
NS = "MelvorBase:"

# skill -> [(EntityConfig key listing the ids, object type holding the records)]
SKILL_SOURCES = {
    "Woodcutting": [("Recipes", "WoodcuttingRecipe")],
    "Mining": [("Recipes", "MiningRecipe")],
    "Fishing": [("Recipes", "FishingRecipe")],
    "Firemaking": [("Recipes", "FiremakingRecipe")],
    "Cooking": [("Recipes", "CookingRecipe")],
    "Herblore": [("Recipes", "HerbloreRecipe")],
    "Thieving": [("Recipes", "ThievingRecipe")],
    "Farming": [("Recipes", "FarmingRecipe"), ("GrainMillRecipes", "GrainMillRecipe")],
    "Hunting": [("Animals", "HuntingAnimal")],
    "Ranching": [("Animals", "RanchingAnimal")],
    "Smithing": [("Recipes", "ArtisanRecipe")],
    "Crafting": [("Recipes", "ArtisanRecipe")],
    "Fletching": [("Recipes", "ArtisanRecipe")],
    "Runecrafting": [("Recipes", "ArtisanRecipe")],
    "Beastcrafting": [("Recipes", "ArtisanRecipe")],
}


def short(i):
    return i[len(NS):] if isinstance(i, str) and i.startswith(NS) else i


def name_of(v):
    return v.get("Singular") if isinstance(v, dict) else v


def io(item, qty, chance=1.0, per="action", **extra):
    """One input/output line. expected_qty is per `per` event."""
    row = {"item": item, "qty": qty, "chance": chance, "expected_qty": round(qty * chance, 6), "per": per}
    row.update(extra)
    return row


def ingredients(rec, key="Ingredients"):
    ing = rec.get(key) or {}
    rows = [io(i["Item"], i["Quantity"]) for i in ing.get("Items") or []]
    rows += [io(c["Currency"], c["Quantity"], kind="currency") for c in ing.get("Currencies") or []]
    return rows


def products(lst, per="action"):
    return [io(p["Item"], p["Quantity"], p.get("Chance", 1.0), per) for p in lst or []]


def weighted(table, base_chance, per="action", label=None):
    """Weighted drop table rolled with probability base_chance."""
    total = sum(e["Weight"] for e in table or []) or 1
    rows = []
    for e in table or []:
        avg = (e["MinQuantity"] + e["MaxQuantity"]) / 2
        extra = {"weight": e["Weight"], "min_qty": e["MinQuantity"], "max_qty": e["MaxQuantity"]}
        if label:
            extra["table"] = label
        rows.append(io(e["Item"], avg, round(base_chance * e["Weight"] / total, 8), per, **extra))
    return rows


def requirements(rec, skill, key="Requirements"):
    """Return (level_req for this skill, demo_locked, other requirements)."""
    level, locked, other = None, False, []
    for r in rec.get(key) or []:
        data = dict(r.get("Data") or {})
        comp = data.pop("Component", None) or {}
        data.pop("IsOr", None) if not data.get("IsOr") else None
        rtype = short(r["Type"])
        if rtype == "DemoLocked":
            locked = True
        elif rtype == "Level" and short(comp.get("Entity")) == skill:
            level = max(level or 0, data["Level"])
        else:
            other.append({"type": rtype, "entity": short(comp.get("Entity")), **data})
    return level, locked, other


# ---- per-type normalizers: return dict of fields merged into the action ----

def n_artisan(r):
    return {"interval_ms": r["BaseInterval"], "steps": r.get("Steps"),
            "inputs": ingredients(r), "outputs": products(r["Products"])}


def n_cooking(r):
    return {**n_artisan(r), "raw_extra": {"Difficulty": r["Difficulty"]}}


def n_herblore(r):
    phases = {k: r[k] for k in ("BasePreparingInterval", "BaseHeatingInterval",
                                "BaseStirringInterval", "BaseBottlingInterval")}
    return {"interval_ms": sum(phases.values()), "steps": r.get("Steps"),
            "inputs": ingredients(r), "outputs": products(r["Products"]),
            "raw_extra": {**phases, "PotionGroup": r["PotionGroup"], "Potency": r["Potency"],
                          "PotionClass": r["PotionClass"]}}


def n_firemaking(r):
    outs = products(r["Products"])
    cur = r.get("CurrencyReward")
    if cur:
        outs.append(io(cur["Currency"], cur["Quantity"], kind="currency"))
    return {"interval_ms": r["BaseInterval"], "inputs": ingredients(r), "outputs": outs}


def n_woodcutting(r):
    # BaseInterval is per hit; a tree takes BaseMaxHealth worth of hits per cut.
    outs = [io(r["Product"]["Item"], r["Product"]["Quantity"], per="cut")]
    outs += products(r["RandomProductsPerCut"], "cut") + products(r["RandomProductsPerHit"], "hit")
    return {"interval_ms": r["BaseInterval"], "inputs": [], "outputs": outs,
            "raw_extra": {"BaseMaxHealth": r["BaseMaxHealth"], "interval_is_per": "hit"}}


def n_mining(r):
    outs = [io(r["Product"]["Item"], r["Product"]["Quantity"], r["BaseProductChance"], "hit")]
    outs += products(r["RandomProductsPerHit"], "hit")
    extra = {k: r[k] for k in ("BaseProductChance", "BarItem", "StoneItem", "EssenceItem") if r.get(k) is not None}
    return {"interval_ms": r["BaseInterval"], "inputs": [], "outputs": outs,
            "raw_extra": {**extra, "interval_is_per": "hit"}}


def n_fishing(r):
    outs = weighted(r["RandomProducts"], r["FishChance"], label="fish")
    if r.get("JunkItem"):
        outs.append(io(r["JunkItem"], 1, round(1 - r["FishChance"], 8), table="junk"))
    outs += products(r["SpecialProducts"])
    lo, hi = r["BaseMinInterval"], r["BaseMaxInterval"]
    return {"interval_ms": (lo + hi) / 2, "interval_min_ms": lo, "interval_max_ms": hi,
            "inputs": [], "outputs": outs,
            "raw_extra": {"FishChance": r["FishChance"], "Location": r["Location"]}}


def n_thieving(r):
    outs = []
    cur = r.get("CurrencyReward")
    if cur:
        avg = (cur["MinQuantity"] + cur["MaxQuantity"]) / 2
        outs.append(io(cur["Currency"], avg, r["BaseCurrencyLootChance"], kind="currency",
                       min_qty=cur["MinQuantity"], max_qty=cur["MaxQuantity"]))
    outs += weighted(r["CommonDropTable"], r["BaseCommonLootChance"], label="common")
    outs += weighted(r["RareDropTable"], r["BaseRareLootChance"], label="rare")
    outs += weighted(r["CompletionDropTable"], 1.0, per="completion", label="completion")
    extra = {k: r[k] for k in ("RecipeType", "Stages", "Damage", "Perception", "BaseCurrencyLootChance",
                               "BaseCommonLootChance", "BaseRareLootChance")}
    return {"interval_ms": r["BaseInterval"], "inputs": [], "outputs": outs, "raw_extra": extra}


def n_farming(r):
    extra = {k: r[k] for k in ("Regrows", "RegrowInterval", "GrownInterval") if r.get(k)}
    extra["passive"] = True
    return {"interval_ms": r["BaseInterval"], "inputs": ingredients(r),
            "outputs": [io(r["Product"]["Item"], r["Product"]["Quantity"], per="harvest")],
            "raw_extra": extra}


def n_grainmill(r):
    return {"interval_ms": None, "inputs": ingredients(r),
            "outputs": [io(r["Product"]["Item"], r["Product"]["Quantity"])],
            "raw_extra": {"passive": True}}


def n_hunting(r):
    keys = ("BaseMinTrackingSteps", "BaseMaxTrackingSteps", "BaseMinTrackInterval", "BaseMaxTrackInterval",
            "BaseMinTrapInterval", "BaseMaxTrapInterval", "BaseTrackSuccessChance", "BaseTrapSuccessChance",
            "BaseJournalTrackingExperience", "BaseJournalTrappingExperience", "BaseRareSpeciesChance",
            "Location", "Tool", "TrapType", "PreferredBait", "EnableTrapless")
    outs = products(r["Products"], "catch") + products(r["SurveyingProducts"], "catch")
    return {"interval_ms": None, "inputs": [], "outputs": outs,
            "raw_extra": {k: r[k] for k in keys}}


def n_ranching(r):
    return {"interval_ms": r["ProductInterval"],
            "inputs": [io(e["Item"], e["Quantity"], per="feed") for e in r["Eats"] or []],
            "outputs": [io(p["Item"], p["Quantity"], per="product_interval") for p in r["Products"] or []],
            "raw_extra": {"passive": True, "BaseVariantInterval": r["BaseVariantInterval"],
                          "LifespanInterval": r["LifespanInterval"], "PurchaseCosts": r["PurchaseCosts"],
                          "RequiresPets": r["RequiresPets"]}}


NORMALIZERS = {
    "ArtisanRecipe": n_artisan, "CookingRecipe": n_cooking, "HerbloreRecipe": n_herblore,
    "FiremakingRecipe": n_firemaking, "WoodcuttingRecipe": n_woodcutting, "MiningRecipe": n_mining,
    "FishingRecipe": n_fishing, "ThievingRecipe": n_thieving, "FarmingRecipe": n_farming,
    "GrainMillRecipe": n_grainmill, "HuntingAnimal": n_hunting, "RanchingAnimal": n_ranching,
}


def base_completion_ms(a):
    """Base time for one XP-granting completion, before any player bonuses.
    See docs/FORMULAS.md. None where the loop has not been verified."""
    t, ms, extra = a["type"], a["interval_ms"], a["raw_extra"]
    if ms is None:
        return None
    if t == "ArtisanRecipe":            # one step per timer fire
        return ms * max(1, a["steps"] or 1)
    if t == "WoodcuttingRecipe":        # one damage per hit, XP when the tree is cut
        return ms * extra["BaseMaxHealth"]
    if t == "MiningRecipe":             # each hit breaks with chance p, guaranteed once progress fills
        p = extra["BaseProductChance"]
        if not p:
            return None
        guaranteed = math.ceil(round(0.9999 / p, 9))
        return round(ms * (1 - (1 - p) ** guaranteed) / p, 3)
    if t in ("CookingRecipe", "FiremakingRecipe", "FishingRecipe"):
        return ms
    return None


def build_actions(objects, config):
    actions, problems = [], []
    by_type = {t: {NS + r["Id"]: r for r in objects[t]} for t in NORMALIZERS}
    used = defaultdict(set)
    for skill, sources in SKILL_SOURCES.items():
        cfg = config[NS + skill]
        for cfg_key, otype in sources:
            for order, ref in enumerate(sorted(cfg.get(cfg_key, []), key=lambda x: x["Priority"])):
                rec = by_type[otype].get(ref["Id"])
                if rec is None:
                    problems.append(f"{skill}.{cfg_key}: {ref['Id']} not found in {otype}")
                    continue
                used[otype].add(ref["Id"])
                level, locked, other = requirements(rec, skill)
                action = {
                    "key": f"{skill}/{rec['Id']}", "skill": skill, "id": NS + rec["Id"],
                    "type": otype, "name": name_of(rec["DisplayName"]),
                    "category": rec.get("Category") or rec.get("Location"),
                    "subcategory": rec.get("Subcategory"), "order": order,
                    "level_req": level if level is not None else rec.get("Level"),
                    "demo_locked": locked, "other_requirements": other,
                    "base_xp": rec["BaseExperience"],
                    "interval_ms": None, "interval_min_ms": None, "interval_max_ms": None,
                    "steps": None, "inputs": [], "outputs": [], "raw_extra": {},
                }
                action.update(NORMALIZERS[otype](rec))
                action["base_completion_ms"] = base_completion_ms(action)
                done = action["base_completion_ms"]
                action["base_xp_per_hour"] = round(action["base_xp"] * 3_600_000 / done, 2) if done else None
                actions.append(action)
    for otype, recs in by_type.items():
        orphans = set(recs) - used[otype]
        if orphans:
            problems.append(f"{otype}: {len(orphans)} records not listed by any skill: {sorted(map(short, orphans))[:8]}")
    return actions, problems


# ---- bonuses ----

def collect_bonuses(objects):
    """Flatten every modifier ({Stat, Value, LocalContext...}) found in any object."""
    rows = []

    def walk(node, path, src_type, src):
        if isinstance(node, dict):
            stat = node.get("Stat")
            if "Value" in node and (isinstance(stat, dict) or "StatGroup" in node):
                ctx = node.get("LocalContext") or {}
                comp = ctx.get("Component") or {}
                rows.append({
                    "source_type": src_type, "source_id": NS + src["Id"],
                    "source_name": name_of(src.get("DisplayName")), "path": path,
                    "stats_type": short(stat.get("StatsType")) if stat else None,
                    "stat": stat.get("Name") if stat else node.get("StatGroup"),
                    "value": node["Value"],
                    "entity": short(comp.get("Entity")), "component": comp.get("Component"),
                    "context": {k: v for k, v in ctx.items() if k != "Component" and v is not None},
                    "from_context": node.get("FromContext"), "condition": node.get("Condition"),
                    "scaling": node.get("Scaling"),
                })
                return
            for k, v in node.items():
                walk(v, f"{path}.{short(k)}" if path else short(k), src_type, src)
        elif isinstance(node, list):
            for v in node:
                walk(v, path, src_type, src)

    for otype, recs in objects.items():
        for rec in recs:
            if isinstance(rec, dict) and "Id" in rec:
                walk(rec, "", otype, rec)
    return rows


# ---- items ----

def mentions_index(objects, item_ids):
    """item id -> object types that mention it anywhere (hint for non-recipe sources)."""
    idx = defaultdict(set)

    def walk(node, otype):
        if isinstance(node, dict):
            for v in node.values():
                walk(v, otype)
        elif isinstance(node, list):
            for v in node:
                walk(v, otype)
        elif isinstance(node, str) and node in item_ids:
            idx[node].add(otype)

    for otype, recs in objects.items():
        if otype != "Item":
            walk(recs, otype)
    return idx


def fmt_io(rows):
    parts = []
    for r in rows:
        s = f"{r['qty']:g}x {short(r['item'])}"
        if r["chance"] != 1:
            s += f" @{r['chance']:g}"
        if r["per"] != "action":
            s += f" /{r['per']}"
        parts.append(s)
    return "; ".join(parts)


def write_json(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=1, ensure_ascii=False), encoding="utf-8")


def write_csv(path, header, rows):
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)


def main():
    if not SRC.exists():
        sys.exit(f"missing {SRC} - run unpack_melvor.py first")
    data = json.loads(SRC.read_text(encoding="utf-8-sig"))
    objects, config = data["Objects"], data["EntityConfig"]

    for otype, recs in objects.items():
        write_json(OUT / "objects" / f"{otype}.json", recs)
    write_json(OUT / "entity_config.json", config)

    actions, problems = build_actions(objects, config)
    write_json(OUT / "actions.json", actions)
    write_csv(OUT / "actions.csv",
              ["key", "skill", "id", "name", "type", "category", "subcategory", "level_req", "demo_locked",
               "base_xp", "interval_ms", "interval_min_ms", "interval_max_ms", "steps", "base_completion_ms",
               "base_xp_per_hour", "inputs", "outputs", "other_requirements", "raw_extra"],
              [[a["key"], a["skill"], short(a["id"]), a["name"], a["type"], short(a["category"]),
                short(a["subcategory"]), a["level_req"], a["demo_locked"], a["base_xp"], a["interval_ms"],
                a["interval_min_ms"], a["interval_max_ms"], a["steps"], a["base_completion_ms"],
                a["base_xp_per_hour"], fmt_io(a["inputs"]),
                fmt_io(a["outputs"]), json.dumps(a["other_requirements"]) if a["other_requirements"] else "",
                json.dumps(a["raw_extra"]) if a["raw_extra"] else ""] for a in actions])
    write_csv(OUT / "action_io.csv",
              ["action", "skill", "demo_locked", "direction", "item", "qty", "chance", "expected_qty", "per", "kind", "table"],
              [[a["key"], a["skill"], a["demo_locked"], d, short(r["item"]), r["qty"], r["chance"],
                r["expected_qty"], r["per"], r.get("kind", "item"), r.get("table", "")]
               for a in actions for d, rows in (("in", a["inputs"]), ("out", a["outputs"])) for r in rows])

    bonuses = collect_bonuses(objects)
    write_json(OUT / "bonus_sources.json", bonuses)
    write_json(OUT / "modifiers.json", {"global": data["GlobalModifierDescriptions"],
                                        "component": data["ComponentModifierDescriptions"]})

    shop = [{"id": NS + s["Id"], "name": s["DisplayName"], "subcategory": s["Subcategory"],
             "buy_limit": s["DefaultBuyLimit"], "costs": s["Costs"], "requirements": s["Requirements"],
             "provides_items": (s["Provides"] or {}).get("Items") or [],
             "provides_currencies": (s["Provides"] or {}).get("Currencies") or [],
             "has_bonuses": bool(((s["Provides"] or {}).get("Bonuses") or {}).get("Component"))}
            for s in objects["ShopPurchase"]]
    write_json(OUT / "shop_purchases.json", shop)
    upgrades = [{"id": NS + u["Id"], "item": u["UpgradedItem"], "qty": u["QuantityPerUpgrade"],
                 "costs": u["Costs"], "requirements": u["Requirements"]} for u in objects["ItemUpgrade"]]
    write_json(OUT / "item_upgrades.json", upgrades)

    # items with producer / consumer indexes (unlocked actions only)
    produced, consumed = defaultdict(list), defaultdict(list)
    for a in actions:
        if a["demo_locked"]:
            continue
        for r in a["outputs"]:
            produced[r["item"]].append(a["key"])
        for r in a["inputs"]:
            consumed[r["item"]].append(a["key"])
    shop_src = defaultdict(list)
    for s in shop:
        for p in s["provides_items"]:
            shop_src[p["Item"]].append(short(s["id"]))
    upg_src = {u["item"]: short(u["id"]) for u in upgrades}
    item_ids = {NS + i["Id"] for i in objects["Item"]}
    mentions = mentions_index(objects, item_ids)
    item_rows = []
    for i in objects["Item"]:
        iid = NS + i["Id"]
        caps = i["Capabilities"] or {}
        sell = i["SellsFor"] or {}
        item_rows.append([i["Id"], name_of(i["DisplayName"]), short(sell.get("Currency")), sell.get("Quantity"),
                          ",".join(short(c) for c in caps),
                          short((caps.get(NS + "Toolbelt") or {}).get("FitsInSlot")),
                          "; ".join(sorted(set(produced[iid]))), "; ".join(sorted(set(consumed[iid]))),
                          "; ".join(shop_src[iid]), upg_src.get(iid, ""),
                          ",".join(sorted(mentions[iid]))])
    write_csv(OUT / "items.csv",
              ["id", "name", "sell_currency", "sell_price", "capabilities", "toolbelt_slot_type", "produced_by",
               "consumed_by", "shop_purchases", "item_upgrade", "mentioned_in_types"], item_rows)

    # ---- report ----
    print("actions per skill (total / demo-locked / usable):")
    per = defaultdict(lambda: [0, 0])
    for a in actions:
        per[a["skill"]][0] += 1
        per[a["skill"]][1] += a["demo_locked"]
    for skill, (total, locked) in per.items():
        print(f"  {skill:14} {total:4} / {locked:4} / {total - locked:4}")
    print(f"total actions: {len(actions)}   bonus rows: {len(bonuses)} "
          f"from {len({(b['source_type'], b['source_id']) for b in bonuses})} sources")
    print("bonus rows by source type:", dict(Counter(b["source_type"] for b in bonuses).most_common(12)))
    for p in problems:
        print("NOTE:", p)

    no_source = []
    for a in actions:
        if a["demo_locked"]:
            continue
        for r in a["inputs"]:
            it = r["item"]
            if r.get("kind") == "currency" or produced[it] or shop_src[it] or it in upg_src:
                continue
            no_source.append((short(it), a["key"]))
    by_item = defaultdict(list)
    for it, key in no_source:
        by_item[it].append(key)
    print(f"\ningredients with no recipe/shop/upgrade source: {len(by_item)}")
    for it, keys in sorted(by_item.items()):
        hint = ",".join(sorted(mentions[NS + it] - set(NORMALIZERS))) or "-"
        print(f"  {it:28} used by {len(keys):3} actions; also mentioned in: {hint}")
    write_json(OUT / "no_known_source.json",
               {it: {"used_by": keys, "mentioned_in": sorted(mentions[NS + it])} for it, keys in sorted(by_item.items())})
    print(f"\nwrote tables to {OUT}")


if __name__ == "__main__":
    main()
