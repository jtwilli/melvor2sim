extends VBoxContainer
## Combat GP tab: the richest enemies to farm for drops, and the gear tier and level melee, ranged and
## magic each need to farm them safely. The window buttons limit it to tiers within that many levels of yours.

const WINDOWS := [["Within 10 levels", 10], ["20", 20], ["30", 30], ["40", 40], ["50", 50], ["Any level", 0]]

var panel            # panel.gd (shell)
var ui               # ui.gd
var within := 10
var content: VBoxContainer


func build() -> void:
	add_theme_constant_override("separation", 8)
	var row: HFlowContainer = ui.flow()
	add_child(row)
	var group := ButtonGroup.new()
	for w in WINDOWS:
		var b: Button = ui.chip(w[0], group, _window.bind(w[1]),
			"Only gear tiers within this many levels of yours" if w[1] > 0 else "Every tier up to 99")
		b.button_pressed = w[1] == within
		row.add_child(b)
	content = ui.scroll_area(self)


func _window(n: int) -> void:
	within = n
	run()


func run() -> void:
	panel.send("/api/game/view", {"view": "combat_gp", "params": {"within": within}}, _on_view,
		"Simulating enemies at each gear tier (about half a minute the first time)...")


func _on_view(ok: bool, data) -> void:
	if not ok:
		if data is Dictionary:
			panel.set_status("The server reported a problem. " + String(data.get("error", "")))
		else:
			panel.server_lost()
		return
	panel.set_status("Done in %.1fs. Save from %s." % [panel.elapsed(), panel.saved_at()])
	ui.render_view(content, data)
