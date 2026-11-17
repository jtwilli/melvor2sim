extends VBoxContainer
## Levelling planner tab: the fastest route from your current level to a target level.

var panel            # panel.gd (shell)
var ui               # ui.gd
var skill: OptionButton
var target: SpinBox
var make_caption: Label
var make: OptionButton
var use_caption: Label
var use: OptionButton
var loadout: OptionButton
var bank: CheckBox
var noshop: CheckBox
var tools: CheckBox
var food: OptionButton
var stealth: SpinBox
var content: VBoxContainer
var planned := false


func build() -> void:
	add_theme_constant_override("separation", 8)
	var row: HFlowContainer = ui.flow()
	add_child(row)
	skill = ui.options([])
	skill.item_selected.connect(func(_i): _skill_changed())
	row.add_child(skill)
	row.add_child(ui.label("to level", true))
	target = SpinBox.new()
	target.min_value = 2
	target.max_value = 120
	target.step = 1
	target.get_line_edit().text_submitted.connect(func(_t): run())     # Enter in the box plans straight away
	row.add_child(target)
	make_caption = ui.label("Make", true)
	row.add_child(make_caption)
	make = ui.options([["Anything", ""]])
	row.add_child(make)
	use_caption = ui.label("Used for", true)
	row.add_child(use_caption)
	use = ui.options([["Anything", ""]])
	row.add_child(use)
	loadout = ui.options([])
	row.add_child(loadout)
	bank = CheckBox.new()
	bank.text = "Use items in my bank"
	bank.tooltip_text = "Take ingredients from your bank before gathering or buying more"
	row.add_child(bank)
	noshop = CheckBox.new()
	noshop.text = "Never buy from the shop"
	row.add_child(noshop)
	food = ui.options([], 200)
	food.tooltip_text = "The food Thieving or Agility eats when a failure hurts you; making it is counted in the time"
	row.add_child(food)
	stealth = ui.stealth_box()
	row.add_child(stealth)
	tools = CheckBox.new()
	tools.text = "Assume I get required tools"
	tools.tooltip_text = "Treat toolbelt items an action needs as equipped (e.g. the Fire Wizard set for the Mage Tower)"
	row.add_child(tools)
	row.add_child(ui.button("Plan route", run))
	content = ui.scroll_area(self)
	content.add_child(ui.wrapped("Pick a skill and a target level, then press Plan route. Use Make / Used for to level a certain way, " +
		"for example Fletching by making arrows.", ui.DIM))


func on_info() -> void:
	var entries := []
	for s in panel.info.get("skills", []):
		entries.append(["%s (%d)" % [s["skill"], int(s["level"])], s["skill"]])
	ui.fill(skill, entries, ui.value(skill))
	var loadouts := []
	var worn = null
	var los: Array = panel.info.get("loadouts", [])
	for i in los.size():
		loadouts.append(["%s: %s" % [los[i]["name"], los[i]["weapon"] if los[i]["weapon"] != null else "no weapon"], i])
		if los[i]["worn"]:
			worn = i
	ui.fill(loadout, loadouts, ui.value(loadout) if loadout.item_count > 0 else worn)
	ui.fill(food, ui.food_entries(panel.info), ui.value(food))
	_skill_changed()


func _skill_info(name) -> Dictionary:
	for s in panel.info.get("skills", []):
		if s["skill"] == name:
			return s
	return {}


func _skill_changed() -> void:
	var s := _skill_info(ui.value(skill))
	if s.is_empty():
		return
	var level := int(s["level"])
	target.min_value = level + 1
	target.max_value = int(panel.info.get("max_level", 120))
	if target.value <= level or target.get_meta("skill", "") != s["skill"]:
		target.value = mini(int(target.max_value), int(ceil((level + 1) / 5.0)) * 5)
	target.set_meta("skill", s["skill"])
	var is_combat: bool = s["combat"]
	for c in [make_caption, make, use_caption, use, noshop, bank, tools]:
		c.visible = not is_combat
	loadout.visible = is_combat
	food.visible = String(s["skill"]) == "Thieving" or String(s["skill"]) == "Agility"
	stealth.visible = String(s["skill"]) == "Thieving"
	if is_combat or s.get("focus") == null:
		return
	var makes := [["Anything", ""]]
	for c in s["focus"]["categories"]:
		var from := " (from level %d)" % int(c["level"]) if int(c["level"]) > level else ""
		makes.append([("    " if c["sub"] else "") + String(c["label"]) + from, c["id"]])
	ui.fill(make, makes)
	var uses := [["Anything", ""]]
	for u in s["focus"]["uses"]:
		var from2 := " (from level %d)" % int(u["level"]) if int(u["level"]) > level else ""
		uses.append([String(u["label"]) + from2, u["id"]])
	ui.fill(use, uses)


func run() -> void:
	if not planned and not panel.visible:
		return               # nothing chosen yet: don't plan during the hidden warm-up
	target.apply()           # commit a number that was typed but not confirmed with Enter
	stealth.apply()
	planned = true
	var params := {"skill": ui.value(skill), "target": int(target.value), "category": ui.value(make), "use": ui.value(use),
		"no_shop": noshop.button_pressed, "use_bank": bank.button_pressed, "assume_tools": tools.button_pressed,
		"food": ui.value(food) if food.visible else "",
		"stealth": int(stealth.value) if stealth.visible else 0,
		"loadout": ui.value(loadout)}
	panel.send("/api/game/view", {"view": "plan", "params": params}, _on_view, "Planning...")


func _on_view(ok: bool, data) -> void:
	if not ok:
		if data is Dictionary:
			panel.set_status("The server reported a problem. " + String(data.get("error", "")))
		else:
			panel.server_lost()
		return
	panel.set_status("Planned in %.1fs. Save from %s." % [panel.elapsed(), panel.saved_at()])
	ui.render_view(content, data)
