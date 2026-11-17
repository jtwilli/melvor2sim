extends Control
## Paired bar chart for the Compare tab: one group per target, bar A (outline) and bar B (filled),
## a 95% range on each bar, the % difference above the group, and the enemy's icon and name below.

signal picked(id: String)

const GROUP_MIN := 58.0
const GROUP_MAX := 120.0
const LEFT := 58.0            # room for the value axis
const TOP := 26.0             # room for the % labels
const BOTTOM := 62.0          # room for icon + name
const ICON := 30.0

var ui                         # ui.gd
var rows: Array = []           # [{id, name, icon, a: {value, lo, hi} or null, b: ..., delta, verdict}]
var two_sided := false
var selected := ""
var shown := 0                 # how many groups fit (the rest are listed by the tab)
var _group_w := GROUP_MIN
var _top_value := 1.0


func _ready() -> void:
	custom_minimum_size = Vector2(320, 260)
	mouse_filter = Control.MOUSE_FILTER_STOP
	resized.connect(queue_redraw)


func set_rows(new_rows: Array, both: bool) -> void:
	rows = new_rows
	two_sided = both
	queue_redraw()


func select(id: String) -> void:
	selected = id
	queue_redraw()


func _layout() -> void:
	var width := maxf(1.0, size.x - LEFT - 8.0)
	var n := maxi(1, rows.size())
	_group_w = clampf(width / n, GROUP_MIN, GROUP_MAX)
	shown = mini(rows.size(), int(floor(width / _group_w)))
	var top := 0.0
	for i in shown:
		for side in ["a", "b"]:
			var m = rows[i].get(side)
			if m is Dictionary:
				top = maxf(top, float(m.get("hi", m.get("value", 0.0))))
	_top_value = _nice(top) if top > 0.0 else 1.0


func _nice(v: float) -> float:
	## Round up to a tidy axis maximum (1, 2, 2.5, 5 x a power of ten).
	var p := pow(10.0, floor(log(v) / log(10.0)))
	for m in [1.0, 2.0, 2.5, 5.0, 10.0]:
		if v <= m * p:
			return m * p
	return 10.0 * p


func _y(value: float) -> float:
	var h := size.y - TOP - BOTTOM
	return TOP + h * (1.0 - clampf(value / _top_value, 0.0, 1.0))


