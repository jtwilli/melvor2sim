"""HTML report for calculator.py: best XP/hour and GP/hour per skill, with the
full step-by-step chain behind every number."""
from collections import defaultdict
from datetime import datetime
from html import escape as e

COMBAT_SKILLS = ["Attack", "Strength", "Defence", "Hitpoints", "Ranged", "Magic", "Prayer", "Slayer"]

CSS = """
:root{--bg:#f6f5f1;--card:#fff;--ink:#1c1b19;--muted:#6b6862;--line:#e3e0d8;--accent:#2f6f4f;--accent-bg:#e7f1ea;
--gold:#8a6410;--gold-bg:#f6edd6;--warn:#8a3b1c;--warn-bg:#f8e7df;--chip:#efede7}
@media (prefers-color-scheme:dark){:root{--bg:#161614;--card:#1f1f1c;--ink:#ecebe6;--muted:#a09d95;--line:#33322e;
--accent:#7fc7a0;--accent-bg:#1e3329;--gold:#e0b858;--gold-bg:#3a3018;--warn:#f0a585;--warn-bg:#40251a;--chip:#2a2a26}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
.wrap{max-width:1120px;margin:0 auto;padding:0 16px 80px}
header.top{padding:28px 0 8px}
h1{font-size:26px;margin:0 0 4px;letter-spacing:-.01em}
h2{font-size:19px;margin:0}
h3{font-size:13px;text-transform:uppercase;letter-spacing:.06em;color:var(--muted);margin:18px 0 6px;font-weight:600}
p{margin:6px 0}.muted{color:var(--muted)}.small{font-size:13px}
.num{font-variant-numeric:tabular-nums;text-align:right;white-space:nowrap}
.bar{position:sticky;top:0;z-index:5;background:var(--bg);border-bottom:1px solid var(--line);padding:10px 0;
display:flex;gap:12px;align-items:center;flex-wrap:wrap}
.toggle{display:inline-flex;border:1px solid var(--line);border-radius:8px;overflow:hidden;background:var(--card)}
.toggle button{font:inherit;font-weight:600;padding:7px 16px;border:0;background:none;color:var(--muted);cursor:pointer}
body[data-view=xp] .toggle .bx{background:var(--accent-bg);color:var(--accent)}
body[data-view=gp] .toggle .bg{background:var(--gold-bg);color:var(--gold)}
body[data-view=xp] .v-gp,body[data-view=gp] .v-xp{display:none}
.jump{display:flex;gap:4px 10px;flex-wrap:wrap;font-size:13px}
.jump a{color:var(--muted);text-decoration:none}.jump a:hover{color:var(--ink);text-decoration:underline}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:16px 18px;margin:16px 0}
.how ol{margin:6px 0 0;padding-left:20px}.how li{margin:3px 0}
table{border-collapse:collapse;width:100%}
th{font-size:12px;text-transform:uppercase;letter-spacing:.05em;color:var(--muted);font-weight:600;text-align:left;
padding:6px 10px;border-bottom:1px solid var(--line);white-space:nowrap}
td{padding:7px 10px;border-bottom:1px solid var(--line);vertical-align:top}
tr:last-child td{border-bottom:0}th.num{text-align:right}
.scroll{overflow-x:auto}
section.skill{margin-top:34px;scroll-margin-top:64px}
.skillhead{display:flex;align-items:baseline;gap:10px;flex-wrap:wrap;margin-bottom:8px}
.lvl{font-size:13px;color:var(--muted)}
.rowhead,.row>summary{display:grid;grid-template-columns:30px minmax(0,1fr) 92px 92px 92px 84px;gap:10px;align-items:center}
.rowhead{font-size:12px;text-transform:uppercase;letter-spacing:.05em;color:var(--muted);font-weight:600;padding:0 14px 6px}
.rowhead span.num{text-align:right}
details.row{background:var(--card);border:1px solid var(--line);border-radius:10px;margin:6px 0}
details.row>summary{list-style:none;cursor:pointer;padding:10px 14px}
details.row>summary::-webkit-details-marker{display:none}
details.row[open]>summary{border-bottom:1px solid var(--line)}
details.row>summary:hover{background:var(--chip);border-radius:10px}
.rank{color:var(--muted);font-variant-numeric:tabular-nums}
.name{font-weight:600}.chain{font-size:13px;color:var(--muted);overflow-wrap:anywhere}
.chain b{color:var(--ink);font-weight:600}
body[data-view=xp] .c-xp,body[data-view=gp] .c-gp{font-weight:700}
body[data-view=xp] .c-xp{color:var(--accent)}body[data-view=gp] .c-gp{color:var(--gold)}
.body{padding:6px 16px 16px}
.lead{font-size:14px}
.tag{display:inline-block;font-size:12px;padding:1px 7px;border-radius:999px;background:var(--chip);color:var(--muted);white-space:nowrap}
.tag.final{background:var(--accent-bg);color:var(--accent)}
.tag.buy{background:var(--gold-bg);color:var(--gold)}
.io{font-size:12.5px;color:var(--muted)}
.two{display:grid;grid-template-columns:1fr 1fr;gap:0 28px}
.total td{font-weight:700;border-top:2px solid var(--line)}
.neg{color:var(--warn)}.c-gain{color:var(--accent);font-weight:700}
.flag{background:var(--warn-bg);color:var(--warn);border-radius:8px;padding:6px 10px;font-size:13px;margin-top:10px}
.calc{font-size:12.5px;color:var(--muted);margin-top:10px}
ul.notes{margin:6px 0;padding-left:20px}
@media (max-width:760px){.rowhead{display:none}
.row>summary{grid-template-columns:24px minmax(0,1fr) 78px;row-gap:2px}
.row>summary .c-all,.row>summary .c-share{display:none}
body[data-view=xp] .row>summary .c-gp,body[data-view=gp] .row>summary .c-xp{display:none}
.two{grid-template-columns:1fr}}
"""

