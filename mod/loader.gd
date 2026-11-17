extends Node
## melvor2sim in-game loader (unofficial). Adds one button to the game's top banner.
## Loaded as an autoload through override.cfg next to the game executable.
## Writes what it did to user://melvor2sim/status.json so problems can be diagnosed.

const DIR := "user://melvor2sim"
const HEADER_SCRIPT := "GameMenus/Header/GameHeader.cs"
const BUTTON_NAME := "Melvor2SimButton"

var status := {"loaded": true, "engine": "", "header_found": false, "button_added": false, "errors": [], "events": []}
var panel: Node = null
var panel_mtime := 0


var config := {}
var server_pid := 0
var started_at := 0


func _check_scripts(dir: String, out: String) -> void:
	## Developer check: compile every .gd in `dir`, write {file: ok} to `out`, and quit before the game loads.
	##   Melvor2.exe --headless -- --m2s-check <folder> <result.json>
	var result := {}
	var d := DirAccess.open(dir)
	if d:
		for f in d.get_files():
			if f.ends_with(".gd"):
				var s = ResourceLoader.load(dir.path_join(f), "", ResourceLoader.CACHE_MODE_IGNORE)
				result[f] = s != null and s is Script and s.can_instantiate()
	var fa := FileAccess.open(out, FileAccess.WRITE)
	if fa:
		fa.store_string(JSON.stringify(result, "  "))
		fa.close()
	get_tree().quit()


func _ready() -> void:
	var args := OS.get_cmdline_user_args()
	var at := args.find("--m2s-check")
	if at >= 0 and args.size() > at + 2:
		_check_scripts(args[at + 1], args[at + 2])
		return
	status["engine"] = Engine.get_version_info().get("string", "")
	_note("autoload ready")
	var f := FileAccess.open(DIR + "/config.json", FileAccess.READ)
	if f:
		var parsed = JSON.parse_string(f.get_as_text())
		if parsed is Dictionary:
			config = parsed
	start_server()      # start now, while the game loads, so the panel never waits for it
	get_tree().node_added.connect(_on_node_added)
	_scan(get_tree().root)
	var t := Timer.new()          # fallback: the header may be built before we connect, or rebuilt later
	t.wait_time = 2.0
	t.timeout.connect(func(): _scan(get_tree().root))
	add_child(t)
	t.start()


# ------------------------------------------------------------------ local server
func port() -> int:
	return int(config.get("port", 8765))


func _ready_path() -> String:
	return ProjectSettings.globalize_path(DIR + "/server_ready.json")


func server_ready() -> bool:
	## True once a server is listening. Checks the file the server writes, never the port itself:
	## on Windows a connection to a port nobody listens on takes about 2 seconds to fail.
	var f := FileAccess.open(DIR + "/server_ready.json", FileAccess.READ)
	if f == null:
		return false
	var info = JSON.parse_string(f.get_as_text())
	if not (info is Dictionary):
		return false
	var pid = info.get("pid")
	return pid == null or OS.is_process_running(int(pid))     # null pid: another server already had the port


func start_server() -> void:
	if server_ready():
		_note("server already running")
		return
	if server_pid > 0 and OS.is_process_running(server_pid):
		return              # still starting up
	var py := String(config.get("python", ""))
	var repo := String(config.get("repo", ""))
	if py == "" or repo == "":
		_note("config.json has no python/repo path; run install_mod.py again", true)
		return
	DirAccess.remove_absolute(_ready_path())
	server_pid = OS.create_process(py, ["-I", repo.path_join("sim_server.py"), "--no-browser", "--port", str(port()),
		"--exit-with", str(OS.get_process_id()), "--ready-file", _ready_path()])
	started_at = Time.get_ticks_msec()
	_note("started server, pid %d" % server_pid, server_pid <= 0)


func _note(msg: String, error := false) -> void:
	(status["errors"] if error else status["events"]).append(msg)
	if status["events"].size() > 40:
		status["events"] = status["events"].slice(-40)
	var f := FileAccess.open(DIR + "/status.json", FileAccess.WRITE)
	if f:
		f.store_string(JSON.stringify(status, "  "))


