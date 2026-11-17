extends VBoxContainer
## GP planner tab: the best self-sufficient ways to make gold that are still locked, by a skill level or by
## something else (a tool, a shop unlock, an Astrology node, a 'do X first' count). The window buttons
## limit it to methods within that many levels of where your skills are now.

const WINDOWS := [["Within 10 levels", 10], ["20", 20], ["30", 30], ["40", 40], ["50", 50], ["Any level", 0]]

var panel            # panel.gd (shell)
var ui               # ui.gd
var within := 10
var passive: CheckBox
var content: VBoxContainer


func build() -> void:
	add_theme_constant_override("separation", 8)
	var row: HFlowContainer = ui.flow()
	add_child(row)
	var group := ButtonGroup.new()
	for w in WINDOWS:
		var b: Button = ui.chip(w[0], group, _window.bind(w[1]),
			"Only methods whose levels are all within this many of yours" if w[1] > 0 else "Every locked method")
		b.button_pressed = w[1] == within
		row.add_child(b)
	passive = CheckBox.new()
	passive.text = "Count farming as free"
	passive.tooltip_text = "Treat Farming and Ranching produce (herbs, dragonscales) as free and unlimited"
	passive.toggled.connect(func(_on): run())
	row.add_child(passive)
	content = ui.scroll_area(self)


func _window(n: int) -> void:
	within = n
	run()


func run() -> void:
	var p := {"within": within, "passive": passive.button_pressed}
	panel.send("/api/game/view", {"view": "gp_plan", "params": p}, _on_view, "Costing locked gold methods...")


func _on_view(ok: bool, data) -> void:
	if not ok:
		if data is Dictionary:
			panel.set_status("The server reported a problem. " + String(data.get("error", "")))
		else:
			panel.server_lost()
		return
	panel.set_status("Done in %.1fs. Save from %s." % [panel.elapsed(), panel.saved_at()])
	ui.render_view(content, data)
