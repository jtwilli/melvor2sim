extends HBoxContainer
## Compare loadouts tab: setup A (and optionally B) against many targets at once.
## Left: the two setups. Middle: target filter, metric and a paired bar chart. Right: details for one target.

const BATCH := 5               # targets per request, so progress shows and the panel stays responsive
const SKILLS := ["Attack", "Strength", "Defence", "Hitpoints", "Ranged", "Magic", "Prayer", "Slayer"]
# detail rows: [label, key, kind]; kind: n number, p percent, s seconds, t text; "-" suffix = lower is better
const DETAIL := [["Kills / h", "kills_hr", "n"], ["Kill time", "kill_s", "s-"], ["GP / h", "gp_hr", "n"],
	["Food / h", "food_hr", "n-"], ["Prayer points / h", "prayer_hr", "n-"], ["Deaths / h", "deaths_hr", "n-"],
	["Hit chance", "hit", "p"], ["Max hit", "max_hit", "n"], ["Attack interval", "interval", "ms-"],
	["Enemy hit chance", "enemy_hit", "p-"], ["Enemy max hit", "enemy_max_hit", "n-"]]

var panel            # panel.gd (shell)
var ui               # ui.gd
var gear := {}
var card_a
var card_b
var with_b: CheckBox
var picker
var chart
var scope := "can"
var scope_pick: OptionButton
var metric: OptionButton
var stance_rule: OptionButton
var legend: Label
var summary: Label
var go: Button
var detail: VBoxContainer

var queue: Array = []
var total := 0
var rows: Array = []           # results so far
var selected := ""
var stale := true              # setups or filters changed since the last full run
var started_at := 0


