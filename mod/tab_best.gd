extends VBoxContainer
## Best XP / GP tab: the best action per skill, the top actions of one skill, or a look ahead at every
## action in one skill up to a chosen level (including ones your save has not unlocked yet).

var panel            # panel.gd (shell)
var ui               # ui.gd
var rank := "xp"
var skill: OptionButton
var combat: CheckBox
var ahead: CheckBox
var ahead_level: SpinBox
var food: OptionButton
var stealth: SpinBox
var content: VBoxContainer


func build() -> void:
	add_theme_constant_override("separation", 8)
	var row: HFlowContainer = ui.flow()
	add_child(row)
	var group := ButtonGroup.new()
	var by_xp: Button = ui.chip("XP per hour", group, _rank.bind("xp"))
	by_xp.button_pressed = true
	row.add_child(by_xp)
	row.add_child(ui.chip("GP per hour", group, _rank.bind("gp")))
	skill = ui.options([["All skills", ""]])
	skill.item_selected.connect(func(_i): _skill_changed())
	row.add_child(skill)
	ahead = CheckBox.new()
	ahead.text = "Look ahead to level"
	ahead.tooltip_text = "Every action in this skill up to the level, including ones you have not unlocked yet"
	ahead.toggled.connect(func(_on): run())
	row.add_child(ahead)
	ahead_level = SpinBox.new()
	ahead_level.min_value = 2
	ahead_level.max_value = 120
	ahead_level.value = 60
	ahead_level.get_line_edit().text_submitted.connect(func(_t): run())
	row.add_child(ahead_level)
	food = ui.options([], 200)
	food.tooltip_text = "The food Thieving or Agility eats when a failure hurts you; making it is counted in XP / hr"
	food.item_selected.connect(func(_i): run())
	row.add_child(food)
	stealth = ui.stealth_box()
	stealth.get_line_edit().text_submitted.connect(func(_t): run())
	stealth.value_changed.connect(func(_v): run())
	row.add_child(stealth)
	combat = CheckBox.new()
	combat.text = "Include combat"
	combat.tooltip_text = "Scans every enemy with your worn loadout (a few seconds)"
	combat.toggled.connect(func(_on): run())
	row.add_child(combat)
	content = ui.scroll_area(self)
	_skill_changed(false)


func _rank(r: String) -> void:
	rank = r
	run()


func _skill_changed(rerun := true) -> void:
	_show_controls()
	if rerun:
		run()


func _show_controls() -> void:
	var one: bool = String(ui.value(skill)) != ""
	ahead.visible = one
	ahead_level.visible = one
	var sk := String(ui.value(skill))
	food.visible = one and ahead.button_pressed and (sk == "Thieving" or sk == "Agility")
	stealth.visible = food.visible and sk == "Thieving"
	combat.visible = not (one and ahead.button_pressed)


func on_info() -> void:
	var entries := [["All skills", ""]]
	for s in panel.info.get("skills", []):
		if not s["combat"]:
			entries.append(["%s (%d)" % [s["skill"], int(s["level"])], s["skill"]])
	ui.fill(skill, entries, ui.value(skill))
	ui.fill(food, ui.food_entries(panel.info), ui.value(food))
	_skill_changed(false)


func run() -> void:
	var one := String(ui.value(skill)) != ""
	_show_controls()
	if one and ahead.button_pressed:
		ahead_level.apply()
		stealth.apply()
		var p := {"skill": ui.value(skill), "up_to": int(ahead_level.value), "assume_tools": true, "food": ui.value(food),
			"stealth": int(stealth.value) if stealth.visible else 0}
		panel.send("/api/game/view", {"view": "ahead", "params": p}, _on_view, "Looking ahead...")
		return
	var params := {"rank": rank, "skill": ui.value(skill), "combat": combat.button_pressed}
	panel.send("/api/game/view", {"view": "best", "params": params}, _on_view)


func _on_view(ok: bool, data) -> void:
	if not ok:
		if data is Dictionary:
			panel.set_status("The server reported a problem. " + String(data.get("error", "")))
		else:
			panel.server_lost()
		return
	panel.set_status("Done in %.1fs. Save from %s." % [panel.elapsed(), panel.saved_at()])
	ui.render_view(content, data)
