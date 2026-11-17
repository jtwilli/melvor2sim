extends CanvasLayer
## melvor2sim in-game panel (shell): header, tabs, connection to the local server, status line.
## Every number comes from sim_server.py on this computer (127.0.0.1); the tabs live in tab_*.gd.
## Drawn as an ordinary overlay (not an engine popup window) with its own solid background.

const DIR := "user://melvor2sim"
const TABS := [["best", "Best XP / GP", "tab_best.gd"], ["plan", "Levelling planner", "tab_plan.gd"],
	["produce", "Production", "tab_produce.gd"], ["boost", "Boost?", "tab_boost.gd"],
	["compare", "Compare loadouts", "tab_compare.gd"],
	["math", "Maths", "tab_math.gd"]]
const WAIT_STEP := 0.2        # seconds between checks for the server's ready file
const WAIT_LIMIT := 150       # 30 s

var loader: Node = null
var config := {}
var ui                         # ui.gd instance (shared widgets and colours)
var info := {}                 # /api/game/init payload
var tab := "best"
var tabs := {}                 # key -> tab node (built on first use)
var tab_buttons := {}

var http: HTTPRequest
var on_done: Callable
var asked_at := 0
var tries := 0
var waiting := false           # a ready-file wait loop is running
var responses := 0

var backdrop: ColorRect
var frame: PanelContainer
var body: Control
var status: Label
var progress: ProgressBar


# ------------------------------------------------------------------ build
func script_named(file: String):
	## Load one of the mod's scripts fresh (so an updated file is picked up without restarting the game).
	var path := DIR + "/" + file
	if not FileAccess.file_exists(path):
		return null
	var s = ResourceLoader.load(path, "", ResourceLoader.CACHE_MODE_IGNORE)
	if s == null or not (s is Script) or not s.can_instantiate():
		return null
	return s


func _ready() -> void:
	layer = 100
	visible = false
	var f := FileAccess.open(DIR + "/config.json", FileAccess.READ)
	if f:
		var parsed = JSON.parse_string(f.get_as_text())
		if parsed is Dictionary:
			config = parsed
	http = HTTPRequest.new()
	http.timeout = 180.0
	http.request_completed.connect(_on_response)
	add_child(http)

	# dimmed backdrop: blocks clicks to the game and closes the panel when clicked
	backdrop = ColorRect.new()
	backdrop.color = Color(0, 0, 0, 0.55)
	backdrop.set_anchors_preset(Control.PRESET_FULL_RECT)
	backdrop.mouse_filter = Control.MOUSE_FILTER_STOP
	backdrop.gui_input.connect(_on_backdrop_input)
	add_child(backdrop)

	frame = PanelContainer.new()
	frame.mouse_filter = Control.MOUSE_FILTER_STOP
	add_child(frame)
	var root := VBoxContainer.new()
	root.add_theme_constant_override("separation", 8)
	frame.add_child(root)

	var ui_script = script_named("ui.gd")
	if ui_script == null:
		var sb := StyleBoxFlat.new()
		sb.bg_color = Color(0.09, 0.10, 0.13)
		sb.set_content_margin_all(14)
		frame.add_theme_stylebox_override("panel", sb)
		var broken := Label.new()
		broken.text = "melvor2sim: ui.gd is missing or has an error. Run install_mod.py again."
		root.add_child(broken)
		return
	ui = ui_script.new()
	frame.add_theme_stylebox_override("panel", ui.box(ui.BACKGROUND, 8, 14, ui.BORDER))

	var top := HBoxContainer.new()
	top.add_theme_constant_override("separation", 6)
	root.add_child(top)
	var group := ButtonGroup.new()
	for t in TABS:
		var b: Button = ui.chip(t[1], group, _show_tab.bind(t[0]))
		top.add_child(b)
		tab_buttons[t[0]] = b
	top.add_child(ui.spacer())
	top.add_child(ui.button("Reload save", _reload, "Re-read your save file"))
	top.add_child(ui.button("Open in browser", _browser, "Open the full pages in your browser"))
	top.add_child(ui.button("Close", hide, "Esc also closes"))
	root.add_child(HSeparator.new())

	body = MarginContainer.new()
	body.size_flags_vertical = Control.SIZE_EXPAND_FILL
	body.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	root.add_child(body)

	var foot := HBoxContainer.new()
	foot.add_theme_constant_override("separation", 10)
	root.add_child(foot)
	status = ui.label("", true)
	status.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	status.clip_text = true
	foot.add_child(status)
	progress = ProgressBar.new()
	progress.custom_minimum_size = Vector2(220, 0)
	progress.min_value = 0.0
	progress.max_value = 1.0
	progress.show_percentage = false
	progress.visible = false
	foot.add_child(progress)

	tab_buttons[tab].button_pressed = true
	_show_tab(tab)


func _tab_node(key: String) -> Node:
	if tabs.has(key):
		return tabs[key]
	var node: Node = null
	for t in TABS:
		if t[0] == key:
			var s = script_named(t[2])
			if s != null:
				node = s.new()
				node.set("panel", self)
				node.set("ui", ui)
				node.set("visible", false)
				body.add_child(node)
				if node.has_method("build"):
					node.call("build")
				if not info.is_empty() and node.has_method("on_info"):
					node.call("on_info")
			else:
				node = ui.wrapped("This tab's script (%s) is missing or has an error. Run install_mod.py again." % t[2], ui.BAD)
				node.visible = false
				body.add_child(node)
				_log("%s failed to load" % t[2], true)
	tabs[key] = node
	return node