# ------------------------------------------------------------------ build
func build() -> void:
	add_theme_constant_override("separation", 10)
	var card_script = panel.script_named("gear_card.gd")
	var chart_script = panel.script_named("bar_chart.gd")
	var picker_script = panel.script_named("item_picker.gd")
	if card_script == null or chart_script == null or picker_script == null:
		add_child(ui.wrapped("A Compare script (gear_card.gd, bar_chart.gd or item_picker.gd) is missing or has an error. " +
			"Run install_mod.py again.", ui.BAD))
		return

	# --- left: the setups
	var left_scroll := ScrollContainer.new()
	left_scroll.custom_minimum_size = Vector2(300, 0)
	left_scroll.horizontal_scroll_mode = ScrollContainer.SCROLL_MODE_DISABLED
	add_child(left_scroll)
	var left := VBoxContainer.new()
	left.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	left.add_theme_constant_override("separation", 8)
	left_scroll.add_child(left)
	card_a = card_script.new()
	card_a.ui = ui
	card_a.side = "A"
	card_a.build()
	card_a.changed.connect(_setup_changed)
	left.add_child(card_a)
	var mid_row: HFlowContainer = ui.flow()
	left.add_child(mid_row)
	with_b = CheckBox.new()
	with_b.text = "Compare with B"
	with_b.toggled.connect(func(_on): _b_toggled())
	mid_row.add_child(with_b)
	mid_row.add_child(ui.button("Swap", _swap, "Swap A and B"))
	mid_row.add_child(ui.button("Copy A to B", _copy, "Start B as a copy of A, then change it"))
	card_b = card_script.new()
	card_b.ui = ui
	card_b.side = "B"
	card_b.build()
	card_b.changed.connect(_setup_changed)
	card_b.visible = false
	left.add_child(card_b)

	# --- middle: filters, chart, run
	var mid := VBoxContainer.new()
	mid.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	mid.add_theme_constant_override("separation", 8)
	add_child(mid)
	var filters: HFlowContainer = ui.flow()
	mid.add_child(filters)
	var group := ButtonGroup.new()
	var first: Button = ui.chip("Can fight", group, _scope.bind("can"), "Every enemy in the areas you have unlocked")
	first.button_pressed = true
	filters.add_child(first)
	filters.add_child(ui.chip("All", group, _scope.bind("all"), "Every enemy and dungeon, including locked ones"))
	filters.add_child(ui.chip("Dungeons", group, _scope.bind("dungeon"), "Full dungeon runs: every enemy in order, rewards at the end"))
	filters.add_child(ui.chip("Slayer", group, _scope.bind("slayer"), "The enemies one slayer master can assign"))
	filters.add_child(ui.chip("Area", group, _scope.bind("area"), "One combat area"))
	scope_pick = ui.options([], 170)
	scope_pick.visible = false
	scope_pick.item_selected.connect(func(_i): _filters_changed())
	filters.add_child(scope_pick)
	filters.add_child(ui.spacer())
	var metrics := [["Combat XP / hr", "xp"]]
	for s in SKILLS:
		metrics.append([s + " XP / hr", "xp:" + s])
	metrics.append(["GP / hr", "gp"])
	metrics.append(["GP / hr after supplies", "net_gp"])
	metrics.append(["Kills / hr", "kills"])
	metric = ui.options(metrics, 150)
	metric.item_selected.connect(func(_i): _filters_changed())
	filters.add_child(metric)
	stance_rule = ui.options([["Best stance per target", "best"], ["My saved stance", "saved"]], 170)
	stance_rule.tooltip_text = "A stance picked on a setup card always wins"
	stance_rule.item_selected.connect(func(_i): _filters_changed())
	filters.add_child(stance_rule)

	var chart_card: PanelContainer = ui.card()
	chart_card.size_flags_vertical = Control.SIZE_EXPAND_FILL
	mid.add_child(chart_card)
	chart = chart_script.new()
	chart.ui = ui
	chart.size_flags_vertical = Control.SIZE_EXPAND_FILL
	chart.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	chart.picked.connect(_pick)
	chart_card.add_child(chart)
	legend = ui.label("", true, 12)
	legend.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	mid.add_child(legend)
	var run_row := HBoxContainer.new()
	run_row.add_theme_constant_override("separation", 10)
	mid.add_child(run_row)
	summary = ui.label("")
	summary.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	summary.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	run_row.add_child(summary)
	go = ui.button("Compare", _start)
	go.custom_minimum_size = Vector2(190, 36)
	run_row.add_child(go)

	# --- right: one target in detail
	var right_scroll := ScrollContainer.new()
	right_scroll.custom_minimum_size = Vector2(330, 0)
	right_scroll.horizontal_scroll_mode = ScrollContainer.SCROLL_MODE_DISABLED
	add_child(right_scroll)
	detail = VBoxContainer.new()
	detail.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	detail.add_theme_constant_override("separation", 6)
	right_scroll.add_child(detail)

	# the item picker covers the whole panel while open
	picker = picker_script.new()
	picker.ui = ui
	picker.build()
	panel.add_child(picker)
	card_a.picker = picker
	card_b.picker = picker


func _exit_tree() -> void:
	if picker != null and is_instance_valid(picker):
		picker.queue_free()


# ------------------------------------------------------------------ data
func on_info() -> void:
	gear = {}            # the save may have changed: fetch gear again on the next run


func run() -> void:
	if card_a == null:
		return
	if gear.is_empty():
		panel.send("/api/game/gear", null, _on_gear, "Loading your gear...")
	elif rows.is_empty() or (not queue.is_empty()):
		_start()         # first visit, or a run that was interrupted by switching tabs


func _on_gear(ok: bool, data) -> void:
	if not ok:
		if data is Dictionary:
			panel.set_status("The server reported a problem. " + String(data.get("error", "")))
		else:
			panel.server_lost()
		return
	var first_time: bool = card_a.setup.is_empty()
	gear = data
	card_a.set_gear(gear)
	card_b.set_gear(gear)
	var los: Array = gear.get("loadouts", [])
	var worn := 0
	for i in los.size():
		if los[i]["worn"]:
			worn = i
	if first_time:
		card_a.use_loadout(worn)
		card_b.use_loadout(worn)
	else:                # keep the user's edits, but refresh what "saved" means
		for c in [card_a, card_b]:
			var i = c.loadout_index()
			if i != null and int(i) < los.size():
				c.use_setup(c.setup, los[int(i)]["setup"], i)
	_fill_scope()
	_start()


