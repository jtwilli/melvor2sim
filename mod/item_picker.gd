extends Control
## Item picker for one equipment slot: searchable list with the game's icons, owned items first.
## Covers the whole panel while open; emits nothing itself, it calls back the Callable it was opened with.

const WIDTH := 420.0
const HEIGHT := 520.0

var ui                         # ui.gd
var icons := {}                # item id -> texture path
var items: Array = []
var current := ""
var on_pick: Callable
var title: Label
var search: LineEdit
var owned_only: CheckBox
var list: ItemList
var shown_ids: Array = []
var empty_button: Button


func build() -> void:
	set_anchors_preset(Control.PRESET_FULL_RECT)
	mouse_filter = Control.MOUSE_FILTER_STOP
	visible = false
	var shade := ColorRect.new()
	shade.color = Color(0, 0, 0, 0.45)
	shade.set_anchors_preset(Control.PRESET_FULL_RECT)
	shade.gui_input.connect(_on_shade_input)
	add_child(shade)
	var frame := PanelContainer.new()
	frame.name = "Frame"
	frame.add_theme_stylebox_override("panel", ui.box(ui.SURFACE, 8, 12, ui.BORDER))
	frame.custom_minimum_size = Vector2(WIDTH, HEIGHT)
	add_child(frame)
	var col := VBoxContainer.new()
	col.add_theme_constant_override("separation", 8)
	frame.add_child(col)
	title = ui.label("", false, 16)
	col.add_child(title)
	search = LineEdit.new()
	search.placeholder_text = "Search..."
	search.clear_button_enabled = true
	search.text_changed.connect(func(_t): _refill())
	col.add_child(search)
	owned_only = CheckBox.new()
	owned_only.text = "Only items I own or wear"
	owned_only.toggled.connect(func(_on): _refill())
	col.add_child(owned_only)
	list = ItemList.new()
	list.size_flags_vertical = Control.SIZE_EXPAND_FILL
	list.fixed_icon_size = Vector2i(28, 28)
	list.item_selected.connect(_on_selected)
	col.add_child(list)
	var row := HBoxContainer.new()
	empty_button = ui.button("Empty this slot", _choose.bind(""))
	row.add_child(empty_button)
	row.add_child(ui.spacer())
	row.add_child(ui.button("Cancel", close))
	col.add_child(row)


func open(slot_name: String, slot_items: Array, current_id: String, picked: Callable, opts := {}) -> void:
	## opts: "filter" (text of the tick box, default owned-or-worn), "empty" (show the empty-slot button, default true).
	## An item may carry "have" (what the tick box keeps) and "note" (shown after its name).
	items = slot_items
	current = current_id
	on_pick = picked
	title.text = "Choose: " + slot_name
	owned_only.text = String(opts.get("filter", "Only items I own or wear"))
	empty_button.visible = bool(opts.get("empty", true))
	search.text = ""
	_refill()
	visible = true
	var frame := get_node("Frame") as Control
	frame.position = ((size - Vector2(WIDTH, HEIGHT)) / 2.0).round()
	frame.size = Vector2(WIDTH, HEIGHT)
	search.grab_focus()


func close() -> void:
	visible = false


func _refill() -> void:
	list.clear()
	shown_ids = []
	var want := search.text.strip_edges().to_lower()
	for it in items:
		var have: bool = bool(it["have"]) if it.has("have") else (int(it["owned"]) > 0 or it["worn"])
		if owned_only.button_pressed and not have:
			continue
		var item_name := String(it["name"])
		if want != "" and item_name.to_lower().find(want) < 0:
			continue
		var text := item_name
		if int(it["owned"]) > 0:
			text += "   x%d" % int(it["owned"])
		elif it["worn"]:
			text += "   (worn)"
		if String(it["needs"]) != "":
			text += "   needs " + String(it["needs"])
		if String(it.get("note", "")) != "":
			text += "   (" + String(it["note"]) + ")"
		var at := list.add_item(text, ui.texture(icons.get(it["id"])))
		if not have:
			list.set_item_custom_fg_color(at, Color(1, 1, 1, 0.55))
		if String(it["needs"]) != "":
			list.set_item_custom_fg_color(at, ui.WARN)
		if String(it["id"]) == current:
			list.select(at)
		shown_ids.append(String(it["id"]))


func _on_selected(index: int) -> void:
	if index >= 0 and index < shown_ids.size():
		_choose(shown_ids[index])


func _choose(id: String) -> void:
	visible = false
	if on_pick.is_valid():
		on_pick.call(id)


func _on_shade_input(ev: InputEvent) -> void:
	var click := ev as InputEventMouseButton
	if click != null and click.pressed:
		close()