# ------------------------------------------------------------------ open / close / tabs
func toggle() -> void:
	if visible:
		hide()
		return
	_place()
	show()
	if ui == null:
		return
	if info.is_empty():
		if not waiting:
			tries = 0
			_connect_server()
	else:      # already connected: just pick up a newer save if there is one
		send("/api/game/init?reload=1", null, _on_refresh, "Checking your save...")


func warm_up() -> void:
	## Called by the loader shortly after the game loads: contact the server while hidden.
	if ui != null and info.is_empty() and not waiting and http.get_http_client_status() == HTTPClient.STATUS_DISCONNECTED:
		tries = 0
		_connect_server()


func _place() -> void:
	## Centre the frame, sized to the game window.
	var win := frame.get_viewport_rect().size
	var size := Vector2(minf(1500.0, win.x * 0.96), minf(900.0, win.y * 0.92))
	frame.position = ((win - size) / 2.0).round()
	frame.size = size


func _on_backdrop_input(ev: InputEvent) -> void:
	var click := ev as InputEventMouseButton
	if click != null and click.pressed:
		hide()


func _unhandled_key_input(ev: InputEvent) -> void:
	if visible and ev.is_action_pressed("ui_cancel"):
		hide()
		get_viewport().set_input_as_handled()


func _show_tab(key: String) -> void:
	tab = key
	var node := _tab_node(key)
	for k in tabs:
		tabs[k].visible = k == key
	set_progress(-1.0)
	if not info.is_empty() and node != null and node.has_method("run"):
		node.call("run")


func _browser() -> void:
	OS.shell_open(_base() + ("/planner" if tab == "plan" else "/"))


# ------------------------------------------------------------------ used by the tabs
func set_status(text: String) -> void:
	status.text = text


func set_progress(fraction: float) -> void:
	## 0..1 shows the bar; a negative value hides it.
	progress.visible = fraction >= 0.0
	progress.value = clampf(fraction, 0.0, 1.0)


func saved_at() -> String:
	return String(info.get("saved_at", "?")).substr(0, 19).replace("T", " ")


func elapsed() -> float:
	return (Time.get_ticks_msec() - asked_at) / 1000.0


func send(path: String, payload, done: Callable, message := "Working...") -> void:
	## One request at a time: a new request replaces one still in flight.
	if http.get_http_client_status() != HTTPClient.STATUS_DISCONNECTED:
		http.cancel_request()
	on_done = done
	asked_at = Time.get_ticks_msec()
	status.text = message
	var err: int
	if payload == null:
		err = http.request(_base() + path)
	else:
		err = http.request(_base() + path, ["Content-Type: application/json"], HTTPClient.METHOD_POST, JSON.stringify(payload))
	if err != OK:
		status.text = "Could not send the request (error %d)." % err


func server_lost() -> void:
	## A tab got no answer at all: the server is gone, so start it again and reconnect.
	info = {}
	DirAccess.remove_absolute(ProjectSettings.globalize_path(DIR + "/server_ready.json"))
	if not waiting:
		tries = 0
		_connect_server()


# ------------------------------------------------------------------ server
func _base() -> String:
	return "http://127.0.0.1:%d" % int(config.get("port", 8765))


func _on_response(result: int, code: int, _headers: PackedStringArray, data_bytes: PackedByteArray) -> void:
	var data = null
	if result == HTTPRequest.RESULT_SUCCESS:
		data = JSON.parse_string(data_bytes.get_string_from_utf8())
	responses += 1
	if responses <= 12:      # enough to diagnose a connection problem without flooding the status file
		_log("response %d: result %d, http %d, %d bytes, parsed %s" % [responses, result, code, data_bytes.size(), data is Dictionary])
	if on_done.is_valid():
		on_done.call(result == HTTPRequest.RESULT_SUCCESS and code == 200 and data is Dictionary, data)


func _connect_server() -> void:
	## Wait until the loader's server is listening (a file check, instant), then ask for the data.
	var ready: bool = loader != null and loader.has_method("server_ready") and loader.call("server_ready")
	if ready:
		waiting = false
		send("/api/game/init?reload=1", null, _on_init, "Loading your save...")
		return
	if tries == 0 and loader and loader.has_method("start_server"):
		loader.call("start_server")      # no-op if it is already starting
	tries += 1
	if tries > WAIT_LIMIT:
		waiting = false
		status.text = "The local server did not start. Reopen the panel to try again, or in the melvor2sim folder run:  python -I sim_server.py --no-browser"
		_log("server did not become ready", true)
		return
	waiting = true
	status.text = "Starting the local server..."
	get_tree().create_timer(WAIT_STEP).timeout.connect(_connect_server)


func _on_init(ok: bool, data) -> void:
	if ok:
		_log("panel connected")
		_set_info(data)
		return
	# the ready file was there but the server did not answer: start a fresh one and wait again
	_log("server marked ready but did not answer; restarting it", true)
	if loader and loader.has_method("start_server"):
		DirAccess.remove_absolute(ProjectSettings.globalize_path(DIR + "/server_ready.json"))
		loader.call("start_server")
	tries = 1
	get_tree().create_timer(WAIT_STEP).timeout.connect(_connect_server)


func _set_info(data: Dictionary) -> void:
	info = data
	for k in tabs:
		if tabs[k].has_method("on_info"):
			tabs[k].call("on_info")
	var node = tabs.get(tab)
	if node != null and node.has_method("run"):
		node.call("run")


func _on_refresh(ok: bool, data) -> void:
	if ok:
		_set_info(data)
	else:
		server_lost()


func _reload() -> void:
	send("/api/game/reload", {}, _on_reload, "Re-reading your save...")


func _on_reload(ok: bool, data) -> void:
	if ok:
		_set_info(data)
	else:
		status.text = "Could not reload the save."


func _log(msg: String, error := false) -> void:
	if loader and loader.has_method("_note"):
		loader.call("_note", msg, error)