JS = """
document.querySelectorAll('.toggle button').forEach(function(b){b.addEventListener('click',function(){
document.body.dataset.view=b.dataset.v;});});
"""


def n(v, small=1):
    """Compact number: thousands separators, one decimal below 10."""
    if v is None:
        return "-"
    a = abs(v)
    if a >= 100:
        return f"{v:,.0f}"
    if a >= 10:
        return f"{v:,.0f}" if small == 1 else f"{v:,.1f}"
    return f"{v:,.1f}" if a >= 0.05 or a == 0 else f"{v:.2f}"


def qty_list(pairs, limit=4):
    return ", ".join(f"{n(q)} {e(name)}" for q, name in pairs[:limit]) + (
        f" +{len(pairs) - limit} more" if len(pairs) > limit else "")


def chain_line(row):
    steps = row["steps"]
    if len(steps) == 1 and not row["shop"]:
        return "No inputs needed. Every minute of the hour is spent on this."
    parts = [f"buy {e(s['item'])}" for s in row["shop"]]
    parts += [e(s["name"]) if not s["final"] else f"<b>{e(s['name'])}</b>" for s in steps]
    return " &rarr; ".join(parts)


def row_html(rank, row):
    prep = 1 - row["final_share"]
    lead = (f"Doing this for one hour from a standing start: you complete it <b>{n(row['per_hr'], 2)}</b> times. "
            + (f"<b>{row['final_share']:.0%}</b> of the hour is spent on the final step and "
               f"<b>{prep:.0%}</b> on gathering and preparing its inputs."
               if len(row["steps"]) > 1 else "There is nothing to prepare."))
    # step table
    trs = []
    i = 0
    for s in row["shop"]:
        i += 1
        trs.append(f"<tr><td class='rank'>{i}</td><td>Buy {e(s['item'])} <span class='tag buy'>shop</span></td>"
                   f"<td class='num'>{n(s['per_hr'])}</td><td class='num'>0%</td><td class='num muted'>-</td>"
                   f"<td class='io'>costs {n(s['gp_hr'])} gp/hr</td></tr>")
    for s in row["passive"]:
        i += 1
        trs.append(f"<tr><td class='rank'>{i}</td><td>{e(s['item'])} <span class='tag'>farm / ranch</span></td>"
                   f"<td class='num'>{n(s['per_hr'])}</td><td class='num'>0%</td><td class='num muted'>-</td>"
                   f"<td class='io'>grown passively, not timed</td></tr>")
    for s in row["steps"]:
        i += 1
        xp = "<br>".join(f"{n(v)} {e(k)}" for k, v in sorted(s["xp_hr"].items(), key=lambda x: -x[1])) or "-"
        io = []
        if s["uses"]:
            io.append("uses " + qty_list(sorted(s["uses"], reverse=True)))
        if s["makes"]:
            io.append("makes " + qty_list(s["makes"]))
        tag = "<span class='tag final'>final step</span>" if s["final"] else f"<span class='tag'>{e(s['skill'])}</span>"
        trs.append(f"<tr><td class='rank'>{i}</td><td>{e(s['name'])} {tag}<div class='io'>{n(s['secs_each'], 2)}s each"
                   f" &middot; {e(s['detail'])}</div></td><td class='num'>{n(s['per_hr'], 2)}</td>"
                   f"<td class='num'>{s['share']:.0%}</td><td class='num'>{xp}</td><td class='io'>{'<br>'.join(io)}</td></tr>")
    steps_tbl = ("<div class='scroll'><table><thead><tr><th>#</th><th>What you do</th><th class='num'>Times / hr</th>"
                 "<th class='num'>Share of hour</th><th class='num'>XP / hr</th><th>Per hour</th></tr></thead><tbody>"
                 + "".join(trs) + "</tbody></table></div>")
    # xp result
    xp_rows = "".join(f"<tr><td>{e(k)}</td><td class='num'>{n(v)}</td></tr>"
                      for k, v in sorted(row["xp_by_skill"].items(), key=lambda x: -x[1]))
    xp_tbl = (f"<table><thead><tr><th>Skill</th><th class='num'>XP / hr</th></tr></thead><tbody>{xp_rows}"
              f"<tr class='total'><td>All skills</td><td class='num'>{n(row['xp_total_hr'])}</td></tr></tbody></table>")
    # gp result
    gp = []
    for s in row["sold"][:6]:
        gp.append(f"<tr><td>Sell {n(s['per_hr'])} {e(s['item'])}</td><td class='num'>{n(s['gp_hr'])}</td></tr>")
    if len(row["sold"]) > 6:
        rest = sum(s["gp_hr"] for s in row["sold"][6:])
        gp.append(f"<tr><td>Sell {len(row['sold']) - 6} other items</td><td class='num'>{n(rest)}</td></tr>")
    if row["coins_hr"]:
        gp.append(f"<tr><td>Coins gained directly</td><td class='num'>{n(row['coins_hr'])}</td></tr>")
    if row["shop_hr"]:
        gp.append(f"<tr><td>Shop purchases</td><td class='num neg'>&minus;{n(row['shop_hr'])}</td></tr>")
    if not gp:
        gp.append("<tr><td class='muted'>Nothing left over to sell</td><td class='num'>0</td></tr>")
    gp_tbl = (f"<table><thead><tr><th>Gold</th><th class='num'>GP / hr</th></tr></thead><tbody>{''.join(gp)}"
              f"<tr class='total'><td>Net</td><td class='num'>{n(row['gp_hr'])}</td></tr></tbody></table>")
    extra = ""
    if row["other_currencies"]:
        extra += "<p class='small muted'>Also per hour: " + ", ".join(
            f"{n(v)} {e(k)}" for k, v in row["other_currencies"].items()) + ".</p>"
    c = row.get("combat")
    if c:
        bits = [f"{c['hit']:.0%} hit chance", f"hits {c['min_hit']}-{c['max_hit']}", f"turn every {c['interval'] / 1000:.1f}s",
                f"enemy has {c['hp']} HP" + (f", {c['armour']} armour" if c["armour"] else "") + (f", {c['ward']} ward" if c["ward"] else ""),
                f"about {c['attacks']:.1f} turns per kill"]
        if c["stunned_share"] >= 0.01:
            bits.append(f"stunned {c['stunned_share']:.0%} of your turns")
        extra += "<p class='small muted'>Fight: " + ", ".join(bits) + ".</p>"
        setup = [("your saved stance" if c["your_stance"] else "not the stance saved for this loadout")]
        if c["spell"]:
            setup.append(f"casting {e(c['spell'])}" + ("" if c["your_spell"] else " (strongest unlocked; none saved)"))
        setup.append("prayers: " + (e(", ".join(c["prayers"])) + f" ({n(c['prayer_points'] * row['per_hr'])} points/hr)" if c["prayers"] else "none"))
        if c["on_task"]:
            setup.append("Slayer XP and coins assume this enemy is your current task")
        extra += "<p class='small muted'>Setup: " + "; ".join(setup) + ".</p>"
        extra += (f"<p class='small muted'>Incoming: enemy hits {c['enemy_hit']:.0%} of the time for up to {c['enemy_max_hit']}; "
                  f"expected damage taken {n(c['damage_taken'])} per kill ({n(c['damage_taken'] * row['per_hr'])} per hour; "
                  f"you have {c['player_hp']} HP).</p>")
        extra += survival_html(c.get("survival") or {}, row.get("uncosted"))
        rec = row.get("recorded")
        if rec:
            fx = lambda xp: ", ".join(f"{k} {v:.1f}" for k, v in sorted(xp.items(), key=lambda kv: -kv[1]))
            extra += (f"<p class='small'><b>Recorded in your save:</b> {rec['kills']:,} kills at {rec['seconds']:.1f}s each "
                      f"({fx(rec['xp'])} XP per kill). <b>This estimate:</b> {rec['model_seconds']:.1f}s each "
                      f"({fx(rec['model_xp'])} XP per kill). <span class='muted'>The record covers all your past levels, gear "
                      "and stances.</span></p>")
    flags = [s["note"] for s in row["steps"] if s["note"]]
    flag = f"<div class='flag'>{e('; '.join(dict.fromkeys(flags)))}</div>" if flags else ""
    return (f"<details class='row'><summary><span class='rank'>{rank}</span>"
            f"<span><span class='name'>{e(row['name'])}</span><div class='chain'>{chain_line(row)}</div></span>"
            f"<span class='num c-xp'>{n(row['xp_hr'])}</span><span class='num c-all'>{n(row['xp_total_hr'])}</span>"
            f"<span class='num c-gp'>{n(row['gp_hr'])}</span><span class='num c-share muted'>{row['final_share']:.0%}</span>"
            f"</summary><div class='body'><p class='lead'>{lead}</p><h3>The hour, step by step</h3>{steps_tbl}"
            f"<h3>What the hour gives you</h3><div class='two'><div>{xp_tbl}</div><div>{gp_tbl}</div></div>"
            f"{extra}{flag}</div></details>")


