extends PanelContainer
## One combat setup (A or B) in the Compare tab: the gear as an icon grid laid out like the game's
## equipment screen, plus stance, spell, food and prayers. Click a slot to change the item.

signal changed

const CELL := 46.0
const GAP := 5.0

var ui                         # ui.gd
var gear := {}                 # /api/game/gear payload
var picker                     # item_picker.gd node, shared by both cards
var side := "A"
var setup := {}                # the setup being edited (server format)
var saved := {}                # the saved loadout it started from
var items_by_id := {}
var _filling := false          # true while controls are being filled, so their signals are ignored

var title: Label
var loadout: OptionButton
var grid: Control
var slot_buttons := {}
var stance: OptionButton
var spell_caption: Label
var spell: OptionButton
var food: OptionButton
var food_qty: SpinBox
var prayer_box: VBoxContainer
var prayer_pickers: Array = []
var changes: Label
var reset: Button
var level_grid: GridContainer
var level_boxes := {}          # skill -> SpinBox
var warning: Label


func build() -> void:
	add_theme_stylebox_override("panel", ui.box(ui.SURFACE, 8, 10, ui.BORDER))
	var col := VBoxContainer.new()
	col.add_theme_constant_override("separation", 6)
	add_child(col)
	var head := HBoxContainer.new()
	col.add_child(head)
	title = ui.label(side, false, 18)
	title.add_theme_color_override("font_color", ui.SIDE_A if side == "A" else ui.SIDE_B)
	head.add_child(title)
	loadout = ui.options([], 170)
	loadout.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	loadout.item_selected.connect(func(_i): _loadout_chosen())
	head.add_child(loadout)

	grid = Control.new()
	grid.custom_minimum_size = Vector2(3 * CELL + 2 * GAP, 5 * CELL + 4 * GAP)
	grid.size_flags_horizontal = Control.SIZE_SHRINK_CENTER
	col.add_child(grid)

	var rows := GridContainer.new()
	rows.columns = 2
	rows.add_theme_constant_override("h_separation", 8)
	rows.add_theme_constant_override("v_separation", 4)
	col.add_child(rows)
	rows.add_child(ui.label("Stance", true))
	stance = ui.options([], 150)
	stance.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	stance.item_selected.connect(func(_i): _control_changed())
	rows.add_child(stance)
	spell_caption = ui.label("Spell", true)
	rows.add_child(spell_caption)
	spell = ui.options([], 150)
	spell.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	spell.item_selected.connect(func(_i): _control_changed())
	rows.add_child(spell)
	rows.add_child(ui.label("Food", true))
	var food_row := HBoxContainer.new()
	food_row.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	food = ui.options([], 110)
	food.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	food.item_selected.connect(func(_i): _food_chosen())
	food_row.add_child(food)
	food_qty = SpinBox.new()
	food_qty.min_value = 0
	food_qty.max_value = 9999999
	food_qty.step = 1
	food_qty.tooltip_text = "How many you bring"
	food_qty.value_changed.connect(func(_v): _control_changed())
	food_row.add_child(food_qty)
	rows.add_child(food_row)
	rows.add_child(ui.label("Prayers", true))
	prayer_box = VBoxContainer.new()
	prayer_box.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	rows.add_child(prayer_box)

	# what-if levels: fight as if a skill were a different level
	col.add_child(ui.label("Levels (change one to test \"what if\")", true, 12))
	level_grid = GridContainer.new()
	level_grid.columns = 4
	level_grid.add_theme_constant_override("h_separation", 6)
	level_grid.add_theme_constant_override("v_separation", 3)
	col.add_child(level_grid)
	warning = ui.wrapped("", ui.WARN)
	warning.visible = false
	col.add_child(warning)

	var foot := HBoxContainer.new()
	col.add_child(foot)
	changes = ui.label("", true)
	changes.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	foot.add_child(changes)
	reset = ui.button("Reset", _reset, "Back to the saved loadout")
	foot.add_child(reset)


# ------------------------------------------------------------------ data in
func set_gear(g: Dictionary) -> void:
	gear = g
	items_by_id = {}
	for s in gear.get("slots", []):
		for it in s["items"]:
			items_by_id[it["id"]] = it
	# slot buttons, placed like the game's equipment grid
	ui.clear(grid)
	slot_buttons = {}
	for s in gear.get("slots", []):
		var b := Button.new()
		b.focus_mode = Control.FOCUS_NONE
		b.expand_icon = true
		b.icon_alignment = HORIZONTAL_ALIGNMENT_CENTER
		b.position = Vector2(int(s["x"]) * (CELL + GAP), int(s["y"]) * (CELL + GAP))
		b.size = Vector2(CELL, CELL)
		b.pressed.connect(_slot_pressed.bind(String(s["id"])))
		grid.add_child(b)
		slot_buttons[String(s["id"])] = b
	var entries := []
	var los: Array = gear.get("loadouts", [])
	for i in los.size():
		entries.append([String(los[i]["name"]), i])
	_filling = true
	ui.fill(loadout, entries, ui.value(loadout))
	var foods := [["No food", ""]]
	for f in gear.get("foods", []):
		foods.append(["%s (heals %d)%s" % [f["name"], int(f["heal"]), "  x%d" % int(f["owned"]) if int(f["owned"]) > 0 else ""], f["id"]])
	ui.fill(food, foods)
	ui.clear(level_grid)
	level_boxes = {}
	var current: Dictionary = gear.get("levels", {})
	for skill in current:
		var caption: Label = ui.label(String(skill).substr(0, 3), true, 12)
		caption.tooltip_text = String(skill)
		level_grid.add_child(caption)
		var box := SpinBox.new()
		box.min_value = 1
		box.max_value = 120
		box.step = 1
		box.tooltip_text = "%s (yours is %d)" % [skill, int(current[skill])]
		box.value_changed.connect(func(_v): _levels_changed())
		level_grid.add_child(box)
		level_boxes[skill] = box
	_filling = false


