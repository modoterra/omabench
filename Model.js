function emptyScan() {
  return {
    ok: true,
    root: "",
    displayRoot: "~/Work",
    rootExists: true,
    projects: [],
    error: ""
  }
}

function parseScan(raw) {
  var text = String(raw || "").trim()
  if (text === "") return emptyScan()
  try {
    var parsed = JSON.parse(text)
    if (!parsed || typeof parsed !== "object") return emptyScan()
    parsed.ok = parsed.ok !== false
    parsed.root = String(parsed.root || "")
    parsed.displayRoot = String(parsed.displayRoot || parsed.root || "~/Work")
    parsed.rootExists = parsed.rootExists !== false
    parsed.projects = Array.isArray(parsed.projects) ? parsed.projects : []
    parsed.error = String(parsed.error || "")
    return parsed
  } catch (e) {
    var failed = emptyScan()
    failed.ok = false
    failed.error = "Failed to read project state"
    return failed
  }
}

function asInt(value) {
  var n = parseInt(String(value === undefined || value === null ? 0 : value), 10)
  return isFinite(n) ? n : 0
}

function dirtyCount(projects) {
  var count = 0
  var list = projects || []
  for (var i = 0; i < list.length; i++) {
    if (list[i] && list[i].dirty) count++
  }
  return count
}

function listeningCount(projects) {
  var count = 0
  var list = projects || []
  for (var i = 0; i < list.length; i++) {
    if (list[i] && list[i].ports && list[i].ports.length > 0) count++
  }
  return count
}

function plural(count, one, many) {
  return count === 1 ? one : many
}

function heroMeta(payload) {
  var scan = payload || emptyScan()
  if (!scan.rootExists) return "Folder missing"
  var projects = scan.projects || []
  if (projects.length === 0) return "No git projects"
  var dirty = dirtyCount(projects)
  var listening = listeningCount(projects)
  var text = projects.length + " " + plural(projects.length, "project", "projects")
  if (dirty > 0) text += " · " + dirty + " dirty"
  if (listening > 0) text += " · " + listening + " listening"
  return text
}

function barTooltip(payload, refreshing) {
  var scan = payload || emptyScan()
  if (refreshing && (!scan.projects || scan.projects.length === 0) && scan.error === "")
    return "Scanning " + scan.displayRoot
  if (!scan.rootExists) return "Work folder not found: " + scan.displayRoot
  var projects = scan.projects || []
  if (projects.length === 0) return "No git projects in " + scan.displayRoot
  var dirty = dirtyCount(projects)
  var listening = listeningCount(projects)
  if (dirty === 0 && listening === 0) return projects.length + " projects · all clean"
  var parts = []
  if (dirty > 0) parts.push(dirty + " dirty")
  else parts.push("all clean")
  if (listening > 0) parts.push(listening + " listening")
  return parts.join(" · ")
}

function statusSegments(project) {
  var item = project || {}
  var segments = []
  var changed = asInt(item.changed)
  if (item.dirty) {
    segments.push({
      kind: "dirty",
      text: "● " + changed + " " + plural(changed, "change", "changed")
    })
  } else {
    segments.push({ kind: "clean", text: "✓ clean" })
  }

  var ahead = asInt(item.ahead)
  var behind = asInt(item.behind)
  if (ahead > 0) segments.push({ kind: "ahead", text: "↑" + ahead })
  if (behind > 0) segments.push({ kind: "behind", text: "↓" + behind })

  var ports = item.ports || []
  for (var i = 0; i < ports.length; i++) {
    var port = asInt(ports[i])
    if (port > 0) segments.push({ kind: "port", text: ":" + port })
  }
  return segments
}

function projectIndexByPath(projects, path) {
  var list = projects || []
  var needle = String(path || "")
  for (var i = 0; i < list.length; i++) {
    if (list[i] && String(list[i].path || "") === needle) return i
  }
  return -1
}

function filePathFromUrl(url) {
  var value = String(url || "")
  if (value.indexOf("file://") === 0) {
    var path = value.substring(7)
    try {
      return decodeURIComponent(path)
    } catch (e) {
      return path
    }
  }
  return value
}

function actionList(project) {
  return [
    { id: "terminal", label: "Open Terminal", icon: "󰆍" },
    { id: "editor", label: "Open Editor", icon: "󰷈" },
    { id: "folder", label: "Open Folder", icon: "󰉋" },
    { id: "github", label: "Open GitHub", icon: "󰊤", enabled: !!(project && project.githubUrl) },
    { id: "copy", label: "Copy Path", icon: "󰆏" }
  ]
}