def survival_text(sv):
    """One plain sentence about eating and dying, from the simulation's survival block."""
    food = (sv.get("food") or "").split(":")[-1] or None
    v = sv.get("verdict")
    if v == "death" and sv.get("deaths_per_hr"):
        return ("bad", f"You can die here: a hit can take you from above the auto-eat point straight to 0, "
                       f"about once every {1 / sv['deaths_per_hr']:.1f} hours of fighting.")
    if v == "death":
        return ("bad", f"You will die here without food: about {sv['minutes_without_food']:.0f} minutes from full HP.")
    if v == "runs_out":
        return ("warn", f"You eat {n(sv['food_per_hr'])} {food} per hour (heals {sv['food_heal']}, auto-eat at {sv['auto_eat_at']} HP). "
                        f"Your {sv['food_qty']:,} last about {sv['hours_food_lasts']:.1f} hours; after that you die in about "
                        f"{sv['minutes_after_food']:.0f} minutes.")
    if v == "eats":
        return ("ok", f"You eat {n(sv['food_per_hr'])} {food} per hour (heals {sv['food_heal']}, auto-eat at {sv['auto_eat_at']} HP); "
                      "you cannot die even if it runs out." if sv.get("minutes_after_food") is None else
                      f"You eat {n(sv['food_per_hr'])} {food} per hour.")
    return ("ok", "Safe: you take too little damage to ever need food.")


