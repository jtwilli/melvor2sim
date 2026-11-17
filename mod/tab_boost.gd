extends VBoxContainer
## Boost? tab: every way to boost one skill (items, potions, shop upgrades, mastery, astrology, pets, sets,
## events), what each gives, its effect on your best action, how to get it and how long, in order of the
## levels needed. Ones you can't get yet are greyed out; ones you have are green.

const SECTIONS := [["All", ""], ["Items & potions", "items"], ["Shop", "shop"], ["Mastery", "mastery"],
	["Astrology", "astrology"], ["Pets, sets & events", "other"]]

var panel            # panel.gd (shell)
var ui               # ui.gd
var skill: OptionButton
var loadout: OptionButton
var hide_have: CheckBox
var section := ""
var content: VBoxContainer


func build() -> void:
	add_theme_constant_override("separation", 8)
	var row: HFlowContainer = ui.flow()
	add_child(row)
	skill = ui.options([])
	skill.item_selected.connect(func(_i): run())
	row.add_child(skill)
	row.add_child(ui.label("Combat drops timed with", true))
	loadout = ui.options([], 200)
	loadout.tooltip_text = "The loadout used to time enemies that drop a booster"
	loadout.item_selected.connect(func(_i): run())
	row.add_child(loadout)
	hide_have = CheckBox.new()
	hide_have.text = "Hide ones I have"
	hide_have.toggled.connect(func(_on): run())
	row.add_child(hide_have)
	var chips: HFlowContainer = ui.flow()
	add_child(chips)
	var group := ButtonGroup.new()
	for s in SECTIONS:
		var b: Button = ui.chip(s[0], group, _section.bind(s[1]))
		b.button_pressed = s[1] == ""
		chips.add_child(b)
	content = ui.scroll_area(self)
	content.add_child(ui.wrapped("Pick a skill to see every way to boost it. Grey rows need a level or item you " +
		"don't have yet; green names are ones you already have.", ui.DIM))


func on_info() -> void:
	var entries := []
	for s in panel.info.get("skills", []):
		entries.append(["%s (%d)" % [s["skill"], int(s["level"])], s["skill"]])
	ui.fill(skill, entries, ui.value(skill))
	var los := []
	var worn = null
	var saved: Array = panel.info.get("loadouts", [])
	for i in saved.size():
		los.append(["%s: %s" % [saved[i]["name"], saved[i]["weapon"] if saved[i]["weapon"] != null else "no weapon"], i])
		if saved[i]["worn"]:
			worn = i
	ui.fill(loadout, los, ui.value(loadout) if loadout.item_count > 0 else worn)


func _section(s: String) -> void:
	section = s
	run()


func run() -> void:
	if ui.value(skill) == null:
		return
	var p := {"skill": ui.value(skill), "loadout": ui.value(loadout), "section": section,
		"hide_have": hide_have.button_pressed}
	panel.send("/api/game/view", {"view": "boost", "params": p}, _on_view, "Finding boosters...")


func _on_view(ok: bool, data) -> void:
	if not ok:
		if data is Dictionary:
			panel.set_status("The server reported a problem. " + String(data.get("error", "")))
		else:
			panel.server_lost()
		return
	panel.set_status("Done in %.1fs. Save from %s." % [panel.elapsed(), panel.saved_at()])
	ui.render_view(content, data)
