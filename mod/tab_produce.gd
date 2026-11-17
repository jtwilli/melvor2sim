extends VBoxContainer
## Production tab: pick an item, see the fastest way to make it per hour (whole chain counted from nothing),
## including quest actions your save has open right now and, optionally, combat drops.

var panel            # panel.gd (shell)
var ui               # ui.gd
var item_button: Button
var quests: CheckBox
var combat: CheckBox
var loadout: OptionButton
var noshop: CheckBox
var content: VBoxContainer
var picker
var items: Array = []          # produce_items payload: [{id, name, owned, have, needs, note}]
var icons := {}
var item := ""


func build() -> void:
	add_theme_constant_override("separation", 8)
	var row: HFlowContainer = ui.flow()
	add_child(row)
	item_button = ui.button("Choose an item...", _open_picker, "The item to make")
	item_button.add_theme_constant_override("icon_max_width", 24)
	item_button.custom_minimum_size = Vector2(220, 0)
	row.add_child(item_button)
	quests = CheckBox.new()
	quests.text = "Include quest actions"
	quests.tooltip_text = "Quest-only actions your save has open now (they disappear when their quest step ends)"
	quests.button_pressed = true
	quests.toggled.connect(func(_on): run())
	row.add_child(quests)
	combat = CheckBox.new()
	combat.text = "Include combat drops"
	combat.tooltip_text = "Also rank enemies that drop the item, fought with the loadout chosen here (a second or two)"
	combat.toggled.connect(func(_on): _combat_toggled())
	row.add_child(combat)
	loadout = ui.options([])
	loadout.visible = false
	loadout.item_selected.connect(func(_i): run())
	row.add_child(loadout)
	noshop = CheckBox.new()
	noshop.text = "Never buy from the shop"
	noshop.toggled.connect(func(_on): run())
	row.add_child(noshop)
	content = ui.scroll_area(self)
	content.add_child(ui.wrapped("Choose an item to see the fastest way to make it, per hour.", ui.DIM))

	var picker_script = panel.script_named("item_picker.gd")
	if picker_script != null:
		picker = picker_script.new()
		picker.ui = ui
		picker.build()
		panel.add_child(picker)


func _exit_tree() -> void:
	if picker != null and is_instance_valid(picker):
		picker.queue_free()


func on_info() -> void:
	items = []          # the save may have changed: fetch the list again on the next run
	var entries := []
	var worn = null
	var los: Array = panel.info.get("loadouts", [])
	for i in los.size():
		entries.append(["%s: %s" % [los[i]["name"], los[i]["weapon"] if los[i]["weapon"] != null else "no weapon"], i])
		if los[i]["worn"]:
			worn = i
	ui.fill(loadout, entries, ui.value(loadout) if loadout.item_count > 0 else worn)


func _combat_toggled() -> void:
	loadout.visible = combat.button_pressed
	run()


func _open_picker() -> void:
	if picker == null:
		panel.set_status("item_picker.gd is missing or has an error. Run install_mod.py again.")
		return
	if items.is_empty():
		panel.send("/api/game/view", {"view": "produce_items", "params": {}}, _on_items_then_pick, "Listing items...")
		return
	picker.icons = icons
	picker.open("item to make", items, item, _picked, {"filter": "Only items I can make now", "empty": false})


func _on_items_then_pick(ok: bool, data) -> void:
	if _take_items(ok, data):
		panel.set_status("")
		_open_picker()


func _take_items(ok: bool, data) -> bool:
	if not ok or not (data is Dictionary) or not data.has("items"):
		if data is Dictionary:
			panel.set_status("The server reported a problem. " + String(data.get("error", "")))
		else:
			panel.server_lost()
		return false
	items = data["items"]
	icons = data.get("icons", {})
	return true


func _picked(id: String) -> void:
	item = id
	_show_item()
	run()


func _show_item() -> void:
	for it in items:
		if String(it["id"]) == item:
			item_button.text = String(it["name"])
			item_button.icon = ui.texture(icons.get(item))
			return


func run() -> void:
	var params := {"item": item, "quests": quests.button_pressed, "combat": combat.button_pressed,
		"no_shop": noshop.button_pressed, "loadout": ui.value(loadout)}
	panel.send("/api/game/view", {"view": "produce", "params": params}, _on_view,
		"Working out the best chains..." if combat.button_pressed else "Working...")


func _on_view(ok: bool, data) -> void:
	if not ok:
		if data is Dictionary:
			panel.set_status("The server reported a problem. " + String(data.get("error", "")))
		else:
			panel.server_lost()
		return
	panel.set_status("Done in %.1fs. Save from %s." % [panel.elapsed(), panel.saved_at()])
	ui.render_view(content, data)
