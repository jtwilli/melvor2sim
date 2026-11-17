extends RefCounted
## Shared look and widgets for the melvor2sim panel: colours, buttons, tiles, tables, icons, number formats.

const BACKGROUND := Color(0.09, 0.10, 0.13)
const SURFACE := Color(0.13, 0.15, 0.19)
const TILE := Color(0.16, 0.18, 0.23)
const BORDER := Color(0.30, 0.34, 0.42)
const DIM := Color(1, 1, 1, 0.62)
const BAD := Color(1.0, 0.56, 0.46)
const WARN := Color(0.96, 0.80, 0.42)
const GOOD := Color(0.47, 0.85, 0.56)
const SAME := Color(0.62, 0.66, 0.74)
const SIDE_A := Color(0.36, 0.78, 0.86)
const SIDE_B := Color(0.47, 0.85, 0.56)
const ACCENT := Color(0.36, 0.62, 0.92)

var _textures := {}


# ------------------------------------------------------------------ numbers
func commas(n: int) -> String:
	var s := str(absi(n))
	var out := ""
	while s.length() > 3:
		out = "," + s.substr(s.length() - 3) + out
		s = s.substr(0, s.length() - 3)
	return ("-" if n < 0 else "") + s + out


func num(v) -> String:
	if v == null:
		return "-"
	var f := float(v)
	if absf(f) >= 100.0:
		return commas(int(round(f)))
	if absf(f) >= 10.0:
		return "%.0f" % f
	return "%.1f" % f


func pct(v, signed := true) -> String:
	if v == null:
		return "-"
	var f := float(v) * 100.0
	var body := ("%.0f" % f) if absf(f) >= 10.0 else ("%.1f" % f)
	return ("+" if signed and f > 0.0 else "") + body + "%"


# ------------------------------------------------------------------ styles
func box(color: Color, radius := 6, margin := 8, border := Color(0, 0, 0, 0)) -> StyleBoxFlat:
	var sb := StyleBoxFlat.new()
	sb.bg_color = color
	sb.set_corner_radius_all(radius)
	sb.set_content_margin_all(margin)
	if border.a > 0.0:
		sb.border_color = border
		sb.set_border_width_all(1)
	return sb


func card() -> PanelContainer:
	var p := PanelContainer.new()
	p.add_theme_stylebox_override("panel", box(SURFACE, 8, 10, BORDER))
	return p


# ------------------------------------------------------------------ basic widgets
func label(text: String, dim := false, size := 0) -> Label:
	var l := Label.new()
	l.text = text
	if dim:
		l.modulate = DIM
	if size > 0:
		l.add_theme_font_size_override("font_size", size)
	return l


func wrapped(text: String, color := Color(1, 1, 1, 1)) -> Label:
	var l := Label.new()
	l.text = text
	l.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	l.add_theme_color_override("font_color", color)
	return l


func button(text: String, action: Callable, tip := "") -> Button:
	var b := Button.new()
	b.text = text
	b.tooltip_text = tip
	b.focus_mode = Control.FOCUS_NONE
	b.pressed.connect(action)
	return b


func chip(text: String, group: ButtonGroup, action: Callable, tip := "") -> Button:
	## A toggle that belongs to a group: one of the group is always on.
	var b := Button.new()
	b.text = text
	b.tooltip_text = tip
	b.toggle_mode = true
	b.button_group = group
	b.focus_mode = Control.FOCUS_NONE
	b.pressed.connect(action)
	return b


func spacer() -> Control:
	var c := Control.new()
	c.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	return c


func flow() -> HFlowContainer:
	var r := HFlowContainer.new()
	r.add_theme_constant_override("h_separation", 8)
	r.add_theme_constant_override("v_separation", 6)
	return r


func scroll_area(parent: Node) -> VBoxContainer:
	## A vertically scrolling column that fills the rest of `parent`.
	var scroll := ScrollContainer.new()
	scroll.size_flags_vertical = Control.SIZE_EXPAND_FILL
	scroll.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	parent.add_child(scroll)
	var content := VBoxContainer.new()
	content.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	content.add_theme_constant_override("separation", 8)
	scroll.add_child(content)
	return content


func clear(node: Node) -> void:
	for c in node.get_children():
		node.remove_child(c)
		c.queue_free()


# ------------------------------------------------------------------ dropdowns
func options(entries: Array, min_width := 150) -> OptionButton:
	var o := OptionButton.new()
	o.focus_mode = Control.FOCUS_NONE
	o.fit_to_longest_item = false
	o.custom_minimum_size = Vector2(min_width, 0)
	fill(o, entries)
	return o


func fill(o: OptionButton, entries: Array, keep = null) -> void:
	## entries: [[text, value], ...]; a null value makes a separator heading.
	o.clear()
	for e in entries:
		if e[1] == null:
			o.add_separator(e[0])
		else:
			o.add_item(e[0])
			o.set_item_metadata(o.item_count - 1, e[1])
	select(o, keep)


func select(o: OptionButton, value) -> void:
	var first := -1
	for i in o.item_count:
		if o.is_item_separator(i):
			continue
		if first < 0:
			first = i
		var m = o.get_item_metadata(i)
		if value != null and typeof(m) == typeof(value) and m == value:
			o.select(i)
			return
	if first >= 0:
		o.select(first)


func value(o: OptionButton):
	return o.get_item_metadata(o.selected) if o.selected >= 0 else null