func _draw() -> void:
	if ui == null:
		return
	_layout()
	var font := get_theme_default_font()
	var fs := get_theme_default_font_size()
	var small := maxi(10, fs - 3)
	var base := size.y - BOTTOM
	# value axis
	for i in 5:
		var v := _top_value * i / 4.0
		var y := _y(v)
		draw_line(Vector2(LEFT, y), Vector2(size.x - 4.0, y), Color(1, 1, 1, 0.07 if i > 0 else 0.25), 1.0)
		draw_string(font, Vector2(0, y + small * 0.35), ui.num(v), HORIZONTAL_ALIGNMENT_RIGHT, LEFT - 8.0, small, ui.DIM)
	if rows.is_empty():
		draw_string(font, Vector2(LEFT, TOP + 40.0), "No targets yet. Press Compare.", HORIZONTAL_ALIGNMENT_LEFT, -1, fs, ui.DIM)
		return
	var bar_w := minf(26.0, (_group_w - 18.0) / (2.0 if two_sided else 1.0))
	for i in shown:
		var r: Dictionary = rows[i]
		var x0 := LEFT + i * _group_w
		var mid := x0 + _group_w / 2.0
		if String(r.get("id", "")) == selected:
			draw_rect(Rect2(x0 + 2.0, 2.0, _group_w - 4.0, size.y - 4.0), Color(1, 1, 1, 0.06), true)
		var a = r.get("a")
		var b = r.get("b")
		var verdict := String(r.get("verdict", "")) if r.get("verdict") != null else ""
		var b_color: Color = ui.SIDE_B if verdict == "b" else (ui.BAD if verdict == "a" else ui.SAME)
		var ax := mid - (bar_w + 2.0 if two_sided else bar_w / 2.0)
		var bx := mid + 2.0
		if a is Dictionary:
			_bar(ax, bar_w, a, base, ui.SIDE_A, not two_sided)
		elif two_sided:
			draw_string(font, Vector2(ax - 4.0, base - 6.0), "x", HORIZONTAL_ALIGNMENT_CENTER, bar_w + 8.0, small, ui.BAD)
		if two_sided:
			if b is Dictionary:
				_bar(bx, bar_w, b, base, b_color, true)
			else:
				draw_string(font, Vector2(bx - 4.0, base - 6.0), "x", HORIZONTAL_ALIGNMENT_CENTER, bar_w + 8.0, small, ui.BAD)
		# % difference above the taller bar
		if two_sided and r.get("delta") != null:
			var tallest := 0.0
			for m in [a, b]:
				if m is Dictionary:
					tallest = maxf(tallest, float(m.get("hi", m["value"])))
			draw_string(font, Vector2(x0, _y(tallest) - 6.0), ui.pct(r["delta"]), HORIZONTAL_ALIGNMENT_CENTER, _group_w, small, b_color)
		# icon and name
		var tex: Texture2D = ui.texture(r.get("icon"))
		if tex != null:
			draw_texture_rect(tex, Rect2(mid - ICON / 2.0, base + 6.0, ICON, ICON), false)
		var title := String(r.get("name", ""))
		draw_string(font, Vector2(x0 + 2.0, base + ICON + 6.0 + small + 2.0), title, HORIZONTAL_ALIGNMENT_CENTER, _group_w - 4.0, small,
			Color(1, 1, 1, 0.9))


func _bar(x: float, w: float, m: Dictionary, base: float, color: Color, filled: bool) -> void:
	var top := _y(float(m["value"]))
	var rect := Rect2(x, top, w, maxf(1.0, base - top))
	if filled:
		draw_rect(rect, color, true)
	else:
		draw_rect(rect, Color(color.r, color.g, color.b, 0.18), true)
		draw_rect(rect, color, false, 2.0)
	# 95% range
	var lo := _y(float(m.get("lo", m["value"])))
	var hi := _y(float(m.get("hi", m["value"])))
	if lo - hi >= 2.0:
		var cx := x + w / 2.0
		var c := Color(1, 1, 1, 0.9)
		draw_line(Vector2(cx, hi), Vector2(cx, lo), c, 1.5)
		draw_line(Vector2(cx - 4.0, hi), Vector2(cx + 4.0, hi), c, 1.5)
		draw_line(Vector2(cx - 4.0, lo), Vector2(cx + 4.0, lo), c, 1.5)


func _index_at(pos: Vector2) -> int:
	if pos.x < LEFT or shown <= 0:
		return -1
	var i := int(floor((pos.x - LEFT) / _group_w))
	return i if i >= 0 and i < shown else -1


func _gui_input(ev: InputEvent) -> void:
	var click := ev as InputEventMouseButton
	if click != null and click.pressed and click.button_index == MOUSE_BUTTON_LEFT:
		var i := _index_at(click.position)
		if i >= 0:
			picked.emit(String(rows[i].get("id", "")))


func _get_tooltip(at_position: Vector2) -> String:
	var i := _index_at(at_position)
	if i < 0 or ui == null:
		return ""
	var r: Dictionary = rows[i]
	var lines := PackedStringArray([String(r.get("name", ""))])
	for side in ["a", "b"]:
		var m = r.get(side)
		if m is Dictionary:
			lines.append("%s: %s  (%s to %s)" % [side.to_upper(), ui.num(m["value"]), ui.num(m.get("lo")), ui.num(m.get("hi"))])
		elif two_sided or side == "a":
			lines.append("%s: cannot kill" % side.to_upper())
	if r.get("delta") != null:
		lines.append("B vs A: " + ui.pct(r["delta"]))
	return "\n".join(lines)