def survival_html(sv, uncosted=None):
    if not sv:
        return ""
    kind, text = survival_text(sv)
    if uncosted:
        text += f" ({', '.join(uncosted)} cannot be gathered or crafted, so its cost is not included.)"
    cls = "flag" if kind in ("bad", "warn") else "small muted"
    return f"<p class='{cls}'>{e(text)}</p>" if kind != "ok" else f"<p class='small muted'>{e(text)}</p>"


def slayer_card(meta):
    blocks = meta.get("slayer") or []
    if not blocks:
        return ""
    rows = []
    for b in sorted(blocks, key=lambda x: -(x["xp_hr"] or -1)):
        en = []
        best_gain = max((x for x in b["enemies"] if x["if_blocked"]), key=lambda x: x["if_blocked"], default=None)
        for x in b["enemies"]:
            gain = (x["if_blocked"] - b["xp_hr"]) if (x["if_blocked"] and b["xp_hr"]) else None
            risk = {"death": "can die", "runs_out": "food runs out"}.get(x["survival"], "")
            en.append(f"<tr><td>{e(x['name'])}</td><td class='num'>{x['seconds']:.1f}s</td><td class='num'>{x['share']:.0%}</td>"
                      f"<td class='num'>{n(x['xp_kill'])}</td><td class='num'>{n(x['xp_hr'])}</td>"
                      f"<td class='num {'c-gain' if gain and gain > 0 else 'neg' if gain is not None else ''}'>"
                      f"{('+' if gain and gain > 0 else '') + n(gain) if gain is not None else '-'}</td>"
                      f"<td class='io'>{e(risk)}</td></tr>")
        stuck = f"<div class='flag'>Cannot fight: {e(', '.join(b['stuck']))}. Auto-slayer would get stuck on these tasks.</div>" if b["stuck"] else ""
        tip = (f"<p class='small'>Blocking <b>{e(best_gain['name'])}</b> raises this to {n(best_gain['if_blocked'])} combat XP/hr.</p>"
               if best_gain and b["xp_hr"] and best_gain["if_blocked"] > b["xp_hr"] else "")
        split = ", ".join(f"{n(v)} {e(k)}" for k, v in sorted(b["xp_by_skill"].items(), key=lambda kv: -kv[1]))
        rows.append(
            f"<details class='row'><summary style='grid-template-columns:minmax(0,1fr) 92px 92px 92px'>"
            f"<span><span class='name'>{e(b['master'])}</span><div class='chain'>{e(b['loadout'])}, {e(b['stance'])} &middot; "
            f"{len(b['enemies'])} enemies &middot; tasks of {b['task_length'][0]}-{b['task_length'][1]} kills</div></span>"
            f"<span class='num c-xp'>{n(b['xp_hr'])}</span><span class='num c-gp'>{n(b['gp_hr'])}</span>"
            f"<span class='num muted'>{n(b['food_hr'])}</span></summary><div class='body'>"
            f"<p class='lead'>XP per hour by skill: {split}.</p>{tip}{stuck}"
            "<div class='scroll'><table><thead><tr><th>Enemy (weakest XP/hr first)</th><th class='num'>Time per kill</th>"
            "<th class='num'>Share of time</th><th class='num'>XP per kill</th><th class='num'>XP / hr on it</th>"
            "<th class='num'>Change if blocked</th><th>Risk</th></tr></thead><tbody>" + "".join(en) + "</tbody></table></div></div></details>")
    return ("<div class='card'><h2>Auto-slayer</h2><p class='small muted'>The game picks each task uniformly from the master's "
            "enemies, so over time every enemy gets the same number of kills and the slow ones take most of the hour. "
            "Enemies earning less XP per hour than the overall rate pull it down; those are the ones to block. "
            "Each master is shown with the stance that gives the most XP. Fighting time only: food, runes and arrows are "
            "made separately. Open a master to see which enemy to block.</p>"
            "<div class='rowhead' style='grid-template-columns:minmax(0,1fr) 92px 92px 92px'><span>Master</span>"
            "<span class='num'>Combat XP / hr</span><span class='num'>GP / hr</span><span class='num'>Food / hr</span></div>"
            + "".join(rows) + "</div>")


