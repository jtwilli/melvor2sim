extends VBoxContainer
## Maths tab: small probability tools that need no server. Worked out here, instantly.
##   Attempts until a drop: how many attempts give a C% chance of seeing a p% drop at least k times.

const LEVELS := [0.10, 0.25, 0.50, 0.75, 0.80, 0.90, 0.95, 0.99, 0.999]

var panel            # panel.gd (shell)
var ui               # ui.gd
var chance: LineEdit
var wanted: SpinBox
var rate: SpinBox
var content: VBoxContainer


func build() -> void:
	add_theme_constant_override("separation", 8)
	add_child(ui.label("Attempts until a drop", false, 18))
	var row: HFlowContainer = ui.flow()
	add_child(row)
	row.add_child(ui.label("Drop chance", true))
	chance = LineEdit.new()
	chance.text = "2%"
	chance.placeholder_text = "2%  or  1/50"
	chance.custom_minimum_size = Vector2(130, 0)
	chance.tooltip_text = "A percentage (2 or 2%) or odds (1/50, 1 in 50)"
	chance.text_changed.connect(func(_t): _update())
	row.add_child(chance)
	row.add_child(ui.label("Drops wanted", true))
	wanted = SpinBox.new()
	wanted.min_value = 1
	wanted.max_value = 1000
	wanted.value = 1
	wanted.tooltip_text = "How many times the drop has to show up (e.g. 3 for three copies)"
	wanted.value_changed.connect(func(_v): _update())
	row.add_child(wanted)
	row.add_child(ui.label("Attempts per hour", true))
	rate = SpinBox.new()
	rate.min_value = 0
	rate.max_value = 1000000
	rate.value = 0
	rate.tooltip_text = "Optional: kills or actions per hour, to turn attempts into time (0 = leave out)"
	rate.value_changed.connect(func(_v): _update())
	row.add_child(rate)
	content = ui.scroll_area(self)
	_update()


func run() -> void:
	_update()


# ------------------------------------------------------------------ maths
func _parse(text: String) -> float:
	## "2", "2%", "0.5 %" -> percent; "1/50", "1 in 50" -> odds. Returns -1 when it can't be read.
	var t := text.strip_edges().to_lower().replace(",", "").replace(" in ", "/").replace(" ", "")
	if t.contains("/"):
		var parts := t.split("/")
		if parts.size() != 2 or not parts[0].is_valid_float() or not parts[1].is_valid_float() or float(parts[1]) == 0.0:
			return -1.0
		return float(parts[0]) / float(parts[1])
	t = t.trim_suffix("%")
	return float(t) / 100.0 if t.is_valid_float() else -1.0


func _at_least(n: int, p: float, k: int) -> float:
	## Chance of k or more drops in n attempts (binomial), summed in logs so large n doesn't underflow.
	if n < k:
		return 0.0
	if k == 1:
		return 1.0 - exp(n * log(1.0 - p))
	var lt := n * log(1.0 - p)          # log of the chance of exactly 0 drops
	var step := log(p / (1.0 - p))
	var terms := [lt]
	for i in range(1, k):
		lt += log(float(n - i + 1) / i) + step
		terms.append(lt)
	var top: float = terms.max()
	var s := 0.0
	for x in terms:
		s += exp(x - top)
	return clampf(1.0 - exp(top + log(s)), 0.0, 1.0)


func _attempts(p: float, k: int, c: float) -> int:
	## Fewest attempts with at least a `c` chance of k drops.
	if p >= 1.0:
		return k
	if k == 1:
		var n := int(ceil(log(1.0 - c) / log(1.0 - p) - 1e-9))
		return maxi(n, 1)
	var hi := maxi(k, int(ceil(k / p)))
	while _at_least(hi, p, k) < c:
		hi *= 2
	var lo := k
	while lo < hi:
		var mid := (lo + hi) / 2
		if _at_least(mid, p, k) >= c:
			hi = mid
		else:
			lo = mid + 1
	return lo


func _time(n: int) -> String:
	var h := n / rate.value
	if h < 1.0:
		return "%d min" % maxi(1, int(round(h * 60.0)))
	if h < 48.0:
		return "%dh %02dm" % [int(h), int(round(fmod(h, 1.0) * 60.0)) % 60]
	return "%.1f days" % (h / 24.0)


func _odds(p: float) -> String:
	var x := 1.0 / p
	return "1 in " + (ui.commas(int(round(x))) if x >= 10.0 else "%.2f" % x)


# ------------------------------------------------------------------ view
func _update() -> void:
	if content == null:
		return
	var p := _parse(chance.text)
	if p <= 0.0 or p > 1.0:
		ui.clear(content)
		content.add_child(ui.wrapped("Enter a drop chance above 0 and up to 100%, for example 2% or 1/50.", ui.WARN))
		return
	var k := int(wanted.value)
	var timed := rate.value > 0.0
	var mean := k / p
	var kpis := [{"v": _odds(p), "l": "%s per attempt" % ui.pct(p, false)},
		{"v": ui.commas(int(round(mean))), "l": "attempts on average" + (" (%s)" % _time(int(round(mean))) if timed else "")},
		{"v": ui.commas(_attempts(p, k, 0.5)), "l": "attempts for a 50/50 chance"}]
	var cols := [{"label": "Confidence", "num": true}, {"label": "Attempts needed", "num": true},
		{"label": "Exact chance at that count", "num": true}]
	if timed:
		cols.append({"label": "Time at %s / hr" % ui.commas(int(rate.value)), "num": true})
	var rows := []
	for c in LEVELS:
		var n := _attempts(p, k, c)
		var label := "99.9%" if c > 0.99 else "%d%%" % int(round(c * 100.0))
		var row := [label, ui.commas(n), "%.2f%%" % (_at_least(n, p, k) * 100.0)]
		if timed:
			row.append(_time(n))
		rows.append(row)
	var what := "the drop" if k == 1 else "%d drops" % k
	var note := ("Chance of %s within n attempts: 1 − (1 − p)^n. Solving for n gives n = ln(1 − C) ÷ ln(1 − p), rounded up. " % what
		if k == 1 else
		"Chance of %s within n attempts is a binomial tail: 1 − Σ (i < %d) C(n, i) pⁱ (1 − p)ⁿ⁻ⁱ; the table finds the smallest n reaching each confidence. " % [what, k])
	note += "Every attempt is independent, so a dry streak doesn't make the next drop any likelier: the table counts from now."
	ui.render_view(content, {"kpis": kpis, "tables": [{"title": "Attempts to see %s" % what, "note": note,
		"columns": cols, "rows": rows}]})
