# Omabench

Omabench turns `~/Work` into a desktop workspace. It walks that folder for git repositories and keeps a live row for each one: name, path, branch, dirty or clean, ahead and behind, and listening ports.

Click a project to open a terminal, the editor, the folder, or GitHub, or to copy its path.

## Install

```sh
omarchy plugin add https://github.com/modoterra/omabench.git --enable
```

The widget lands on the right of the bar. Move it with:

```sh
omarchy bar move modoterra.omabench --section right
```

## Usage

Left click the folder icon to open the project list. Right click refreshes immediately. The icon lights up when any project is dirty.

Inside the panel:

- `j` / `k` or arrows: move between projects
- `h` / `l`: move onto the action buttons
- `enter` / `space`: open a terminal, or run the highlighted action
- double click a row: open a terminal in that project
- `t` terminal · `e` editor · `f` folder · `g` GitHub · `y` / `c` copy path
- `r` refresh
- `esc` close

## Configure

The default work folder is `~/Work`. Point it somewhere else in `~/.config/omarchy/shell.json` on the `modoterra.omabench` bar entry:

```json
{
  "id": "modoterra.omabench",
  "workRoot": "~/Work",
  "refreshIntervalSec": 8,
  "maxDepth": 6
}
```

Refresh is every 8 seconds while the panel is closed, and every 4 seconds while it is open.

## Omafile

Drop an `Omafile` in the project root to name the card, add a summary, set the site URL, and attach commands. The file is TOML. Projects without one keep the folder name and built-in actions.

```toml
name = "Omabench"
summary = "Live overview of ~/Work"
url = "https://github.com/modoterra/omabench"

[[actions]]
label = "Dev"
command = "bun run dev"
```

`name` replaces the folder name. `summary` sits under the path. `url` feeds the GitHub or site button. Each `actions` entry becomes a button; the command runs in that project directory in a new terminal.

A broken `Omafile` is shown on that row. Omabench still lists the project.

## Remove

```sh
omarchy plugin remove modoterra.omabench
```