func set_warning(text: String) -> void:
	warning.text = text
	warning.visible = text != ""


func use_loadout(index: int) -> void:
	var los: Array = gear.get("loadouts", [])
	if index < 0 or index >= los.size():
		return
	_filling = true
	ui.select(loadout, index)
	_filling = false
	saved = los[index]["setup"].duplicate(true)
	setup = saved.duplicate(true)
	refresh()


func use_setup(new_setup: Dictionary, new_saved: Dictionary, loadout_index) -> void:
	setup = new_setup.duplicate(true)
	saved = new_saved.duplicate(true)
	_filling = true
	if loadout_index != null:
		ui.select(loadout, loadout_index)
	_filling = false
	refresh()


func loadout_index():
	return ui.value(loadout)


# ------------------------------------------------------------------ draw
func _slot_item(source: Dictionary, slot_id: String) -> String:
	var eq = source.get("equipment", {})
	if eq is Dictionary and eq.has(slot_id) and eq[slot_id] is Dictionary:
		return String(eq[slot_id].get("item", ""))
	return ""


func _weapon() -> Dictionary:
	return items_by_id.get(_slot_item(setup, "Weapon"), {})


func refresh() -> void:
	_filling = true
	for s in gear.get("slots", []):
		var sid := String(s["id"])
		var b: Button = slot_buttons.get(sid)
		if b == null:
			continue
		var item_id := _slot_item(setup, sid)
		var differs := item_id != _slot_item(saved, sid)
		var it: Dictionary = items_by_id.get(item_id, {})
		b.icon = ui.texture(gear.get("icons", {}).get(item_id)) if item_id != "" else null
		b.text = "" if b.icon != null else (String(it.get("name", "?")).substr(0, 3) if item_id != "" else "")
		var tip := String(s["name"]) + ": " + (String(it.get("name", item_id)) if item_id != "" else "empty")
		if String(it.get("needs", "")) != "":
			tip += "\nNeeds " + String(it["needs"])
		b.tooltip_text = tip + "\nClick to change"
		var border: Color = ui.WARN if differs else ui.BORDER
		b.add_theme_stylebox_override("normal", ui.box(ui.TILE, 6, 3, border))
		b.add_theme_stylebox_override("hover", ui.box(ui.TILE.lightened(0.12), 6, 3, border))
		b.add_theme_stylebox_override("pressed", ui.box(ui.TILE.lightened(0.2), 6, 3, border))
	# stance: the ones this weapon type allows
	var w := _weapon()
	var stances := [["Best for each target", ""]]
	var magic := false
	for sid in gear.get("weapon_types", {}).get(w.get("weapon_type"), []):
		var st: Dictionary = gear.get("stances", {}).get(sid, {})
		stances.append(["%s (%s)" % [st.get("name", sid), ", ".join(PackedStringArray(st.get("trains", [])))], sid])
		if String(st.get("style", "")) == "Magic":
			magic = true
	var cur_stance = setup.get("stance")
	ui.fill(stance, stances, cur_stance if cur_stance != null else "")
	spell_caption.visible = magic
	spell.visible = magic
	if magic:
		var spells := [["Best I can cast", ""]]
		for sp in gear.get("spells", []):
			spells.append([String(sp["name"]) + ("  (needs %s)" % sp["needs"] if String(sp["needs"]) != "" else ""), sp["id"]])
		var cur_spell = setup.get("spell")
		ui.fill(spell, spells, cur_spell if cur_spell != null else "")
	var cur_food = setup.get("food")
	ui.select(food, cur_food if cur_food != null else "")
	food_qty.value = int(setup.get("food_qty", 0))
	food_qty.editable = cur_food != null and String(cur_food) != ""
	# prayers: one picker per open slot
	var mine: Array = setup.get("prayers", [])
	var slots := maxi(int(gear.get("prayer_slots", 0)), mine.size())
	if prayer_pickers.size() != slots:
		ui.clear(prayer_box)
		prayer_pickers = []
		for i in slots:
			var o: OptionButton = ui.options([], 150)
			o.size_flags_horizontal = Control.SIZE_EXPAND_FILL
			o.item_selected.connect(func(_i): _control_changed())
			prayer_box.add_child(o)
			prayer_pickers.append(o)
		if slots == 0:
			prayer_box.add_child(ui.label("none unlocked", true))
	var prayers := [["None", ""]]
	for p in gear.get("prayers", []):
		prayers.append(["%s (%d pts)%s" % [p["name"], int(p["cost"]), "  needs " + String(p["needs"]) if String(p["needs"]) != "" else ""], p["id"]])
	for i in prayer_pickers.size():
		ui.fill(prayer_pickers[i], prayers, mine[i] if i < mine.size() else "")
	var overrides = setup.get("levels", {})
	var current: Dictionary = gear.get("levels", {})
	for skill in level_boxes:
		var box: SpinBox = level_boxes[skill]
		var changed_level: bool = overrides is Dictionary and overrides.has(skill)
		box.value = int(overrides[skill]) if changed_level else int(current.get(skill, 1))
		box.modulate = ui.WARN if changed_level else Color(1, 1, 1, 1)
	var n := change_count()
	changes.text = ("%d change%s from saved" % [n, "" if n == 1 else "s"]) if n > 0 else "as saved"
	reset.visible = n > 0
	_filling = false


