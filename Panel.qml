import QtQuick
import QtQuick.Controls
import Quickshell
import Quickshell.Io
import qs.Commons
import qs.Ui
import "Model.js" as Model

Panel {
  id: root
  moduleName: "modoterra.omabench"
  ipcTarget: "modoterra.omabench"
  manageIpc: false

  property bool cursorActive: false
  property string selectedPath: ""
  property string focusSection: "projects"
  property int actionIndex: 0

  readonly property color foreground: bar ? bar.foreground : Color.foreground
  readonly property color urgent: bar ? bar.urgent : Color.urgent
  readonly property color dim: Qt.darker(foreground, 1.55)
  readonly property color accent: Color.accent
  readonly property string fontFamily: bar ? bar.fontFamily : Style.font.family
  readonly property var projects: workspace.projects
  readonly property int projectIndex: {
    var index = Model.projectIndexByPath(projects, selectedPath)
    if (index >= 0) return index
    return projects.length > 0 ? 0 : -1
  }
  readonly property var selectedProject: projectIndex >= 0 ? projects[projectIndex] : null
  readonly property var actions: Model.actionList(selectedProject)
  readonly property string mark: "󰚝"
  readonly property color markColor: {
    if (projects.length === 0) return foreground
    var hsl = Model.dirtyMarkHsl(Model.dirtyRatio(projects), urgent.r, urgent.g, urgent.b)
    return Qt.hsla(hsl.h, hsl.s, hsl.l, 1)
  }

  function ensureSelection() {
    if (!selectedProject) {
      selectedPath = projects.length > 0 ? String(projects[0].path || "") : ""
      return
    }
    selectedPath = String(selectedProject.path || "")
    if (actionIndex < 0) actionIndex = 0
    if (actionIndex >= actions.length) actionIndex = Math.max(0, actions.length - 1)
  }

  function selectProject(index) {
    if (projects.length === 0) {
      selectedPath = ""
      return
    }
    var next = Math.max(0, Math.min(projects.length - 1, index))
    selectedPath = String(projects[next].path || "")
    if (focusSection === "actions") actionIndex = 0
    scrollCursorIntoView()
  }

  function setProjectCursor(index) {
    cursorActive = true
    focusSection = "projects"
    selectProject(index)
  }

  function moveCursor(dx, dy) {
    cursorActive = true
    ensureSelection()
    if (projects.length === 0) return

    if (dy !== 0) {
      selectProject(projectIndex + dy)
      return
    }
    if (dx === 0) return

    if (focusSection !== "actions") {
      focusSection = "actions"
      actionIndex = dx > 0 ? 0 : actions.length - 1
      return
    }
    actionIndex = Math.max(0, Math.min(actions.length - 1, actionIndex + dx))
  }

  function activateCursor() {
    ensureSelection()
    if (!selectedProject) return
    if (focusSection === "actions") workspace.runAction(actions[actionIndex].id, selectedProject)
    else workspace.openTerminal(selectedProject)
  }

  function runSelectedAction(actionId) {
    cursorActive = true
    ensureSelection()
    if (!selectedProject) return
    workspace.runAction(actionId, selectedProject)
  }

  function runActionAt(index) {
    if (index < 0 || index >= actions.length) return
    focusSection = "actions"
    actionIndex = index
    runSelectedAction(actions[index].id)
  }

  function segmentColor(kind) {
    if (kind === "dirty") return accent
    if (kind === "port") return foreground
    if (kind === "ahead") return foreground
    return dim
  }

  function scrollItemIntoView(item) {
    if (!panelFlick || !item) return
    Qt.callLater(function() {
      if (!item) return
      var margin = Style.space(6)
      var point = item.mapToItem(panelFlick.contentItem, 0, 0)
      var top = point.y
      var bottom = top + item.height
      var viewTop = panelFlick.contentY
      var viewBottom = viewTop + panelFlick.height
      var maxY = Math.max(0, panelFlick.contentHeight - panelFlick.height)
      if (top < viewTop + margin) panelFlick.contentY = Math.max(0, top - margin)
      else if (bottom > viewBottom - margin) panelFlick.contentY = Math.min(maxY, bottom + margin - panelFlick.height)
    })
  }

  function scrollCursorIntoView() {
    if (projectRepeater && projectIndex >= 0)
      scrollItemIntoView(projectRepeater.itemAt(projectIndex))
  }

  implicitWidth: button.implicitWidth
  implicitHeight: button.implicitHeight

  onOpenedChanged: if (opened) {
    cursorActive = true
    focusSection = "projects"
    ensureSelection()
    if (panelFlick) panelFlick.contentY = 0
    workspace.refresh()
    Qt.callLater(function() { keyCatcher.forceActiveFocus() })
  }
  onProjectIndexChanged: scrollCursorIntoView()

  Service {
    id: workspace
    settings: root.settings
    opened: root.opened
  }

  IpcHandler {
    target: root.ipcTarget
    function open(): void { root.open() }
    function close(): void { root.close() }
    function show(): void { root.open() }
    function hide(): void { root.close() }
    function toggle(): void { root.toggle() }
    function refresh(): string { workspace.refresh(); return "ok" }
  }

  BarIconButton {
    id: button
    anchors.fill: parent
    bar: root.bar
    text: root.mark
    active: projects.length > 0
    useActiveColor: true
    activeColor: root.markColor
    tooltipText: Model.barTooltip(workspace.scanPayload, workspace.refreshing)
    onPressed: function(buttonCode) {
      if (buttonCode === Qt.RightButton) workspace.refresh()
      else root.toggle()
    }
  }

  KeyboardPanel {
    id: panel
    anchorItem: button
    owner: root
    bar: root.bar
    open: root.opened
    focusTarget: keyCatcher
    contentWidth: panel.fittedContentWidth(Style.space(440))
    contentHeight: panel.fittedContentHeight(column.implicitHeight, Style.space(640))

    PanelKeyCatcher {
      id: keyCatcher
      anchors.fill: parent
      onMoveRequested: function(dx, dy) {
        root.moveCursor(dx, dy)
      }
      onActivateRequested: root.activateCursor()
      onCloseRequested: root.close()
      onTabRequested: function(direction) { root.switchPanel(direction) }
      onTextKey: function(t) {
        if (t === "r" || t === "R") workspace.refresh()
        else if (t === "t" || t === "T" || t === "1") root.runActionAt(0)
        else if (t === "e" || t === "E" || t === "2") root.runActionAt(1)
        else if (t === "f" || t === "F" || t === "3") root.runActionAt(2)
        else if (t === "g" || t === "G" || t === "4") root.runActionAt(3)
        else if (t === "y" || t === "Y" || t === "c" || t === "C" || t === "5") root.runActionAt(4)
      }

      Column {
        id: column
        width: parent.width
        spacing: Style.space(12)

        PanelHero {
          id: hero
          width: parent.width
          title: "Omabench"
          meta: Model.heroMeta(workspace.scanPayload)
          foreground: root.foreground
          fontFamily: root.fontFamily
          iconComponent: Component {
            Text {
              text: root.mark
              color: root.markColor
              font.family: root.fontFamily
              font.pixelSize: Style.font.display
            }
          }
          trailingControl: Component {
            Button {
              iconText: "󰑐"
              tooltipText: "Refresh"
              iconSpinning: workspace.refreshing
              foreground: hero.foreground
              fontFamily: hero.fontFamily
              iconSize: Style.font.subtitle * 1.5
              horizontalPadding: Style.space(5)
              verticalPadding: Style.space(2)
              onClicked: workspace.refresh()
            }
          }
        }

        Text {
          visible: workspace.actionStatus !== "" || workspace.lastError !== ""
          width: parent.width
          text: workspace.actionStatus !== "" ? workspace.actionStatus : workspace.lastError
          color: workspace.lastError !== "" && workspace.actionStatus === "" ? root.urgent : root.dim
          font.family: root.fontFamily
          font.pixelSize: Style.font.bodySmall
          wrapMode: Text.WordWrap
        }

        PanelSeparator {
          foreground: root.foreground
        }

        PanelSectionHeader {
          text: "PROJECTS"
          foreground: root.foreground
          fontFamily: root.fontFamily
        }

        Text {
          visible: projects.length === 0 && workspace.lastError === ""
          width: parent.width
          text: workspace.rootExists
            ? "No git projects in " + workspace.displayRoot + "."
            : "Work folder not found: " + workspace.displayRoot
          color: root.dim
          font.family: root.fontFamily
          font.pixelSize: Style.font.body
          wrapMode: Text.WordWrap
        }

        Flickable {
          id: panelFlick
          visible: projects.length > 0
          width: parent.width
          height: visible ? Math.min(projectColumn.implicitHeight, Style.space(320)) : 0
          contentWidth: width
          contentHeight: projectColumn.implicitHeight
          clip: true
          boundsBehavior: Flickable.StopAtBounds
          flickableDirection: Flickable.VerticalFlick
          interactive: contentHeight > height
          ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }

          Column {
            id: projectColumn
            width: panelFlick.width
            spacing: Style.space(4)

            Repeater {
              id: projectRepeater
              model: projects
              ProjectRow {
                required property var modelData
                required property int index
                width: projectColumn.width
                project: modelData
                rowIndex: index
              }
            }
          }
        }

        PanelSeparator {
          visible: projects.length > 0
          foreground: root.foreground
        }

        Column {
          visible: projects.length > 0
          width: parent.width
          spacing: Style.space(10)

          PanelSectionHeader {
            text: selectedProject ? String(selectedProject.name || "PROJECT") : "PROJECT"
            foreground: root.foreground
            fontFamily: root.fontFamily
          }

          Row {
            id: actionRow
            width: parent.width
            spacing: Style.space(6)
            readonly property int count: Math.max(1, root.actions.length)
            readonly property real cellWidth: (width - spacing * (count - 1)) / count

            Repeater {
              model: root.actions
              ActionPill {
                required property var modelData
                required property int index
                width: actionRow.cellWidth
                action: modelData
                actionIndex: index
              }
            }
          }
        }
      }
    }
  }

  component ProjectRow: CursorSurface {
    id: projectRow
    property var project: null
    property int rowIndex: 0
    readonly property bool selected: root.cursorActive && root.projectIndex === rowIndex
    readonly property var segments: Model.statusSegments(projectRow.project)

    hasCursor: selected
    foreground: root.foreground
    borderSpec: Border.none()

    implicitHeight: rowBody.implicitHeight + Style.spacing.rowPaddingX

    MouseArea {
      anchors.fill: parent
      hoverEnabled: true
      cursorShape: Qt.PointingHandCursor
      onClicked: root.setProjectCursor(projectRow.rowIndex)
      onDoubleClicked: {
        root.setProjectCursor(projectRow.rowIndex)
        workspace.openTerminal(projectRow.project)
      }
    }

    Column {
      id: rowBody
      anchors.left: parent.left
      anchors.right: parent.right
      anchors.verticalCenter: parent.verticalCenter
      anchors.leftMargin: Style.space(10)
      anchors.rightMargin: Style.space(10)
      spacing: Style.space(2)

      Text {
        width: parent.width
        text: projectRow.project ? String(projectRow.project.name || "") : ""
        color: root.foreground
        font.family: root.fontFamily
        font.pixelSize: Style.font.title
        font.bold: true
        elide: Text.ElideRight
      }

      Text {
        width: parent.width
        text: projectRow.project ? String(projectRow.project.displayPath || "") : ""
        color: root.dim
        font.family: root.fontFamily
        font.pixelSize: Style.font.caption
        elide: Text.ElideMiddle
      }

      Text {
        visible: !!(projectRow.project && projectRow.project.summary)
        width: parent.width
        text: projectRow.project ? String(projectRow.project.summary || "") : ""
        color: root.dim
        font.family: root.fontFamily
        font.pixelSize: Style.font.caption
        elide: Text.ElideRight
      }

      Row {
        width: parent.width
        spacing: Style.space(10)

        Text {
          width: Math.max(0, parent.width - statusRow.implicitWidth - parent.spacing)
          text: projectRow.project ? String(projectRow.project.branch || "") : ""
          color: root.foreground
          font.family: root.fontFamily
          font.pixelSize: Style.font.bodySmall
          elide: Text.ElideRight
        }

        Row {
          id: statusRow
          spacing: Style.space(8)

          Repeater {
            model: projectRow.segments
            Text {
              required property var modelData
              text: modelData.text
              color: root.segmentColor(modelData.kind)
              font.family: root.fontFamily
              font.pixelSize: Style.font.bodySmall
            }
          }
        }
      }

      Text {
        visible: !!(projectRow.project && projectRow.project.omafileError)
        width: parent.width
        text: projectRow.project ? String(projectRow.project.omafileError || "") : ""
        color: root.urgent
        font.family: root.fontFamily
        font.pixelSize: Style.font.bodySmall
        wrapMode: Text.WordWrap
      }
    }
  }

  component ActionPill: Button {
    id: pill
    property var action: null
    property int actionIndex: 0

    text: action ? String(action.label || "") : ""
    tooltipText: action ? String(action.tooltip || action.label || "") : ""
    fontSize: Style.font.caption
    foreground: root.foreground
    fontFamily: root.fontFamily
    horizontalPadding: Style.spacing.sm
    verticalPadding: Style.spacing.controlPaddingY
    bordered: true
    enabled: !action || action.enabled !== false
    hasCursor: root.cursorActive && root.focusSection === "actions" && root.actionIndex === actionIndex

    onClicked: if (pill.action) root.runSelectedAction(pill.action.id)
    onHovered: function(on) {
      if (!on) return
      root.cursorActive = true
      root.focusSection = "actions"
      root.actionIndex = pill.actionIndex
    }
  }
}
