"""Local web pages built on your save:

  /         combat simulator: try equipment, stance, spell, prayers, food and levels
            against one enemy or a Slayer master's auto-slayer
  /planner  levelling planner: the fastest route to a target level in any skill

    python -I sim_server.py            # opens http://127.0.0.1:8765
    python -I sim_server.py --port 9000 --no-browser

Starts from your save (run read_save.py first; the page's "Reload save" button
re-reads it). Listens on this computer only. Stop it with Ctrl+C.
"""
import argparse
import json
import subprocess
import sys
import threading
import webbrowser
from collections import defaultdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))  # sibling modules, also under python -I

import calculator as calc  # noqa: E402
import combat as cmb       # noqa: E402
import planner as plan_mod  # noqa: E402
import game_api             # noqa: E402

NS = cmb.NS
short = cmb.short
HOUR = 3_600_000
PAGE = ROOT / "sim.html"
PLANNER_PAGE = ROOT / "planner.html"


class World:
    """Save state, bonuses and lookup tables; rebuilt by reload()."""

    def __init__(self):
        self.lock = threading.Lock()
        self.reload(run_reader=False)

    def reload(self, run_reader=True):
        if run_reader:
            subprocess.run([sys.executable, "-I", str(ROOT / "read_save.py")], cwd=ROOT, check=True,
                           stdout=subprocess.DEVNULL)
        for p in (calc.TABLES / "actions.json", calc.SAVE / "player_state.json", calc.SAVE / "active_bonuses.json"):
            if not p.exists():
                sys.exit(f"missing {p} - run build_tables.py and read_save.py first")
        self.state = calc.load(calc.SAVE / "player_state.json")
        self.active = calc.load(calc.SAVE / "active_bonuses.json")["active"]
        self.bonuses = calc.Bonuses(self.active, False)
        self.items = calc.objects("Item")
        self.init_payload = None
        self.planner = plan_mod.Planner(self)
        if not hasattr(self, "game"):
            self.game = game_api.GameViews(self)   # view models for the in-game panel

    # ---------------------------------------------------------------- helpers
    def xp_mult(self, skill):
        blank = {"skill": skill, "category": None, "subcategory": None, "id": None, "raw_extra": {}, "outputs": []}
        return 1 + self.bonuses.total(blank, "PercentXPGained")

    def name(self, iid):
        dn = self.items.get(iid, {}).get("DisplayName")
        return dn.get("Singular", short(iid)) if isinstance(dn, dict) else (dn or short(iid))

    def price(self, iid):
        sell = self.items.get(iid, {}).get("SellsFor") or {}
        return sell.get("Quantity", 0) if sell.get("Currency") == NS + "GoldPieces" else 0

    @staticmethod
    def level_reqs(reqs):
        out, other = [], []
        for r in reqs or []:
            t = short(r["Type"])
            d = r.get("Data") or {}
            if t == "Level":
                out.append({"skill": short(d["Component"]["Entity"]), "level": d["Level"]})
            elif t != "DemoLocked":
                other.append(t)
        return out, other

    def engine(self, setup):
        state = cmb.with_levels(self.state, setup.get("levels") or {})
        reqs = calc.Requirements(state)
        return cmb.Combat(calc.TABLES, state, reqs, self.items, self.active, self.xp_mult, setup, "sim")

    # ---------------------------------------------------------------- init payload
    def init(self):
        if self.init_payload:
            return self.init_payload
        st = self.state
        O = lambda n: cmb._objects(calc.TABLES, n)
        owned = defaultdict(int, st.get("bank") or {})
        worn_any = {s["item"] for gear in [st["equipment"]] + list((st.get("equipment_loadouts") or {}).values())
                    for s in gear.values()}

        slots = []
        types = {}
        for iid, it in self.items.items():
            eq = (it.get("Capabilities") or {}).get(NS + "Equipment")
            if not eq:
                continue
            if any(short(r["Type"]) == "DemoLocked" for r in eq.get("Requirements") or []):
                continue
            wcap = (it.get("Capabilities") or {}).get(NS + "Weapon") or {}
            lv, other = self.level_reqs(eq.get("Requirements"))
            types.setdefault(eq["Type"], []).append({
                "id": iid, "name": self.name(iid), "owned": owned.get(iid, 0), "worn": iid in worn_any,
                "reqs": lv, "other_reqs": other,
                "two_handed": NS + "Offhand" in (eq.get("OccupiesSlots") or []),
                "weapon_type": wcap.get("Type"), "elements": wcap.get("Elements") or [],
                "ammo_needed": wcap.get("RequiredAmmoType"), "ammo_type": eq.get("AmmoType"),
            })
        for lst in types.values():
            lst.sort(key=lambda x: (not (x["owned"] or x["worn"]), x["name"]))
        for sid, s in O("EquipmentSlot").items():
            slots.append({"id": short(sid), "name": s["DisplayName"], "items": types.get(s["FitsType"], [])})

        stances = {sid: {"name": s["DisplayName"], "style": short(s["CombatStyle"]),
                         "trains": [short(g["Component"]["Entity"]) for g in s["ExperienceGain"]]}
                   for sid, s in O("AttackStance").items()}
        weapon_types = {wid: w["Stances"] for wid, w in O("WeaponType").items()}
        spells = []
        for sid, s in O("MagicSpell").items():
            lv, _ = self.level_reqs(s.get("UnlockRequirements"))
            spells.append({"id": sid, "name": s["DisplayName"], "element": s["Element"],
                           "max_hit": next((x["Value"] for x in s["Stats"]["Stats"] if x["Stat"] == NS + "SpellMaxHit"), 0),
                           "reqs": lv, "runes": [f"{c['Quantity']} {self.name(c['Item'])}" for c in s["RuneCosts"]]})
        prayers = []
        for pid, p in O("Prayer").items():
            if short(p.get("FitsInSlot")) != "CombatPrayer":
                continue
            lv, other = self.level_reqs(p.get("UnlockRequirements"))
            if any(short(r["Type"]) == "DemoLocked" for r in p.get("UnlockRequirements") or []):
                continue
            prayers.append({"id": pid, "name": p["DisplayName"], "reqs": lv, "cost": (p.get("Cost") or {}).get("Quantity", 0)})
        prayers.sort(key=lambda x: (x["reqs"][0]["level"] if x["reqs"] else 0, x["name"]))
        prayer_slots = []
        for sid, s in O("PrayerSlot").items():
            if short(s["FitsType"]) == "CombatPrayer":
                lv, _ = self.level_reqs(s.get("UnlockRequirements"))
                prayer_slots.append({"id": short(sid), "reqs": lv})
        foods = []
        for iid, it in self.items.items():
            f = (it.get("Capabilities") or {}).get(NS + "Food")
            if f:
                foods.append({"id": iid, "name": self.name(iid), "heal": f["BaseHealing"], "owned": owned.get(iid, 0),
                              "bonus": bool(f.get("Bonuses"))})
        foods.sort(key=lambda x: (not x["owned"], -x["heal"]))

        eng = self.engine(cmb.setup_from_state(st))
        areas = []
        for aid, a in eng.areas.items():
            if short(a["Category"]) == "Quests" or not a["Enemies"]:
                continue
            if any(short(r["Type"]) == "DemoLocked" for r in a.get("UnlockRequirements") or []):
                continue
            ens = []
            for eid in a["Enemies"]:
                en = eng.enemies.get(eid)
                if en and not any(short(r["Type"]) == "DemoLocked" for r in en.get("Requirements") or []):
                    lv = {short(x["Key"]): x["Value"] for x in en["Levels"]}
                    ens.append({"id": eid, "name": en["DisplayName"], "hp": lv.get("Hitpoints", 1) * 10})
            if ens:
                areas.append({"id": aid, "name": a["DisplayName"], "category": short(a["Category"]),
                              "unlocked": eng.area_ok(a) is not False, "enemies": ens})
        masters = []
        saved = (st.get("slayer") or {}).get("masters") or {}
        for mid, m in eng.masters.items():
            ens = eng.master_enemies(mid)
            if not ens:
                continue
            sv = saved.get(mid) or {}
            slots_info = sv.get("block_slots") or {}
            masters.append({
                "id": mid, "name": m["DisplayName"],
                "unlocked": all(eng.reqs.check_raw(r)[0] is not False for r in m.get("UnlockRequirements") or []),
                "enemies": [{"id": e, "name": eng.enemies[e]["DisplayName"], "area": eng.areas[a]["DisplayName"],
                             "area_unlocked": ok} for e, a, ok in ens],
                "block_slots": sum(1 for b in slots_info.values() if b.get("unlocked")) or len(m.get("BlockSlots") or []),
                "block_slots_unlocked": sum(1 for b in slots_info.values() if b.get("unlocked")),
                "blocked": [b["blocked"] for b in slots_info.values() if b.get("blocked")],
                "task": sv.get("task"), "auto_slayer": sv.get("auto_slayer", False),
                "task_length": [m["BaseTaskLength"], m["BaseTaskLength"] + 3 * eng.levels.get(NS + "Slayer", 1)],
            })
        loadouts = [cmb.setup_from_state(st, lid) for lid in cmb.loadout_ids(st)]
        history = {}
        for eid, h in (st.get("loot_history") or {}).items():
            if h["kills"]:
                history[eid] = {"kills": h["kills"], "seconds": h["seconds"] / h["kills"],
                                "xp": {k: v / h["kills"] for k, v in h["xp"].items()}}
        self.init_payload = {
            "character": st["header"]["character"], "levels": {k: v["level"] for k, v in st["skills"].items()},
            "combat_levels": cmb.COMBAT_LEVELS, "loadouts": loadouts, "slots": slots, "stances": stances,
            "weapon_types": weapon_types, "spells": spells, "prayers": prayers, "prayer_slots": prayer_slots,
            "foods": foods, "areas": areas, "masters": masters, "history": history,
            "current_area": (st.get("combat_area") or {}).get("area"), "current_enemy": (st.get("combat_area") or {}).get("enemy"),
        }
        return self.init_payload

    def planner_init(self):
        st = self.state
        loadouts = []
        for lid in cmb.loadout_ids(st):
            s = cmb.setup_from_state(st, lid)
            w = (s["equipment"].get("Weapon") or {}).get("item")
            loadouts.append({"setup": s, "name": s["name"], "worn": s["worn"], "weapon": self.name(w) if w else None,
                             "food": self.name(s["food"]) if s.get("food") else None, "food_qty": s.get("food_qty", 0),
                             "prayers": len(s.get("prayers") or [])})
        return {"character": st["header"]["character"], "skills": self.planner.skills(),
                "max_level": plan_mod.MAX_LEVEL, "loadouts": loadouts}

    # ---------------------------------------------------------------- results
    def summarize(self, r):
        """A fight rate dict -> per-hour figures for the page."""
        kph = HOUR / r["ms"]
        loot = sorted(({"item": self.name(i), "per_hr": q * kph, "gp_hr": q * kph * self.price(i)}
                       for i, q in r["outputs"].items()), key=lambda x: -x["gp_hr"])
        coins = r["currency"].get(NS + "GoldPieces", 0) * kph
        return {
            "stance_id": r["combat"]["stance_id"], "stance": r["combat"]["stance"],
            "kills_hr": kph, "seconds_per_kill": r["ms"] / 1000,
            "xp_hr": {k: v * kph for k, v in r["xp_split"].items()}, "xp_hr_total": r["xp"] * kph,
            "gp_hr": sum(x["gp_hr"] for x in loot) + coins, "coins_hr": coins,
            "other_currency_hr": {short(k): v * kph for k, v in r["currency"].items() if k != NS + "GoldPieces"},
            "loot": loot, "uses": sorted(({"item": self.name(i), "id": i, "per_hr": n * kph, "gp_hr": n * kph * self.price(i)}
                                          for i, n in r["inputs"].items()), key=lambda x: -x["per_hr"]),
            "combat": r["combat"], "note": r.get("note"), "detail": r["detail"],
        }

    def simulate(self, req):
        setup = req["setup"]
        target = req["target"]
        eng = self.engine(setup)
        if not eng.weapon:
            return {"error": "No weapon equipped. Pick a weapon to simulate combat."}
        if target["kind"] == "enemy":
            eid = target["enemy"]
            aid = target.get("area") or eng.area_of(eid)
            stances = [target["stance"]] if target.get("stance") else eng.stance_options()
            out = []
            for sid in stances:
                r = eng.fight(aid, eid, sid)
                if r:
                    out.append(self.summarize(r))
            h = self.init()["history"].get(eid)
            return {"kind": "enemy", "enemy": eng.enemies[eid]["DisplayName"], "area": eng.areas[aid]["DisplayName"],
                    "saved_stance": eng.saved_stance, "results": out, "history": h,
                    "not_viable": [eng.stances[s]["DisplayName"] for s in stances
                                   if s not in {x["stance_id"] for x in out}]}
        # auto-slayer
        blocked = set(target.get("blocked") or [])
        res = eng.slayer(target["master"], target.get("stance") or None, blocked)
        rows, tot_ms, tot_xp, tot_gp, food = [], 0.0, 0.0, 0.0, 0.0
        xp_by = defaultdict(float)
        for x in res["rows"]:
            r = x["rate"]
            row = {"id": x["enemy_id"], "name": eng.enemies[x["enemy_id"]]["DisplayName"],
                   "area": eng.areas[x["area_id"]]["DisplayName"], "blocked": x["blocked"], "unlocked": x["unlocked"]}
            if r:
                s = self.summarize(r)
                row.update(seconds=r["ms"] / 1000, xp_kill=r["xp"], xp_hr=s["xp_hr_total"], gp_kill=s["gp_hr"] / s["kills_hr"],
                           survival=r["combat"]["survival"], note=r.get("note"), combat=r["combat"])
                if not x["blocked"]:
                    tot_ms += r["ms"]
                    tot_xp += r["xp"]
                    tot_gp += row["gp_kill"]
                    food += r["combat"]["survival"]["food_per_hr"] * r["ms"] / HOUR
                    for k, v in r["xp_split"].items():
                        xp_by[k] += v
            rows.append(row)
        active = [x for x in rows if not x["blocked"]]
        stuck = [x["name"] for x in active if "seconds" not in x]
        for x in rows:
            if "seconds" in x and not x["blocked"] and tot_ms > x["seconds"] * 1000:
                rest_ms, rest_xp = tot_ms - x["seconds"] * 1000, tot_xp - x["xp_kill"]
                x["if_blocked"] = rest_xp / rest_ms * HOUR
                x["share"] = x["seconds"] * 1000 / tot_ms
            elif "seconds" in x and x["blocked"]:
                x["if_unblocked"] = (tot_xp + x["xp_kill"]) / (tot_ms + x["seconds"] * 1000) * HOUR
        rows.sort(key=lambda x: x.get("xp_hr", -1))   # weakest first: these are the ones worth blocking
        deaths = [x["name"] for x in active if (x.get("survival") or {}).get("verdict") == "death"]
        return {"kind": "slayer", "master": res["name"], "stance_id": res["stance_id"], "stance": res["stance"],
                "task_length": res["task_length"], "rows": rows, "stuck": stuck, "deaths": deaths,
                "xp_hr_total": tot_xp / tot_ms * HOUR if tot_ms else 0,
                "xp_hr": {k: v / tot_ms * HOUR for k, v in xp_by.items()} if tot_ms else {},
                "gp_hr": tot_gp / tot_ms * HOUR if tot_ms else 0, "food_hr": food / tot_ms * HOUR if tot_ms else 0,
                "seconds_per_kill": tot_ms / 1000 / max(1, len([x for x in active if "seconds" in x]))}