func _fill_scope() -> void:
	var entries := []
	var keep = ui.value(scope_pick)
	var choice = null
	if scope == "slayer":
		for m in gear.get("masters", []):
			entries.append([String(m["name"]) + ("" if m["unlocked"] else " (locked)"), m["id"]])
		for m in panel.info.get("masters", []):
			if m["auto"]:
				choice = m["id"]         # default: the master you have auto-slayer on
	elif scope == "area":
		for a in panel.info.get("areas", []):
			entries.append([String(a["name"]) + ("" if a["unlocked"] else " (locked)"), a["id"]])
		choice = panel.info.get("current_area")
	for e in entries:
		if keep != null and e[1] == keep:
			choice = keep                # keep what was picked, if it is still in this list
	ui.fill(scope_pick, entries, choice)
	scope_pick.visible = scope == "slayer" or scope == "area"


func _targets() -> Array:
	var out := []
	var pick = ui.value(scope_pick)
	if scope == "slayer":
		for m in gear.get("masters", []):
			if m["id"] == pick:
				for t in m["enemies"]:
					if not m["blocked"].has(t["enemy"]):
						out.append({"enemy": t["enemy"], "area": t["area"]})
		return out
	if scope != "dungeon":
		for a in panel.info.get("areas", []):
			if scope == "can" and not a["unlocked"]:
				continue
			if scope == "area" and a["id"] != pick:
				continue
			for e in a["enemies"]:
				out.append({"enemy": e["id"], "area": a["id"]})
	if scope != "area":
		for d in panel.info.get("dungeons", []):
			# "Can fight" only takes dungeons the save shows as unlocked; the others take them all
			if scope == "can" and d["unlocked"] != true:
				continue
			out.append({"dungeon": d["id"]})
	return out


# ------------------------------------------------------------------ edits
func _scope(s: String) -> void:
	scope = s
	_fill_scope()
	_filters_changed()


func _filters_changed() -> void:
	_start()


func _setup_changed() -> void:
	stale = true
	_update_button()


func _b_toggled() -> void:
	card_b.visible = with_b.button_pressed
	stale = true
	_start()


func _swap() -> void:
	var a_setup: Dictionary = card_a.setup
	var a_saved: Dictionary = card_a.saved
	var a_index = card_a.loadout_index()
	card_a.use_setup(card_b.setup, card_b.saved, card_b.loadout_index())
	card_b.use_setup(a_setup, a_saved, a_index)
	if not with_b.button_pressed:
		with_b.button_pressed = true      # toggling starts a run
	else:
		_start()


func _copy() -> void:
	card_b.use_setup(card_a.setup, card_a.saved, card_a.loadout_index())
	if not with_b.button_pressed:
		with_b.button_pressed = true
	else:
		stale = true
		_update_button()


func _update_button() -> void:
	var n := _targets().size()
	go.text = ("Compare %d targets" % n) if queue.is_empty() else "Comparing..."
	go.disabled = not queue.is_empty()
	if stale and queue.is_empty() and not rows.is_empty():
		panel.set_status("Setup changed. Press Compare to update the chart.")


# ------------------------------------------------------------------ run
func _start() -> void:
	if gear.is_empty() or card_a.setup.is_empty():
		return
	queue = _targets()
	total = queue.size()
	rows = []
	started_at = Time.get_ticks_msec()
	stale = false
	summary.text = ""
	_show_rows()
	if total == 0:
		panel.set_status("No targets for this filter.")
		_update_button()
		return
	_update_button()
	_next()