def combat_split(row):
    """Combat XP per hour by skill, plus XP earned in other skills while making consumables."""
    parts = [(k, v) for k, v in sorted(row["xp_by_skill"].items(), key=lambda kv: -kv[1]) if k in COMBAT_SKILLS]
    other = sum(v for k, v in row["xp_by_skill"].items() if k not in COMBAT_SKILLS)
    return parts, other


def strip_loadout(name):
    return name.rsplit(" - ", 1)[0]


def combat_section(lst, meta, head):
    top = meta["top"]
    out = [f"<section class='skill' id='Combat'><div class='skillhead'><h2>Combat</h2><span class='lvl'>"
           f"{len(meta['loadouts'])} loadouts compared &middot; {len(lst)} fights evaluated</span></div>"
           "<p class='small muted'>Each row is one enemy fought in one attack stance with one loadout. XP / hr is combat XP only; "
           "arrows and runes are made from scratch, so their time and the XP they give in other skills are included. "
           "Estimates use normal attacks only.</p>"]
    # side-by-side comparison
    cmp_rows = []
    for lo in meta["loadouts"]:
        mine = [r for r in lst if r["loadout"] == lo["label"]]
        if not mine:
            cmp_rows.append(f"<tr><td><b>{e(lo['name'])}</b><div class='io'>{e(lo['weapon'] or 'no weapon')}</div></td>"
                            "<td colspan='6' class='muted'>Not evaluated (no weapon, or nothing it can fight).</td></tr>")
            continue
        bx = max(mine, key=lambda r: r["xp_hr"])
        bg = max(mine, key=lambda r: r["gp_hr"])
        parts, other = combat_split(bx)
        split = "<br>".join(f"{n(v)} {e(k)}" for k, v in parts)
        cmp_rows.append(
            f"<tr><td><a href='#lo-{e(lo['label'].replace(' ', ''))}'><b>{e(lo['name'])}</b></a><div class='io'>{e(lo['weapon'] or '')}</div></td>"
            f"<td>{e(strip_loadout(bx['name']))}<div class='io'>{bx['final_share']:.0%} of the hour fighting</div></td>"
            f"<td class='num'><b>{n(bx['xp_hr'])}</b></td><td class='num'>{split}</td><td class='num'>{n(bx['gp_hr'])}</td>"
            f"<td>{e(strip_loadout(bg['name']))}</td><td class='num'><b>{n(bg['gp_hr'])}</b>"
            f"<div class='io'>{n(bg['xp_hr'])} xp/hr</div></td></tr>")
    out.append("<div class='card'><h2>Loadouts side by side</h2><div class='scroll'><table><thead><tr><th>Loadout</th>"
               "<th>Best fight for XP</th><th class='num'>Combat XP / hr</th><th class='num'>Split by skill</th>"
               "<th class='num'>Its GP / hr</th><th>Best fight for gold</th><th class='num'>GP / hr</th></tr></thead><tbody>"
               + "".join(cmp_rows) + "</tbody></table></div></div>")
    out.append(slayer_card(meta))
    # one block per loadout
    for lo in meta["loadouts"]:
        mine = [r for r in lst if r["loadout"] == lo["label"]]
        out.append(f"<div class='skillhead' id='lo-{e(lo['label'].replace(' ', ''))}' style='margin-top:26px;scroll-margin-top:64px'>"
                   f"<h2>{e(lo['name'])}</h2><span class='lvl'>{e(lo['weapon'] or 'no weapon')} &middot; {len(mine)} fights</span></div>"
                   f"<p class='small muted'>Gear: {e(', '.join(lo['gear']) or 'none')}</p>")
        if not mine:
            continue
        best = []
        for cs in COMBAT_SKILLS:
            cand = [r for r in mine if r["xp_by_skill"].get(cs)]
            if cand:
                b = max(cand, key=lambda r: r["xp_by_skill"][cs])
                best.append(f"<tr><td>{e(cs)}</td><td class='num'>{meta['levels'].get(cs, '-')}</td>"
                            f"<td>{e(strip_loadout(b['name']))}</td><td class='num'><b>{n(b['xp_by_skill'][cs])}</b></td>"
                            f"<td class='num'>{n(b['xp_hr'])}</td><td class='num'>{n(b['gp_hr'])}</td></tr>")
        out.append("<div class='card'><div class='scroll'><table><thead><tr><th>Combat skill</th><th class='num'>Level</th>"
                   "<th>Best fight for it</th><th class='num'>That skill's XP / hr</th><th class='num'>All combat XP / hr</th>"
                   "<th class='num'>GP / hr</th></tr></thead><tbody>" + "".join(best) + "</tbody></table></div></div>")
        xp_rows = "".join(row_html(i + 1, r) for i, r in enumerate(sorted(mine, key=lambda r: -r["xp_hr"])[:top]))
        gp_rows = "".join(row_html(i + 1, r) for i, r in enumerate(sorted(mine, key=lambda r: -r["gp_hr"])[:top]))
        out.append(f"{head}<div class='v-xp'>{xp_rows}</div><div class='v-gp'>{gp_rows}</div>")
        if len(mine) > top:
            out.append(f"<p class='small muted'>Showing the top {top} of {len(mine)}; the rest are in rates.csv.</p>")
    out.append("</section>")
    return "".join(out)