WORLD = None


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def send(self, code, body, ctype="application/json"):
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path in ("/", "/index.html", "/combat"):
            return self.send(200, PAGE.read_bytes(), "text/html; charset=utf-8")
        if path == "/planner":
            return self.send(200, PLANNER_PAGE.read_bytes(), "text/html; charset=utf-8")
        if path == "/api/init":
            with WORLD.lock:
                return self.send(200, WORLD.init())
        if path == "/api/planner/init":
            with WORLD.lock:
                return self.send(200, WORLD.planner_init())
        if path in ("/api/game/init", "/api/game/gear"):
            try:
                with WORLD.lock:
                    if path.endswith("gear"):
                        return self.send(200, WORLD.game.gear())
                    return self.send(200, WORLD.game.init(reload="reload=1" in self.path))
            except Exception as ex:
                import traceback
                traceback.print_exc()
                return self.send(500, {"error": f"{type(ex).__name__}: {ex}"})
        self.send(404, {"error": "not found"})

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        try:
            req = json.loads(self.rfile.read(n) or b"{}")
            if self.path == "/api/simulate":
                with WORLD.lock:
                    return self.send(200, WORLD.simulate(req))
            if self.path == "/api/game/view":
                with WORLD.lock:
                    return self.send(200, WORLD.game.view(req.get("view"), req.get("params")))
            if self.path == "/api/game/compare":
                with WORLD.lock:
                    return self.send(200, WORLD.game.compare(req))
            if self.path == "/api/game/reload":
                with WORLD.lock:
                    return self.send(200, WORLD.game.force_reload())
            if self.path == "/api/planner/reload":
                with WORLD.lock:
                    WORLD.reload()
                    return self.send(200, WORLD.planner_init())
            if self.path == "/api/plan":
                with WORLD.lock:
                    try:
                        res = WORLD.planner.plan(req["skill"], req["target"], bool(req.get("no_shop")),
                                                 req.get("setup"), req.get("focus"), bool(req.get("use_bank")),
                                                 bool(req.get("assume_tools")), req.get("food") or None,
                                                 int(req.get("stealth") or 0))
                    except ValueError as ex:
                        res = {"error": str(ex)}
                    return self.send(200, res)
            if self.path == "/api/reload":
                with WORLD.lock:
                    WORLD.reload()
                    return self.send(200, WORLD.init())
            self.send(404, {"error": "not found"})
        except Exception as ex:  # report to the page instead of dropping the connection
            import traceback
            traceback.print_exc()
            self.send(500, {"error": f"{type(ex).__name__}: {ex}"})


