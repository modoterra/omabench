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
assert.strictEqual(actions[3].id, "url")
assert.strictEqual(actions[3].enabled, false)
assert.strictEqual(actions[3].label, "Site")
assert.strictEqual(actions[3].tooltip, "Open Site (g · 4)")

const githubActions = model.actionList({ url: "https://github.com/modoterra/echo" })
assert.strictEqual(githubActions[3].enabled, true)
assert.strictEqual(githubActions[3].label, "GitHub")
assert.strictEqual(githubActions[3].tooltip, "Open GitHub (g · 4)")

const withExtras = model.actionList({
  url: "https://echo.dev",
  actions: [{ id: "omafile:0", label: "Dev", command: "bun run dev" }]
})
assert.strictEqual(withExtras[3].label, "Site")
assert.strictEqual(withExtras[5].id, "omafile:0")
assert.strictEqual(withExtras[5].label, "Dev")
assert.strictEqual(withExtras[5].command, "bun run dev")

assert.strictEqual(model.dirtyRatio([]), 0)
assert.strictEqual(model.dirtyRatio(null), 0)
assert.strictEqual(model.dirtyRatio([
  { dirty: true },
  { dirty: false },
  { dirty: false },
  { dirty: false }
]), 0.25)
assert.strictEqual(model.dirtyRatio([{ dirty: true }, { dirty: true }]), 1)

const cleanHsl = model.dirtyMarkHsl(0, 165 / 255, 85 / 255, 85 / 255)
const midHsl = model.dirtyMarkHsl(0.5, 165 / 255, 85 / 255, 85 / 255)
const dirtyHsl = model.dirtyMarkHsl(1, 165 / 255, 85 / 255, 85 / 255)
assert.ok(Math.abs(cleanHsl.h - 120 / 360) < 0.01)
assert.ok(midHsl.h < cleanHsl.h && midHsl.h > dirtyHsl.h)
assert.ok(dirtyHsl.h < 0.05)
assert.ok(Math.abs(cleanHsl.s - dirtyHsl.s) < 0.001)
assert.ok(Math.abs(cleanHsl.l - dirtyHsl.l) < 0.001)

console.log("ok")