def build(rows, meta):
    by_skill = defaultdict(list)
    for r in rows:
        by_skill[r["skill"]].append(r)
    skills = sorted((s for s in by_skill if s != "Combat"), key=lambda s: -max(x["xp_hr"] for x in by_skill[s]))
    if "Combat" in by_skill:
        skills.append("Combat")
    head = ("<div class='rowhead'><span>#</span><span>Action and chain</span><span class='num'>XP / hr</span>"
            "<span class='num'>All-skill XP</span><span class='num'>GP / hr</span><span class='num'>Final step</span></div>")

    # overview
    ov = []
    for s in skills:
        bx = max(by_skill[s], key=lambda x: x["xp_hr"])
        bg = max(by_skill[s], key=lambda x: x["gp_hr"])
        lvl = meta["levels"].get(s)
        ov.append(f"<tr><td><a href='#{e(s)}'>{e(s)}</a></td><td class='num'>{lvl if lvl else '-'}</td>"
                  f"<td>{e(bx['name'])}</td><td class='num'><b>{n(bx['xp_hr'])}</b></td>"
                  f"<td>{e(bg['name'])}</td><td class='num'><b>{n(bg['gp_hr'])}</b></td></tr>")
    overview = ("<div class='card'><h2>Best per skill</h2><p class='muted small'>Every figure assumes you start with an "
                "empty bank and gather or craft every input yourself.</p><div class='scroll'><table><thead><tr><th>Skill</th>"
                "<th class='num'>Level</th><th>Best for XP</th><th class='num'>XP / hr</th><th>Best for gold</th>"
                "<th class='num'>GP / hr</th></tr></thead><tbody>" + "".join(ov) + "</tbody></table></div></div>")

    sections = []
    for s in skills:
        lst = by_skill[s]
        top = meta["top"]
        if s == "Combat":
            sections.append(combat_section(lst, meta, head))
            continue
        xp_rows = "".join(row_html(i + 1, r) for i, r in enumerate(sorted(lst, key=lambda x: -x["xp_hr"])[:top]))
        gp_rows = "".join(row_html(i + 1, r) for i, r in enumerate(sorted(lst, key=lambda x: -x["gp_hr"])[:top]))
        sub = []
        if s == "Combat":
            sub.append(f"{len(meta['loadouts'])} loadouts compared")
            intro = ["<p class='small muted'>Each row is one enemy fought in one attack stance with one of your loadouts. "
                     "XP / hr is the total across the combat skills that stance trains; open a row for the split. "
                     "Estimates use normal attacks only.</p>"]
            gear = "".join(f"<tr><td>{e(l['name'])}</td><td>{e(l['weapon'] or 'no weapon')}</td>"
                           f"<td class='io'>{e(', '.join(l['gear']))}</td></tr>" for l in meta["loadouts"])
            intro.append("<div class='card'><div class='scroll'><table><thead><tr><th>Loadout</th><th>Weapon</th>"
                         f"<th>Gear</th></tr></thead><tbody>{gear}</tbody></table></div></div>")
            best = []
            for cs in COMBAT_SKILLS:
                cand = [r for r in lst if r["xp_by_skill"].get(cs)]
                if cand:
                    b = max(cand, key=lambda x: x["xp_by_skill"][cs])
                    best.append(f"<tr><td>{e(cs)}</td><td class='num'>{meta['levels'].get(cs, '-')}</td><td>{e(b['name'])}"
                                f"</td><td class='num'><b>{n(b['xp_by_skill'][cs])}</b></td></tr>")
            if best:
                intro.append("<div class='card'><div class='scroll'><table><thead><tr><th>Combat skill</th><th class='num'>Level"
                             "</th><th>Best fight for it</th><th class='num'>XP / hr</th></tr></thead><tbody>"
                             + "".join(best) + "</tbody></table></div></div>")
            intro = "".join(intro)
        else:
            lvl = meta["levels"].get(s)
            sub.append(f"level {lvl}")
            intro = ""
        sub.append(f"{len(lst)} available")
        if s in meta["next_unlock"]:
            lv, nm = meta["next_unlock"][s]
            sub.append(f"next unlock: {e(nm)} at level {lv}")
        more = f"<p class='small muted'>Showing the top {top} of {len(lst)}; the rest are in rates.csv.</p>" if len(lst) > top else ""
        missing = meta["no_source"].get(s)
        if missing:
            shown = ", ".join(f"{e(a)} (needs {e(i)})" for a, i in missing[:6])
            more += (f"<p class='small muted'>Left out, because an input has no source you can reach: {shown}"
                     + (f" and {len(missing) - 6} more" if len(missing) > 6 else "") + ".</p>")
        sections.append(
            f"<section class='skill' id='{e(s)}'><div class='skillhead'><h2>{e(s)}</h2>"
            f"<span class='lvl'>{' &middot; '.join(sub)}</span></div>{intro}{head}"
            f"<div class='v-xp'>{xp_rows}</div><div class='v-gp'>{gp_rows}</div>{more}</section>")

    how = ("<div class='card how'><h2>How to read this</h2><ol>"
           "<li><b>XP / hr</b> is XP in that skill per hour of play, with the time to gather and craft every input included.</li>"
           "<li><b>All-skill XP</b> adds the XP earned in other skills along the way, such as Mining XP while collecting ore for Smithing.</li>"
           "<li><b>GP / hr</b> is what everything left at the end of the hour sells for, including by-products of the earlier steps, "
           "plus coins gained directly, minus anything bought from the shop.</li>"
           "<li><b>Final step</b> is the share of the hour spent on the action itself; the rest goes on its inputs.</li>"
           "<li>Click any row to see the hour broken into steps: how many times you do each one, how long it takes, "
           "and the XP each step gives.</li></ol></div>")
    notes = meta["notes"] + [
        "Each input comes from the single fastest source you have unlocked; by-products of one step are not reused as inputs to another.",
        "Bonuses from your toolbelt, shop upgrades and equipment are applied. Several bonuses to the same stat are assumed to add together.",
        "Sell prices are the fixed prices in the game data.",
        "Combat is a simulation of the game's fight rules for each loadout, with its gear, set bonuses and prayers. "
        "Food is simulated with the game's auto-eat rules and its cost is included in the chain; deaths are reported. "
        "Not simulated: stacking and charging effects, lifesteal and reflect, bonuses with conditions. "
        "Slayer XP assumes the enemy is your current task.",
        "Thieving ignores food used after failed attempts. Hunting catch sizes are averages.",
    ]
    opts = f" Options: {e(', '.join(meta['options']))}." if meta["options"] else ""
    jump = "".join(f"<a href='#{e(s)}'>{e(s)}</a>" for s in skills)
    return (
        "<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
        f"<title>Melvor Idle 2 rates - {e(meta['character'])}</title><style>{CSS}</style></head><body data-view='xp'><div class='wrap'>"
        f"<header class='top'><h1>Best XP and gold per hour</h1><p class='muted'>{e(meta['character'])} &middot; total level "
        f"{meta['total_level']} &middot; generated {datetime.now():%d %b %Y %H:%M} from your save.{opts}</p></header>"
        "<div class='bar'><div class='toggle'><button class='bx' data-v='xp'>Rank by XP</button>"
        f"<button class='bg' data-v='gp'>Rank by gold</button></div><nav class='jump'>{jump}</nav></div>"
        f"{overview}{how}{''.join(sections)}"
        "<div class='card' style='margin-top:36px'><h2>Limits of these numbers</h2><ul class='notes small'>"
        + "".join(f"<li>{e(x)}</li>" for x in notes) + "</ul></div>"
        f"</div><script>{JS}</script></body></html>")
