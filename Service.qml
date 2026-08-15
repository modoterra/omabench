import QtQuick
import Quickshell
import Quickshell.Io
import qs.Commons
import "Model.js" as Model

Item {
  id: root

  property var settings: ({})
  property bool opened: false

  property var projects: []
  property var roots: []
  property string workRoot: ""
  property string displayRoot: "~/Work"
  property bool rootExists: true
  property bool refreshing: false
  property string lastError: ""
  property string actionStatus: ""
  property var pendingOmafile: null
  property bool grantingTrust: false

  readonly property int dirtyCount: Model.dirtyCount(projects)
  readonly property int refreshIntervalSec: {
    var configured = intSetting("refreshIntervalSec", 8, 2, 3600)
    return opened ? Math.min(configured, 4) : configured
  }
  readonly property int maxDepth: intSetting("maxDepth", 6, 1, 12)
  readonly property string workRootSetting: {
    var value = String(setting("workRoot", "~/Work") || "~/Work").trim()
    return value === "" ? "~/Work" : value
  }
  readonly property var workRootsSetting: {
    var value = setting("workRoots", null)
    if (!Array.isArray(value)) return [workRootSetting]
    var roots = []
    for (var i = 0; i < value.length; i++) {
      var path = String(value[i] || "").trim()
      if (path !== "") roots.push(path)
    }
    return roots.length > 0 ? roots : [workRootSetting]
  }
  readonly property string workRootsJson: JSON.stringify(workRootsSetting)
  readonly property string helperPath: Model.filePathFromUrl(Qt.resolvedUrl("scan.py"))
  readonly property string home: Quickshell.env("HOME") || ""
  readonly property string trustStorePath: {
    var stateHome = String(Quickshell.env("XDG_STATE_HOME") || "").trim()
    if (stateHome !== "") return stateHome + "/omabench/trust.json"
    return home + "/.local/state/omabench/trust.json"
  }
  readonly property string rootStorePath: {
    var stateHome = String(Quickshell.env("XDG_STATE_HOME") || "").trim()
    if (stateHome !== "") return stateHome + "/omabench/roots.json"
    return home + "/.local/state/omabench/roots.json"
  }
  readonly property string pendingConfirmMessage: Model.omafileConfirmMessage(
    pendingOmafile ? pendingOmafile.project : null,
    pendingOmafile ? pendingOmafile.action : null
  )
  readonly property var scanPayload: ({
    ok: lastError === "",
    root: workRoot,
    displayRoot: displayRoot,
    rootExists: rootExists,
    roots: roots,
    projects: projects,
    error: lastError
  })

  property string _scanOutput: ""
  property string _scanError: ""
  property string _rootOutput: ""
  property string _rootError: ""
  property bool _refreshQueued: false

  function setting(name, fallback) {
    var value = settings ? settings[name] : undefined
    return value === undefined || value === null ? fallback : value
  }

  function intSetting(name, fallback, min, max) {
    var n = parseInt(String(setting(name, fallback)), 10)
    if (!isFinite(n)) n = fallback
    if (n < min) n = min
    if (n > max) n = max
    return n
  }

  function refresh() {
    if (helperPath === "") return
    if (scanProcess.running) {
      _refreshQueued = true
      return
    }
    _refreshQueued = false
    _scanOutput = ""
    _scanError = ""
    refreshing = true
    scanProcess.command = [
      "python3", helperPath,
      "--root", workRootSetting,
      "--roots-json", workRootsJson,
      "--root-store", rootStorePath,
      "--home", home,
      "--max-depth", String(maxDepth),
      "--trust-store", trustStorePath
    ]
    scanProcess.running = true
  }

  function applyScan(raw) {
    var parsed = Model.parseScan(raw)
    if (!parsed.ok && parsed.projects.length === 0 && parsed.error !== "") {
      lastError = parsed.error
      return
    }
    workRoot = parsed.root
    displayRoot = parsed.displayRoot
    rootExists = parsed.rootExists === true
    roots = parsed.roots
    projects = parsed.projects
    lastError = parsed.error
  }

  function runRootCommand(action, path) {
    if (rootProcess.running || helperPath === "") return
    _rootOutput = ""
    _rootError = ""
    rootProcess.command = [
      "python3", helperPath, action,
      "--root", workRootSetting,
      "--roots-json", workRootsJson,
      "--root-store", rootStorePath,
      "--home", home,
      "--path", String(path || "")
    ]
    rootProcess.running = true
  }

  function addRoot(path) {
    runRootCommand("add-root", path)
  }

  function toggleRoot(path) {
    runRootCommand("toggle-root", path)
  }

  function removeRoot(path) {
    runRootCommand("remove-root", path)
  }

  function elideStatus(text) {
    var value = String(text || "").replace(/\s+/g, " ").trim()
    return value.length > 140 ? value.substring(0, 137) + "…" : value
  }

  function flash(message) {
    actionStatus = message
    actionStatusTimer.restart()
  }

  function projectTmuxName(project) {
    var name = String((project && project.name) || "")
    if (name === "") {
      var path = String((project && project.path) || "").replace(/\/+$/, "")
      var parts = path.split("/")
      name = parts.length > 0 ? parts[parts.length - 1] : "project"
    }
    name = name.replace(/[:.]/g, "-").replace(/[^A-Za-z0-9_-]/g, "-")
    return name === "" ? "project" : name
  }

  function openProjectTmux(project, editor) {
    if (!project || !project.path) return
    var script = editor
      ? "session=Work; dir=$1; name=$2; cmd='omarchy-launch-editor --inline .'; "
        + "if tmux has-session -t \"$session\" 2>/dev/null; then "
        + "tmux new-window -t \"$session:\" -c \"$dir\" -n \"$name\" \"$cmd\"; "
        + "exec tmux attach-session -t \"$session\"; "
        + "else exec tmux new-session -s \"$session\" -c \"$dir\" -n \"$name\" \"$cmd\"; fi"
      : "session=Work; dir=$1; name=$2; "
        + "if tmux has-session -t \"$session\" 2>/dev/null; then "
        + "tmux new-window -t \"$session:\" -c \"$dir\" -n \"$name\"; "
        + "exec tmux attach-session -t \"$session\"; "
        + "else exec tmux new-session -s \"$session\" -c \"$dir\" -n \"$name\"; fi"
    Quickshell.execDetached([
      "setsid", "uwsm-app", "--", "xdg-terminal-exec",
      "--dir=" + String(project.path),
      "--", "bash", "-lc", script, "omabench", String(project.path), projectTmuxName(project)
    ])
    flash(editor ? "Opened editor in tmux" : "Opened tmux")
  }

  function openTerminal(project) {
    openProjectTmux(project, false)
  }

  function openEditor(project) {
    openProjectTmux(project, true)
  }

  function openFolder(project) {
    if (!project || !project.path) return
    Util.execDetached("setsid uwsm-app -- nautilus --new-window " + Util.shellQuote(project.path))
    flash("Opened folder")
  }

  function openTrustStore() {
    if (ensureProcess.running || helperPath === "") return
    ensureProcess.command = [
      "python3", helperPath, "ensure",
      "--home", home,
      "--trust-store", trustStorePath
    ]
    ensureProcess.running = true
  }

  function openUrl(project) {
    var url = Model.projectUrl(project)
    if (!url) {
      flash("No URL")
      return
    }
    Qt.openUrlExternally(url)
    flash(Model.isGitHubUrl(url) ? "Opened GitHub" : "Opened site")
  }

  function executeOmafileArgv(project, argv, label) {
    if (!project || !project.path || !argv || argv.length === 0) return
    var command = ["setsid", "uwsm-app", "--", "xdg-terminal-exec", "--dir=" + String(project.path), "--"]
    for (var i = 0; i < argv.length; i++) command.push(String(argv[i]))
    Quickshell.execDetached(command)
    flash("Running " + (label || argv.join(" ")))
  }

  function markActionTrusted(path, actionId) {
    var next = []
    for (var i = 0; i < projects.length; i++) {
      var project = projects[i] || {}
      if (String(project.path || "") !== String(path || "") || !Array.isArray(project.actions)) {
        next.push(project)
        continue
      }
      var copy = Object.assign({}, project)
      var actions = []
      for (var j = 0; j < project.actions.length; j++) {
        var action = Object.assign({}, project.actions[j] || {})
        if (String(action.id || "") === String(actionId || "")) action.trusted = true
        actions.push(action)
      }
      copy.actions = actions
      next.push(copy)
    }
    projects = next
  }

  function requestOmafileCommand(project, action) {
    if (!project || !project.path || !action) return
    var argv = Array.isArray(action.argv) ? action.argv : []
    if (argv.length === 0) {
      flash("Command is not runnable")
      return
    }
    if (action.trusted === true) {
      executeOmafileArgv(project, argv, action.label)
      return
    }
    pendingOmafile = { project: project, action: action }
  }

  function cancelPendingOmafile() {
    if (grantingTrust) return
    pendingOmafile = null
  }

  function confirmPendingOmafile() {
    if (!pendingOmafile || grantingTrust || trustProcess.running || helperPath === "") return
    var action = pendingOmafile.action || {}
    var project = pendingOmafile.project || {}
    grantingTrust = true
    trustProcess.command = [
      "python3", helperPath, "grant",
      "--home", home,
      "--trust-store", trustStorePath,
      "--repo", String(project.path || ""),
      "--argv-json", JSON.stringify(action.argv || []),
      "--label", String(action.label || "")
    ]
    trustProcess.running = true
  }

  function copyPath(project) {
    if (!project || !project.path) return
    Util.execDetached("printf %s " + Util.shellQuote(project.path) + " | wl-copy")
    flash("Copied path")
  }

  function runAction(actionId, project) {
    if (actionId === "terminal") openTerminal(project)
    else if (actionId === "editor") openEditor(project)
    else if (actionId === "folder") openFolder(project)
    else if (actionId === "url" || actionId === "github") openUrl(project)
    else if (actionId === "copy") copyPath(project)
    else if (String(actionId).indexOf("omafile:") === 0) {
      var extras = project && Array.isArray(project.actions) ? project.actions : []
      for (var i = 0; i < extras.length; i++) {
        if (String(extras[i].id || "") === String(actionId)) {
          requestOmafileCommand(project, extras[i])
          return
        }
      }
    }
  }

  Timer {
    id: refreshTimer
    interval: root.refreshIntervalSec * 1000
    repeat: true
    running: true
    triggeredOnStart: true
    onTriggered: root.refresh()
  }

  Timer {
    id: actionStatusTimer
    interval: 2200
    repeat: false
    onTriggered: root.actionStatus = ""
  }

  Process {
    id: scanProcess
    running: false
    command: []
    stdout: StdioCollector { id: scanStdout; waitForEnd: true; onStreamFinished: root._scanOutput = text }
    stderr: StdioCollector { id: scanStderr; waitForEnd: true; onStreamFinished: root._scanError = text }
    onExited: function(exitCode) {
      root.refreshing = false
      var stdout = String(scanStdout.text || root._scanOutput || "")
      var stderr = String(scanStderr.text || root._scanError || "")
      if (exitCode === 0) root.applyScan(stdout)
      else root.lastError = root.elideStatus(stderr || stdout || "Could not scan work folder")
      if (root._refreshQueued) root.refresh()
    }
  }

  Process {
    id: ensureProcess
    running: false
    command: []
    stdout: StdioCollector { id: ensureStdout; waitForEnd: true }
    stderr: StdioCollector { id: ensureStderr; waitForEnd: true }
    onExited: function(exitCode) {
      var stdout = String(ensureStdout.text || "")
      var stderr = String(ensureStderr.text || "")
      var payload = {}
      try { payload = JSON.parse(stdout) } catch (e) { payload = {} }
      if (exitCode !== 0 || payload.ok !== true) {
        root.flash(root.elideStatus(payload.error || stderr || "Could not open trust store"))
        return
      }
      var path = String(payload.path || root.trustStorePath)
      Quickshell.execDetached(["omarchy-launch-editor", path])
      root.flash("Opened trust store")
    }
  }

  Process {
    id: rootProcess
    running: false
    command: []
    stdout: StdioCollector { id: rootStdout; waitForEnd: true; onStreamFinished: root._rootOutput = text }
    stderr: StdioCollector { id: rootStderr; waitForEnd: true; onStreamFinished: root._rootError = text }
    onExited: function(exitCode) {
      var stdout = String(rootStdout.text || root._rootOutput || "")
      var stderr = String(rootStderr.text || root._rootError || "")
      var payload = {}
      try { payload = JSON.parse(stdout) } catch (e) { payload = {} }
      if (Array.isArray(payload.roots)) roots = payload.roots.map(Model.normalizeRoot).filter(function(item) {
        return item !== null
      })
      if (exitCode !== 0 || payload.ok !== true) {
        root.flash(root.elideStatus(payload.error || stderr || "Could not update directories"))
        return
      }
      root.flash("Updated directories")
      root.refresh()
    }
  }

  Process {
    id: trustProcess
    running: false
    command: []
    stdout: StdioCollector { id: trustStdout; waitForEnd: true }
    stderr: StdioCollector { id: trustStderr; waitForEnd: true }
    onExited: function(exitCode) {
      var pending = root.pendingOmafile
      root.grantingTrust = false
      var stdout = String(trustStdout.text || "")
      var stderr = String(trustStderr.text || "")
      var payload = {}
      try { payload = JSON.parse(stdout) } catch (e) { payload = {} }
      if (exitCode !== 0 || payload.ok !== true) {
        root.pendingOmafile = null
        root.flash(root.elideStatus(payload.error || stderr || "Could not save trust"))
        return
      }
      if (!pending || !pending.project || !pending.action) {
        root.pendingOmafile = null
        return
      }
      root.markActionTrusted(pending.project.path, pending.action.id)
      root.executeOmafileArgv(pending.project, pending.action.argv, pending.action.label)
      root.pendingOmafile = null
    }
  }
}