func stealth_box() -> SpinBox:
	## What-if stealth for Thieving: gear or ranks you don't have yet.
	var box := SpinBox.new()
	box.min_value = 0
	box.max_value = 500
	box.step = 5
	box.prefix = "+"
	box.suffix = "stealth"
	box.tooltip_text = ("Extra stealth to test: Balaclava +15 (from Thug), Thug Chief Ring +25 (Thug Chief), " +
		"Bandit Gloves +30 (Bandit), Thieving Mastery Rank 3 +60, Astrology +10 per node")
	return box


func food_entries(info: Dictionary) -> Array:
	## Dropdown entries for choosing Thieving food: the saved one first, then foods you own, then the rest.
	var saved = info.get("thieving_food")
	var foods: Array = info.get("foods", [])
	var saved_name := ""
	for f in foods:
		if f["id"] == saved:
			saved_name = String(f["name"])
	var out := [["Saved Thieving food: " + (saved_name if saved_name != "" else "none set"), ""]]
	var sorted_foods := foods.duplicate()
	sorted_foods.sort_custom(func(a, b): return (int(a["owned"]) > 0) and not (int(b["owned"]) > 0))
	for f in sorted_foods:
		out.append(["%s (heals %d)%s" % [f["name"], int(f["heal"]), "  x%d" % int(f["owned"]) if int(f["owned"]) > 0 else ""], f["id"]])
	return out


# ------------------------------------------------------------------ icons (the game's own textures)
func texture(path) -> Texture2D:
	if path == null or String(path) == "":
		return null
	var key := String(path)
	if not _textures.has(key):
		var t: Texture2D = null
		if ResourceLoader.exists(key):
			var res = load(key)
			if res is Texture2D:
				t = res
		_textures[key] = t
	return _textures[key]


func icon(path, size := 24) -> TextureRect:
	var r := TextureRect.new()
	r.texture = texture(path)
	r.expand_mode = TextureRect.EXPAND_IGNORE_SIZE
	r.stretch_mode = TextureRect.STRETCH_KEEP_ASPECT_CENTERED
	r.custom_minimum_size = Vector2(size, size)
	r.mouse_filter = Control.MOUSE_FILTER_IGNORE
	return r


# ------------------------------------------------------------------ server "views": tiles, messages, tables
func tile(big_text: String, small_text: String) -> PanelContainer:
	var p := PanelContainer.new()
	p.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	p.add_theme_stylebox_override("panel", box(TILE, 6, 10))
	var vb := VBoxContainer.new()
	vb.add_child(label(big_text, false, 22))
	vb.add_child(label(small_text, true))
	p.add_child(vb)
	return p


func render_view(content: Node, v: Dictionary) -> void:
	clear(content)
	if String(v.get("title", "")) != "":
		content.add_child(label(String(v["title"]), false, 20))
	var kpis: Array = v.get("kpis", [])
	if not kpis.is_empty():
		var row := HBoxContainer.new()
		row.add_theme_constant_override("separation", 8)
		content.add_child(row)
		for k in kpis:
			row.add_child(tile(String(k["v"]), String(k["l"])))
	for b in v.get("banners", []):
		content.add_child(wrapped(String(b["text"]), {"bad": BAD, "warn": WARN}.get(b["kind"], GOOD)))
	for t in v.get("tables", []):
		content.add_child(table(t))


func table(t: Dictionary) -> Control:
	var wrap := card()
	var col := VBoxContainer.new()
	col.add_theme_constant_override("separation", 6)
	wrap.add_child(col)
	var cols: Array = t.get("columns", [])
	var rows: Array = t.get("rows", [])
	if String(t.get("title", "")) != "":
		col.add_child(label(String(t["title"]), false, 16))
	if String(t.get("note", "")) != "":
		col.add_child(wrapped(String(t["note"]), Color(1, 1, 1, 0.62)))
	if cols.is_empty():
		return wrap
	# a column wraps when any of its cells is long, so wide text does not push the table off the panel
	var wide := []
	for i in cols.size():
		var longest := 0
		for r in rows:
			longest = maxi(longest, String(r[i]).length())
		wide.append(longest > 34)
	var grid := GridContainer.new()
	grid.columns = cols.size()
	grid.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	grid.add_theme_constant_override("h_separation", 18)
	grid.add_theme_constant_override("v_separation", 5)
	col.add_child(grid)
	for i in cols.size():
		var h := label(String(cols[i]["label"]), true, 12)
		if cols[i]["num"]:
			h.horizontal_alignment = HORIZONTAL_ALIGNMENT_RIGHT
		grid.add_child(h)
	if rows.is_empty():
		grid.add_child(label("Nothing to show.", true))
		return wrap
	# optional per-row flags: "dim" greys a row out (not reachable yet), "good" marks one you already have
	var dim: Array = t.get("dim", [])
	var good: Array = t.get("good", [])
	for ri in rows.size():
		var r = rows[ri]
		var faded: bool = ri < dim.size() and dim[ri]
		var mine: bool = ri < good.size() and good[ri]
		for i in cols.size():
			var cell := Label.new()
			cell.text = String(r[i])
			if faded:
				cell.modulate = Color(1, 1, 1, 0.38)
			elif mine and i == 0:
				cell.add_theme_color_override("font_color", GOOD)
			if cols[i]["num"]:
				cell.horizontal_alignment = HORIZONTAL_ALIGNMENT_RIGHT
			if wide[i]:
				cell.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
				cell.custom_minimum_size = Vector2(220, 0)
				cell.size_flags_horizontal = Control.SIZE_EXPAND_FILL
			grid.add_child(cell)
	return wrap