func _next() -> void:
	var batch := queue.slice(0, BATCH)
	var payload := {"a": card_a.setup, "targets": batch, "metric": ui.value(metric), "stance": ui.value(stance_rule)}
	if with_b.button_pressed:
		payload["b"] = card_b.setup
	panel.set_progress(float(total - queue.size()) / float(maxi(1, total)))
	panel.send("/api/game/compare", payload, _on_batch, "Simulating %d of %d targets..." % [total - queue.size(), total])


func _on_batch(ok: bool, data) -> void:
	if not ok or (data is Dictionary and data.has("error")):
		queue = []
		panel.set_progress(-1.0)
		_update_button()
		if data is Dictionary:
			panel.set_status(String(data.get("error", "The server reported a problem.")))
		else:
			panel.server_lost()
		return
	queue = queue.slice(BATCH)
	for r in data.get("rows", []):
		rows.append(r)
	var cannot = data.get("cannot_wear", {})
	for pair in [["a", card_a], ["b", card_b]]:
		var list = cannot.get(pair[0], []) if cannot is Dictionary else []
		pair[1].set_warning(("You can't wear this yet: " + ", ".join(PackedStringArray(list)) +
			". The results assume you could.") if list is Array and not list.is_empty() else "")
	legend.text = "%s.  A: outline.  B: filled, green when clearly better, red when clearly worse, grey when the 95%% ranges overlap." % String(data.get("metric_label", ""))
	_show_rows()
	if queue.is_empty():
		panel.set_progress(-1.0)
		panel.set_status("Compared %d targets in %.1fs. Save from %s." % [total, (Time.get_ticks_msec() - started_at) / 1000.0, panel.saved_at()])
		_summarise()
		_update_button()
	else:
		_next()


func _sort_key(r: Dictionary) -> float:
	if with_b.button_pressed and r.get("delta") != null:
		return float(r["delta"])
	var a = r.get("a")
	if with_b.button_pressed:
		return -1e9 if not (a is Dictionary) else -1e8
	return float(a["value"]) if a is Dictionary else -1e9


func _show_rows() -> void:
	var sorted := rows.duplicate()
	sorted.sort_custom(func(x, y): return _sort_key(x) > _sort_key(y))
	var out := []
	for r in sorted:
		out.append({"id": String(r["enemy"]) + "|" + String(r["area"]), "name": r["name"],
			"icon": gear.get("icons", {}).get(r["enemy"]), "a": r.get("a"), "b": r.get("b"),
			"delta": r.get("delta"), "verdict": r.get("verdict")})
	chart.set_rows(out, with_b.button_pressed)
	if selected == "" or _row(selected).is_empty():
		selected = String(out[0]["id"]) if not out.is_empty() else ""
	chart.select(selected)
	_show_detail()


func _row(id: String) -> Dictionary:
	for r in rows:
		if String(r["enemy"]) + "|" + String(r["area"]) == id:
			return r
	return {}


func _pick(id: String) -> void:
	selected = id
	chart.select(id)
	_show_detail()


func _summarise() -> void:
	var parts := PackedStringArray()
	if chart.shown < rows.size():
		parts.append("Showing the first %d of %d targets (widen the game window to see more)." % [chart.shown, rows.size()])
	if with_b.button_pressed:
		var better := 0
		var worse := 0
		var same := 0
		for r in rows:
			match r.get("verdict"):
				"b": better += 1
				"a": worse += 1
				"same": same += 1
		parts.append("B is clearly better on %d, worse on %d, no clear difference on %d." % [better, worse, same])
	if scope == "slayer":
		for side in ["a", "b"]:
			if side == "b" and not with_b.button_pressed:
				continue
			var gain := 0.0
			var hours := 0.0
			var stuck := PackedStringArray()
			for r in rows:
				var m = r.get(side)
				if m is Dictionary and float(m["kills_hr"]) > 0.0:
					gain += float(m["value"]) / float(m["kills_hr"])
					hours += 1.0 / float(m["kills_hr"])
				else:
					stuck.append(String(r["name"]))
			if hours > 0.0:
				parts.append("Auto-slayer overall %s: %s per hour%s" % [side.to_upper(), ui.num(gain / hours),
					"." if stuck.is_empty() else " (would stall on " + ", ".join(stuck) + ")."])
	summary.text = "  ".join(parts)