func _levels_changed() -> void:
	if _filling:
		return
	var current: Dictionary = gear.get("levels", {})
	var overrides := {}
	for skill in level_boxes:
		var box: SpinBox = level_boxes[skill]
		var differs := int(box.value) != int(current.get(skill, 1))
		if differs:
			overrides[skill] = int(box.value)
		box.modulate = ui.WARN if differs else Color(1, 1, 1, 1)
	setup["levels"] = overrides
	var n := change_count()
	changes.text = ("%d change%s from saved" % [n, "" if n == 1 else "s"]) if n > 0 else "as saved"
	reset.visible = n > 0
	changed.emit()


func change_count() -> int:
	var n := 0
	var level_overrides = setup.get("levels", {})
	if level_overrides is Dictionary:
		n += level_overrides.size()
	for s in gear.get("slots", []):
		if _slot_item(setup, String(s["id"])) != _slot_item(saved, String(s["id"])):
			n += 1
	for key in ["stance", "spell", "food"]:
		if str(setup.get(key)) != str(saved.get(key)):
			n += 1
	if setup.get("food") != null and int(setup.get("food_qty", 0)) != int(saved.get("food_qty", 0)):
		n += 1
	var a: Array = setup.get("prayers", []).duplicate()
	var b: Array = saved.get("prayers", []).duplicate()
	a.sort()
	b.sort()
	if a != b:
		n += 1
	return n


# ------------------------------------------------------------------ edits
func _loadout_chosen() -> void:
	if _filling:
		return
	var i = ui.value(loadout)
	if i != null:
		use_loadout(int(i))
		changed.emit()


func _reset() -> void:
	setup = saved.duplicate(true)
	refresh()
	changed.emit()


func _slot_pressed(slot_id: String) -> void:
	if picker == null:
		return
	for s in gear.get("slots", []):
		if String(s["id"]) == slot_id:
			picker.icons = gear.get("icons", {})
			picker.open(String(s["name"]), s["items"], _slot_item(setup, slot_id), _item_picked.bind(slot_id))
			return


func _item_picked(item_id: String, slot_id: String) -> void:
	if not (setup.get("equipment") is Dictionary):
		setup["equipment"] = {}
	var eq: Dictionary = setup["equipment"]
	if item_id == "":
		eq.erase(slot_id)
	else:
		var it: Dictionary = items_by_id.get(item_id, {})
		var qty := 1
		if slot_id == "Ammo":
			qty = maxi(1, int(it.get("owned", 1)))
		eq[slot_id] = {"item": item_id, "qty": qty}
		if slot_id == "Weapon":
			setup["stance"] = null       # a new weapon type has its own stances
			if it.get("two_handed", false):
				eq.erase("Offhand")
		elif slot_id == "Offhand" and _weapon().get("two_handed", false):
			eq.erase("Offhand")           # a two-handed weapon leaves no room
	refresh()
	changed.emit()


func _food_chosen() -> void:
	if _filling:
		return
	var id = ui.value(food)
	if id == null or String(id) == "":
		setup["food"] = null
		setup["food_qty"] = 0
	else:
		setup["food"] = id
		for f in gear.get("foods", []):
			if f["id"] == id:
				setup["food_qty"] = int(f["owned"])
	refresh()
	changed.emit()


func _control_changed() -> void:
	if _filling:
		return
	var st = ui.value(stance)
	setup["stance"] = st if st != null and String(st) != "" else null
	if spell.visible:
		var sp = ui.value(spell)
		setup["spell"] = sp if sp != null and String(sp) != "" else null
	setup["food_qty"] = int(food_qty.value)
	var chosen := []
	for o in prayer_pickers:
		var p = ui.value(o)
		if p != null and String(p) != "" and not chosen.has(p):
			chosen.append(p)
	setup["prayers"] = chosen
	var n := change_count()
	changes.text = ("%d change%s from saved" % [n, "" if n == 1 else "s"]) if n > 0 else "as saved"
	reset.visible = n > 0
	changed.emit()