def process_alive(pid):
    """Is another process still running? (os.kill(pid, 0) is not safe on Windows: it terminates the process.)"""
    if sys.platform == "win32":
        import ctypes
        handle = ctypes.windll.kernel32.OpenProcess(0x00100000, False, pid)   # SYNCHRONIZE
        if not handle:
            return False
        alive = ctypes.windll.kernel32.WaitForSingleObject(handle, 0) == 0x102  # WAIT_TIMEOUT: still running
        ctypes.windll.kernel32.CloseHandle(handle)
        return alive
    import os
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        pass
    return True


def exit_with(pid, srv):
    """Stop the server once process `pid` (the game, when the in-game panel started us) has gone."""
    import time
    while process_alive(pid):
        time.sleep(5)
    srv.shutdown()


class Server(ThreadingHTTPServer):
    """One server per port. Python's HTTP server sets SO_REUSEADDR, which on Windows lets a second process
    bind the same port silently; use an exclusive bind there so a second copy fails and hands over."""
    allow_reuse_address = sys.platform != "win32"

    def server_bind(self):
        import socket
        if sys.platform == "win32":
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


def write_ready(path, port, pid):
    """Tell the in-game panel the server is listening (it waits for this file instead of probing the port,
    because a refused connection on Windows takes about 2 seconds to fail)."""
    import os
    tmp = Path(str(path) + ".tmp")
    tmp.write_text(json.dumps({"port": port, "pid": pid}), encoding="utf-8")
    os.replace(tmp, path)


def main():
    global WORLD
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--no-browser", action="store_true")
    ap.add_argument("--exit-with", type=int, metavar="PID", help="stop when this process exits (used by the in-game panel)")
    ap.add_argument("--ready-file", metavar="PATH", help="write this file once listening (used by the in-game panel)")
    args = ap.parse_args()
    try:
        srv = Server(("127.0.0.1", args.port), Handler)
    except OSError as ex:
        # Port taken: usually a server that is already running, so let the panel use that one.
        if args.ready_file:
            write_ready(args.ready_file, args.port, None)
        sys.exit(f"port {args.port} is in use ({ex}); is sim_server.py already running?")
    WORLD = World()
    if args.exit_with:
        threading.Thread(target=exit_with, args=(args.exit_with, srv), daemon=True).start()
    if args.ready_file:
        import os
        write_ready(args.ready_file, args.port, os.getpid())
    url = f"http://127.0.0.1:{args.port}/"
    print(f"Combat simulator for {WORLD.state['header']['character']}: {url}")
    print(f"Levelling planner: {url}planner  (Ctrl+C to stop)")
    if not args.no_browser:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        if args.ready_file:
            Path(args.ready_file).unlink(missing_ok=True)


if __name__ == "__main__":
    main()
