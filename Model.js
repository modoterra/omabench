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

function sanitizeText(value, maxLen) {
  var text = String(value || "")
  text = text.replace(/[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]/g, "")
  text = text.replace(/[<>]/g, "")
  text = text.replace(/\s+/g, " ").trim()
  if (maxLen > 0 && text.length > maxLen) text = text.substring(0, maxLen).replace(/\s+$/, "")
  return text
}

function normalizeArgv(value) {
  if (!Array.isArray(value)) return []
  var argv = []
  for (var i = 0; i < value.length; i++) {
    var part = String(value[i] || "").trim()
    if (part === "") return []
    argv.push(part)
  }
  return argv
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
    parsed.projects = Array.isArray(parsed.projects) ? parsed.projects.map(normalizeProject) : []
    parsed.error = String(parsed.error || "")
    return parsed
  } catch (e) {
    var failed = emptyScan()
    failed.ok = false
    failed.error = "Failed to read project state"
    return failed
  }
}

function normalizeAction(action, index) {
  if (!action || typeof action !== "object") return null
  var argv = normalizeArgv(action.argv)
  if (argv.length === 0) return null
  var command = sanitizeText(action.command || argv.join(" "), 200)
  var label = sanitizeText(action.label || command, 32)
  if (label === "" || command === "") return null
  return {
    id: String(action.id || ("omafile:" + index)),
    label: label,
    command: command,
    argv: argv,
    digest: String(action.digest || ""),
    trusted: action.trusted === true
  }
}

function normalizeProject(project) {
  if (!project || typeof project !== "object") return project
  project.name = sanitizeText(project.name, 64)
  project.summary = sanitizeText(project.summary, 140)
  project.url = sanitizeText(project.url || project.githubUrl || "", 500)
  project.omafileError = sanitizeText(project.omafileError || "", 280)
  var rawActions = Array.isArray(project.actions) ? project.actions : []
  var actions = []
  for (var i = 0; i < rawActions.length; i++) {
    var action = normalizeAction(rawActions[i], i)
    if (action) actions.push(action)
  }
  project.actions = actions
  return project
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

function dirtyRatio(projects) {
  var list = projects || []
  if (list.length === 0) return 0
  return dirtyCount(list) / list.length
}

function clamp01(value) {
  var n = Number(value)
  if (!isFinite(n) || n < 0) return 0
  if (n > 1) return 1
  return n
}

function rgbToHsl(r, g, b) {
  var red = clamp01(r)
  var green = clamp01(g)
  var blue = clamp01(b)
  var max = Math.max(red, green, blue)
  var min = Math.min(red, green, blue)
  var lightness = (max + min) / 2
  if (max === min) return { h: 0, s: 0, l: lightness }
  var delta = max - min
  var saturation = lightness > 0.5 ? delta / (2 - max - min) : delta / (max + min)
  var hue = 0
  if (max === red) hue = ((green - blue) / delta + (green < blue ? 6 : 0)) / 6
  else if (max === green) hue = ((blue - red) / delta + 2) / 6
  else hue = ((red - green) / delta + 4) / 6
  return { h: hue, s: saturation, l: lightness }
}

function dirtyMarkHsl(ratio, urgentR, urgentG, urgentB) {
  var hsl = rgbToHsl(urgentR, urgentG, urgentB)
  var greenHue = 120 / 360
  return {
    h: greenHue * (1 - clamp01(ratio)),
    s: hsl.s,
    l: hsl.l
  }
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

function projectUrl(project) {
  if (!project) return ""
  var explicit = String(project.url || "")
  if (explicit !== "") return explicit
  return String(project.githubUrl || "")
}

function isGitHubUrl(url) {
  var value = String(url || "")
  return value.indexOf("https://github.com/") === 0 || value.indexOf("http://github.com/") === 0
}

function urlActionLabel(project) {
  return isGitHubUrl(projectUrl(project)) ? "GitHub" : "Site"
}

function urlActionTooltip(project) {
  return isGitHubUrl(projectUrl(project)) ? "Open GitHub" : "Open Site"
}

function urlActionIcon(project) {
  return isGitHubUrl(projectUrl(project)) ? "󰊤" : "󰖟"
}

function builtinActionList(project) {
  return [
    { id: "terminal", label: "Terminal", tooltip: "Open Terminal (t · 1)", icon: "󰆍", kind: "builtin" },
    { id: "editor", label: "Editor", tooltip: "Open Editor (e · 2)", icon: "󰷈", kind: "builtin" },
    { id: "folder", label: "Folder", tooltip: "Open Folder (f · 3)", icon: "󰉋", kind: "builtin" },
    {
      id: "url",
      label: urlActionLabel(project),
      tooltip: urlActionTooltip(project) + " (g · 4)",
      icon: urlActionIcon(project),
      enabled: projectUrl(project) !== "",
      kind: "builtin"
    },
    { id: "copy", label: "Copy", tooltip: "Copy Path (c · 5)", icon: "󰆏", kind: "builtin" }
  ]
}

function projectActionList(project) {
  var extras = project && Array.isArray(project.actions) ? project.actions : []
  var actions = []
  for (var i = 0; i < extras.length; i++) {
    var extra = extras[i] || {}
    var argv = normalizeArgv(extra.argv)
    if (argv.length === 0) continue
    var command = sanitizeText(extra.command || argv.join(" "), 200)
    var extraLabel = sanitizeText(extra.label || command, 32)
    if (extraLabel === "") continue
    var trusted = extra.trusted === true
    actions.push({
      id: String(extra.id || ("omafile:" + i)),
      label: extraLabel,
      tooltip: extraLabel + " — " + command + (trusted ? " (trusted)" : ""),
      icon: trusted ? "󰐊" : "󰌾",
      command: command,
      argv: argv,
      digest: String(extra.digest || ""),
      trusted: trusted,
      kind: "omafile"
    })
  }
  return actions
}

function actionList(project) {
  return builtinActionList(project).concat(projectActionList(project))
}

function omafileConfirmMessage(project, action) {
  var path = ""
  if (project) path = String(project.displayPath || project.path || "")
  if (path === "") path = "this project"
  var command = ""
  if (action && Array.isArray(action.argv) && action.argv.length > 0) command = action.argv.join(" ")
  else if (action) command = String(action.command || "")
  return "Run this Omafile command in " + path + "?\n\n" + command + "\n\nOmabench will remember this command for this project."
}
