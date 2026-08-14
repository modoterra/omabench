#!/usr/bin/env node

const fs = require("fs")
const path = require("path")
const vm = require("vm")
const assert = require("assert")

const source = fs.readFileSync(path.join(__dirname, "..", "Model.js"), "utf8")
const model = {}
vm.createContext(model)
vm.runInContext(source, model)

assert.deepStrictEqual(model.parseScan(""), model.emptyScan())
assert.strictEqual(model.parseScan("{").error, "Failed to read project state")

const parsed = model.parseScan(JSON.stringify({
  ok: true,
  root: "/home/ada/Work",
  displayRoot: "~/Work",
  rootExists: true,
  projects: [
    { path: "/home/ada/Work/echo", dirty: true, ports: [5173] },
    { path: "/home/ada/Work/clean", dirty: false, ports: [] }
  ]
}))
assert.strictEqual(model.dirtyCount(parsed.projects), 1)
assert.strictEqual(model.listeningCount(parsed.projects), 1)
assert.strictEqual(model.heroMeta(parsed), "2 projects · 1 dirty · 1 listening")
assert.strictEqual(model.barTooltip(parsed, false), "1 dirty · 1 listening")

const segments = model.statusSegments({
  dirty: true,
  changed: 3,
  ahead: 2,
  behind: 0,
  ports: [5173, 8000]
})
assert.strictEqual(
  JSON.stringify(Array.from(segments, (item) => String(item.text))),
  JSON.stringify(["● 3 changed", "↑2", ":5173", ":8000"])
)

assert.strictEqual(
  model.filePathFromUrl("file:///home/ada/Work/modoterra/omabench/scan.py"),
  "/home/ada/Work/modoterra/omabench/scan.py"
)

const actions = model.actionList({ githubUrl: "" })
assert.strictEqual(actions[3].id, "github")
assert.strictEqual(actions[3].enabled, false)

console.log("ok")