func _is_header(n: Node) -> bool:
	var s = n.get_script()
	return s != null and String(s.resource_path).ends_with(HEADER_SCRIPT)


func _on_node_added(n: Node) -> void:
	if _is_header(n):
		_attach.call_deferred(n)


func _scan(n: Node) -> void:
	if _is_header(n):
		_attach(n)
		return
	for c in n.get_children():
		_scan(c)


func _attach(header: Node) -> void:
	if not is_instance_valid(header):
		return
	if not status["header_found"]:
		status["header_found"] = true
		_note("header found at %s" % header.get_path())
	if header.find_child(BUTTON_NAME, true, false) != null:
		return
	# Sit beside the banner's own buttons. The per-page options row is hidden on most pages, so it is the last resort.
	var anchor: Node = null
	for prop in ["StatisticsButton", "MasteryGuildButton", "MilestonesButton", "TutorialButton", "OptionsContainer"]:
		var n = header.get(prop)
		if n is Control and (n as Control).get_parent() is Container:
			anchor = n
			break
	if anchor == null:
		if not status.has("no_anchor"):
			status["no_anchor"] = true
			status["tree"] = _dump(header, 0)
			_note("no place found in the header for the button", true)
		return
	var box := anchor.get_parent()
	var b := Button.new()
	b.name = BUTTON_NAME
	b.text = "XP"
	b.tooltip_text = "Best XP, levelling planner and combat simulator (melvor2sim)"
	b.focus_mode = Control.FOCUS_NONE
	b.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	b.pressed.connect(_on_pressed)
	box.add_child(b)
	box.move_child(b, anchor.get_index() + 1)
	status["button_added"] = true
	status["tree"] = _dump(header, 0)
	_note("button added to %s after %s" % [box.get_path(), anchor.name])
	get_tree().create_timer(1.5).timeout.connect(_report.bind(b))
	get_tree().create_timer(0.5).timeout.connect(_warm_up)


func _report(b: Button) -> void:
	if is_instance_valid(b):
		status["button"] = {"visible_in_tree": b.is_visible_in_tree(), "rect": str(b.get_global_rect()),
			"window": str(get_tree().root.size)}
		_note("button visible=%s rect=%s" % [b.is_visible_in_tree(), b.get_global_rect()])


func _dump(n: Node, depth: int) -> Array:
	## The header's layout (name, type, visible, size), a few levels deep, for diagnosing placement.
	var out := []
	var c := n as Control
	out.append("%s%s [%s]%s %s" % ["  ".repeat(depth), n.name, n.get_class(),
		"" if c == null or c.visible else " HIDDEN", "" if c == null else str(c.size)])
	if depth < 6:
		for ch in n.get_children():
			out.append_array(_dump(ch, depth + 1))
	return out


func _on_pressed() -> void:
	status["presses"] = int(status.get("presses", 0)) + 1
	_note("button pressed")
	if _ensure_panel() and panel.has_method("toggle"):
		panel.call("toggle")


func _warm_up() -> void:
	## Start the local server in the background so the first click is quick.
	if _ensure_panel() and panel.has_method("warm_up"):
		panel.call("warm_up")


func _ensure_panel() -> bool:
	var path := DIR + "/panel.gd"
	var mtime := 0                  # newest change among the mod's scripts
	var d := DirAccess.open(DIR)
	if d:
		for f in d.get_files():
			if f.ends_with(".gd") and f != "loader.gd":
				mtime = maxi(mtime, FileAccess.get_modified_time(DIR + "/" + f))
	if panel != null and is_instance_valid(panel) and mtime != panel_mtime:
		panel.queue_free()          # a script was updated: rebuild the panel without restarting the game
		panel = null
		_note("mod scripts changed, reloading the panel")
	if panel == null or not is_instance_valid(panel):
		var script = ResourceLoader.load(path, "", ResourceLoader.CACHE_MODE_IGNORE) if FileAccess.file_exists(path) else null
		if script == null or not script.can_instantiate():
			_note("panel.gd is missing or has a script error", true)
			return false
		panel_mtime = mtime
		panel = script.new()
		panel.set("loader", self)
		get_tree().root.add_child(panel)
		_note("panel created")
	return true