# ------------------------------------------------------------------ detail column
func _fmt(v, kind: String) -> String:
	if v == null:
		return "-"
	if kind.begins_with("p"):
		return ui.pct(v, false)
	if kind.begins_with("s"):
		return "%.1fs" % float(v)
	if kind.begins_with("ms"):
		return "%.2fs" % (float(v) / 1000.0)
	return ui.num(v)


func _delta_cell(a, b, lower_is_better: bool) -> Label:
	if a == null or b == null or absf(float(a)) < 1e-9:
		return ui.label("", true)
	var d := (float(b) - float(a)) / absf(float(a))
	var l: Label = ui.label(ui.pct(d))
	l.horizontal_alignment = HORIZONTAL_ALIGNMENT_RIGHT
	var good := d < 0.0 if lower_is_better else d > 0.0
	l.add_theme_color_override("font_color", ui.SAME if absf(d) < 0.005 else (ui.GOOD if good else ui.BAD))
	return l


func _show_detail() -> void:
	ui.clear(detail)
	var r := _row(selected)
	if r.is_empty():
		detail.add_child(ui.wrapped("Pick a target in the chart to see the details.", ui.DIM))
		return
	var both: bool = with_b.button_pressed
	var a = r.get("a")
	var b = r.get("b")
	var head := HBoxContainer.new()
	head.add_theme_constant_override("separation", 10)
	head.add_child(ui.icon(gear.get("icons", {}).get(r["enemy"]), 52))
	var names := VBoxContainer.new()
	names.add_child(ui.label(String(r["name"]), false, 18))
	names.add_child(ui.label(String(r["area_name"]), true))
	head.add_child(names)
	detail.add_child(head)
	if both:
		var verdict := String(r.get("verdict")) if r.get("verdict") != null else ""
		var text := "No clear difference"
		var color: Color = ui.SAME
		if verdict == "b":
			text = "B is better" + ("  " + ui.pct(r["delta"]) if r.get("delta") != null else "  (A cannot kill it)")
			color = ui.GOOD
		elif verdict == "a":
			text = "A is better" + ("  (B is " + ui.pct(r["delta"]) + ")" if r.get("delta") != null else "  (B cannot kill it)")
			color = ui.BAD
		elif r.get("delta") != null:
			text += "  (" + ui.pct(r["delta"]) + ", within the 95% ranges)"
		detail.add_child(ui.wrapped(text, color))
	if not (a is Dictionary) and not (b is Dictionary):
		detail.add_child(ui.wrapped("Can't be killed in under 15 minutes with %s." % ("either setup" if both else "this setup"), ui.BAD))
		return
	var table: PanelContainer = ui.card()
	detail.add_child(table)
	var grid := GridContainer.new()
	grid.columns = 4 if both else 2
	grid.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	grid.add_theme_constant_override("h_separation", 14)
	grid.add_theme_constant_override("v_separation", 5)
	table.add_child(grid)
	_cells(grid, both, "Rates", "A", "B", "B vs A", true)
	var am: Dictionary = a if a is Dictionary else {}
	var bm: Dictionary = b if b is Dictionary else {}
	_cells(grid, both, "Stance", String(am.get("stance", "-")), String(bm.get("stance", "-")), "")
	_cells(grid, both, "Fights with", String(am.get("levels", "-")), String(bm.get("levels", "-")), "")
	_metric_row(grid, both, "Combat XP / h", am.get("xp_hr"), bm.get("xp_hr"), "n")
	var skills := {}
	for m in [am, bm]:
		for k in m.get("xp_by_skill", {}):
			skills[k] = true
	for k in skills:
		_metric_row(grid, both, "  " + String(k), am.get("xp_by_skill", {}).get(k), bm.get("xp_by_skill", {}).get(k), "n")
	var is_dungeon: bool = r.get("dungeon", false)
	for d in DETAIL:
		var caption := String(d[0])
		if is_dungeon and d[1] == "kills_hr":
			caption = "Runs / h"
		elif is_dungeon and d[1] == "kill_s":
			caption = "Run time"
		_metric_row(grid, both, caption, am.get(d[1]), bm.get(d[1]), d[2])
	# supplies used up: one row per item (arrows, runes, food), then their value and GP after them
	var used := {}
	for m in [am, bm]:
		for k in m.get("uses", {}):
			used[k] = true
	if not used.is_empty():
		_cells(grid, both, "Uses / h", "", "", "", true)
		for k in used:
			_metric_row(grid, both, "  " + String(k), am.get("uses", {}).get(k, 0.0) if not am.is_empty() else null,
				bm.get("uses", {}).get(k, 0.0) if not bm.is_empty() else null, "n-")
		_metric_row(grid, both, "Supplies value / h", am.get("supplies_gp_hr"), bm.get("supplies_gp_hr"), "n-")
	_metric_row(grid, both, "GP / h after supplies", am.get("net_gp_hr"), bm.get("net_gp_hr"), "n")
	if is_dungeon:
		detail.add_child(ui.wrapped("A full run: every enemy in order. Enemies inside drop nothing; the loot below is the " +
			"reward for finishing. Hit chance is averaged over the run; max hit is against the boss.", Color(1, 1, 1, 0.62)))
	for side in [["A", am], ["B", bm]]:
		var m: Dictionary = side[1]
		if m.is_empty() or (side[0] == "B" and not both):
			continue
		var color2: Color = {"bad": ui.BAD, "warn": ui.WARN}.get(m.get("survival_kind"), ui.GOOD)
		detail.add_child(ui.wrapped("%s: %s" % [side[0], m.get("survival", "")], color2))
		for pair in [["loot", "loot per hour"]]:
			var text2 := _items(m.get(pair[0], {}))
			if text2 != "":
				detail.add_child(ui.wrapped("%s %s: %s" % [side[0], pair[1], text2], Color(1, 1, 1, 0.62)))


