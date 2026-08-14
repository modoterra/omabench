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
  property string workRoot: ""
  property string displayRoot: "~/Work"
  property bool rootExists: true
  property bool refreshing: false
  property string lastError: ""
  property string actionStatus: ""

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
  readonly property string helperPath: Model.filePathFromUrl(Qt.resolvedUrl("scan.py"))
  readonly property string home: Quickshell.env("HOME") || ""
  readonly property var scanPayload: ({
    ok: lastError === "",
    root: workRoot,
    displayRoot: displayRoot,
    rootExists: rootExists,
    projects: projects,
    error: lastError
  })

  property string _scanOutput: ""
  property string _scanError: ""

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
    if (scanProcess.running || helperPath === "") return
    _scanOutput = ""
    _scanError = ""
    refreshing = true
    scanProcess.command = [
      "python3", helperPath,
      "--root", workRootSetting,
      "--home", home,
      "--max-depth", String(maxDepth)
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
    projects = parsed.projects
    lastError = parsed.error
  }

  function elideStatus(text) {
    var value = String(text || "").replace(/\s+/g, " ").trim()
    return value.length > 140 ? value.substring(0, 137) + "…" : value
  }

  function flash(message) {
    actionStatus = message
    actionStatusTimer.restart()
  }

  function openTerminal(project) {
    if (!project || !project.path) return
    Util.execDetached("setsid uwsm-app -- xdg-terminal-exec --dir=" + Util.shellQuote(project.path))
    flash("Opened terminal")
  }

  function openEditor(project) {
    if (!project || !project.path) return
    Util.execDetached("omarchy-launch-editor " + Util.shellQuote(project.path))
    flash("Opened editor")
  }

  function openFolder(project) {
    if (!project || !project.path) return
    Util.execDetached("setsid uwsm-app -- nautilus --new-window " + Util.shellQuote(project.path))
    flash("Opened folder")
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

  function runOmafileCommand(project, command, label) {
    if (!project || !project.path || !command) return
    Util.execDetached(
      "setsid uwsm-app -- xdg-terminal-exec --dir=" + Util.shellQuote(project.path)
        + " -- bash -lc " + Util.shellQuote(command)
    )
    flash("Running " + (label || command))
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
          runOmafileCommand(project, extras[i].command, extras[i].label)
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
    }
  }
}