func _cells(grid: GridContainer, both: bool, c0: String, c1: String, c2: String, c3: String, header := false) -> void:
	var texts := [c0, c1, c2, c3] if both else [c0, c1]
	for i in texts.size():
		var l: Label = ui.label(texts[i], header, 12 if header else 0)
		if i > 0:
			l.horizontal_alignment = HORIZONTAL_ALIGNMENT_RIGHT
		if i == 1:
			l.add_theme_color_override("font_color", ui.SIDE_A)
		elif i == 2:
			l.add_theme_color_override("font_color", ui.SIDE_B)
		grid.add_child(l)


func _metric_row(grid: GridContainer, both: bool, title: String, a, b, kind: String) -> void:
	if a == null and b == null:
		return
	grid.add_child(ui.label(title, true))
	var la: Label = ui.label(_fmt(a, kind))
	la.horizontal_alignment = HORIZONTAL_ALIGNMENT_RIGHT
	grid.add_child(la)
	if both:
		var lb: Label = ui.label(_fmt(b, kind))
		lb.horizontal_alignment = HORIZONTAL_ALIGNMENT_RIGHT
		grid.add_child(lb)
		grid.add_child(_delta_cell(a, b, kind.ends_with("-")))


func _items(d) -> String:
	if not (d is Dictionary) or d.is_empty():
		return ""
	var keys: Array = d.keys()
	keys.sort_custom(func(x, y): return float(d[x]) > float(d[y]))
	var parts := PackedStringArray()
	for k in keys.slice(0, 6):
		if float(d[k]) >= 0.05:
			parts.append("%s %s" % [ui.num(d[k]), k])
	return ", ".join(parts)
