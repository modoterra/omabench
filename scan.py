#!/usr/bin/env python3
"""Discover git projects under a work root and print their live state as JSON."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tomllib
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

OMAFILE_NAME = "Omafile"

SKIP_DIR_NAMES = frozenset(
    {
        ".cache",
        ".cargo",
        ".direnv",
        ".git",
        ".venv",
        "__pycache__",
        "Pods",
        "build",
        "dist",
        "node_modules",
        "target",
        "vendor",
        "venv",
    }
)

GIT_ENV = {
    "GIT_OPTIONAL_LOCKS": "0",
    "GIT_TERMINAL_PROMPT": "0",
}

GITHUB_SSH = re.compile(r"^git@github\.com:(?P<owner>[^/]+)/(?P<repo>.+)$")
GITHUB_URL = re.compile(
    r"^(?:https?|ssh|git)://(?:[^@/]+@)?github\.com[:/](?P<owner>[^/]+)/(?P<repo>.+)$"
)


def display_path(path: Path, home: Path) -> str:
    resolved = path
    home_resolved = home
    try:
        resolved = path.resolve()
        home_resolved = home.resolve()
    except OSError:
        pass
    try:
        return "~/" + resolved.relative_to(home_resolved).as_posix()
    except ValueError:
        return str(resolved)


def empty_omafile() -> dict:
    return {
        "present": False,
        "name": "",
        "summary": "",
        "url": "",
        "actions": [],
        "error": "",
    }


def _optional_string(data: dict, key: str, errors: list[str]) -> str:
    if key not in data:
        return ""
    value = data[key]
    if not isinstance(value, str):
        errors.append(f"{key} must be a string")
        return ""
    return value.strip()


def _parse_actions(data: dict, errors: list[str]) -> list[dict]:
    if "actions" not in data:
        return []
    raw_actions = data["actions"]
    if not isinstance(raw_actions, list):
        errors.append("actions must be an array")
        return []
    actions: list[dict] = []
    for index, item in enumerate(raw_actions):
        prefix = f"actions[{index}]"
        if not isinstance(item, dict):
            errors.append(f"{prefix} must be a table")
            continue
        command = item.get("command")
        if not isinstance(command, str) or command.strip() == "":
            errors.append(f"{prefix}.command must be a string")
            continue
        label = item.get("label", command)
        if not isinstance(label, str) or label.strip() == "":
            errors.append(f"{prefix}.label must be a string")
            continue
        actions.append(
            {
                "id": f"omafile:{index}",
                "label": label.strip(),
                "command": command.strip(),
            }
        )
    return actions


def parse_omafile(text: str) -> dict:
    result = empty_omafile()
    if str(text or "").strip() == "":
        return result

    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        result["error"] = f"Invalid Omafile: {exc}"
        return result

    if not isinstance(data, dict):
        result["error"] = "Omafile must be a table"
        return result

    errors: list[str] = []
    name = _optional_string(data, "name", errors)
    summary = _optional_string(data, "summary", errors)
    url = _optional_string(data, "url", errors)
    if url and not (url.startswith("http://") or url.startswith("https://")):
        errors.append("url must start with http:// or https://")
    actions = _parse_actions(data, errors)
    if errors:
        result["error"] = "; ".join(errors)
        return result

    result["name"] = name
    result["summary"] = summary
    result["url"] = url
    result["actions"] = actions
    return result


def load_omafile(repo: Path) -> dict:
    path = repo / OMAFILE_NAME
    if not path.is_file():
        return empty_omafile()

    result = empty_omafile()
    result["present"] = True
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        result["error"] = "Could not read Omafile"
        return result

    parsed = parse_omafile(text)
    parsed["present"] = True
    return parsed


def github_url(remote: str) -> str:
    value = str(remote or "").strip().rstrip("/")
    if value.endswith(".git"):
        value = value[:-4].rstrip("/")
    match = GITHUB_SSH.match(value) or GITHUB_URL.match(value)
    if not match:
        return ""
    return f"https://github.com/{match.group('owner')}/{match.group('repo')}"


def is_git_work_tree(path: Path) -> bool:
    git_path = path / ".git"
    try:
        return git_path.is_dir() or git_path.is_file()
    except OSError:
        return False


def discover_repos(root: Path, max_depth: int = 6) -> list[Path]:
    try:
        root = root.resolve()
    except OSError:
        return []
    if not root.is_dir():
        return []

    found: list[Path] = []

    def walk(current: Path, depth: int) -> None:
        if is_git_work_tree(current):
            found.append(current)
            return
        if depth >= max_depth:
            return
        try:
            entries = sorted(current.iterdir(), key=lambda entry: entry.name.lower())
        except OSError:
            return
        for entry in entries:
            try:
                if not entry.is_dir(follow_symlinks=False):
                    continue
            except OSError:
                continue
            if entry.name in SKIP_DIR_NAMES:
                continue
            if entry.name.startswith("."):
                continue
            walk(entry, depth + 1)

    walk(root, 0)
    return found


def _git_env() -> dict[str, str]:
    env = os.environ.copy()
    env.update(GIT_ENV)
    return env


def run_git(repo: Path, args: list[str], timeout: float = 5.0) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        timeout=timeout,
        env=_git_env(),
        check=False,
    )


def _git_text(repo: Path, args: list[str]) -> str:
    try:
        result = run_git(repo, args)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    if result.returncode != 0:
        return ""
    return (result.stdout or "").strip()


def git_state(repo: Path, home: Path | None = None) -> dict:
    resolved = repo
    try:
        resolved = repo.resolve()
    except OSError:
        pass

    branch = _git_text(resolved, ["branch", "--show-current"])
    if not branch:
        short = _git_text(resolved, ["rev-parse", "--short", "HEAD"])
        branch = f"detached {short}" if short else "HEAD"

    porcelain = _git_text(resolved, ["status", "--porcelain=v1"])
    changed_lines = [line for line in porcelain.splitlines() if line.strip()]
    changed = len(changed_lines)

    ahead = 0
    behind = 0
    has_upstream = False
    upstream = _git_text(resolved, ["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}"])
    if upstream:
        counts = _git_text(resolved, ["rev-list", "--left-right", "--count", "@{upstream}...HEAD"])
        parts = counts.split()
        if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
            has_upstream = True
            behind = int(parts[0])
            ahead = int(parts[1])

    remote = _git_text(resolved, ["remote", "get-url", "origin"])
    home_path = home if home is not None else Path.home()
    remote_url = github_url(remote)
    omafile = load_omafile(resolved)
    name = omafile["name"] or resolved.name
    if omafile["error"]:
        name = resolved.name
        summary = ""
        url = remote_url
        actions: list[dict] = []
    else:
        summary = omafile["summary"]
        url = omafile["url"] or remote_url
        actions = omafile["actions"]

    return {
        "name": name,
        "path": str(resolved),
        "displayPath": display_path(resolved, home_path),
        "branch": branch,
        "dirty": changed > 0,
        "changed": changed,
        "ahead": ahead,
        "behind": behind,
        "hasUpstream": has_upstream,
        "ports": [],
        "githubUrl": remote_url,
        "url": url,
        "summary": summary,
        "actions": actions,
        "omafileError": omafile["error"],
    }


def parse_tcp_table(text: str, ipv6: bool = False) -> dict[str, int]:
    inodes: dict[str, int] = {}
    for raw_line in str(text or "").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("sl"):
            continue
        parts = line.split()
        if len(parts) < 10:
            continue
        local = parts[1]
        state = parts[3]
        inode = parts[9]
        if state != "0A" or not inode or inode == "0":
            continue
        if ":" not in local:
            continue
        address_hex, port_hex = local.rsplit(":", 1)
        try:
            port = int(port_hex, 16)
        except ValueError:
            continue
        if port <= 0 or port > 65535:
            continue
        if ipv6:
            if len(address_hex) != 32:
                continue
        elif len(address_hex) != 8:
            continue
        inodes[inode] = port
    return inodes


def _read_text(path: Path) -> str:
    try:
        return path.read_text()
    except OSError:
        return ""


def listen_inodes(proc_root: Path) -> dict[str, int]:
    inodes = parse_tcp_table(_read_text(proc_root / "net" / "tcp"), ipv6=False)
    inodes.update(parse_tcp_table(_read_text(proc_root / "net" / "tcp6"), ipv6=True))
    return inodes


def _socket_inode(target: str) -> str:
    match = re.fullmatch(r"socket:\[(\d+)\]", target)
    return match.group(1) if match else ""


def pid_listen_ports(proc_root: Path, inode_ports: dict[str, int]) -> dict[int, set[int]]:
    ports_by_pid: dict[int, set[int]] = {}
    try:
        pid_dirs = list(proc_root.iterdir())
    except OSError:
        return ports_by_pid

    for pid_dir in pid_dirs:
        if not pid_dir.name.isdigit():
            continue
        fd_dir = pid_dir / "fd"
        try:
            for fd in fd_dir.iterdir():
                try:
                    target = os.readlink(fd)
                except OSError:
                    continue
                inode = _socket_inode(target)
                if inode and inode in inode_ports:
                    ports_by_pid.setdefault(int(pid_dir.name), set()).add(inode_ports[inode])
        except OSError:
            continue
    return ports_by_pid


def pid_cwd(proc_root: Path, pid: int) -> Path | None:
    try:
        return Path(os.readlink(proc_root / str(pid) / "cwd"))
    except OSError:
        return None


def assign_ports(projects: list[dict], proc_root: Path) -> None:
    if not projects:
        return
    inode_ports = listen_inodes(proc_root)
    if not inode_ports:
        for project in projects:
            project["ports"] = []
        return

    roots = []
    for project in projects:
        try:
            roots.append((Path(project["path"]).resolve(), project))
        except OSError:
            roots.append((Path(project["path"]), project))
    roots.sort(key=lambda item: len(str(item[0])), reverse=True)

    collected: dict[str, set[int]] = {project["path"]: set() for project in projects}
    for pid, ports in pid_listen_ports(proc_root, inode_ports).items():
        cwd = pid_cwd(proc_root, pid)
        if cwd is None:
            continue
        try:
            cwd_resolved = cwd.resolve()
        except OSError:
            cwd_resolved = cwd
        for root, project in roots:
            try:
                cwd_resolved.relative_to(root)
            except ValueError:
                continue
            collected[project["path"]].update(ports)
            break

    for project in projects:
        project["ports"] = sorted(collected.get(project["path"], set()))


def status_line(project: dict) -> str:
    parts: list[str] = []
    if project.get("dirty"):
        changed = int(project.get("changed") or 0)
        label = "change" if changed == 1 else "changed"
        parts.append(f"● {changed} {label}")
    else:
        parts.append("✓ clean")

    motion: list[str] = []
    ahead = int(project.get("ahead") or 0)
    behind = int(project.get("behind") or 0)
    if ahead > 0:
        motion.append(f"↑{ahead}")
    if behind > 0:
        motion.append(f"↓{behind}")
    if motion:
        parts.append("  ".join(motion))

    ports = [int(port) for port in (project.get("ports") or []) if int(port) > 0]
    if ports:
        parts.append(" ".join(f":{port}" for port in ports))

    if not project.get("dirty") and not motion and not ports:
        return "✓ clean"
    return "   ".join(parts)


def sort_projects(projects: list[dict]) -> list[dict]:
    def key(project: dict) -> tuple:
        return (
            0 if project.get("dirty") else 1,
            0 if project.get("ports") else 1,
            str(project.get("name") or "").lower(),
            str(project.get("path") or ""),
        )

    return sorted(projects, key=key)


def resolve_root(raw: str, home: Path) -> Path:
    value = str(raw or "").strip() or "~/Work"
    if value == "~":
        return home
    if value.startswith("~/"):
        return home / value[2:]
    return Path(value)


def scan_work(
    root: Path,
    home: Path | None = None,
    proc_root: Path | None = None,
    max_depth: int = 6,
) -> dict:
    home_path = home if home is not None else Path.home()
    try:
        home_path = home_path.resolve()
    except OSError:
        pass

    try:
        resolved_root = root.expanduser()
        if not resolved_root.is_absolute():
            resolved_root = (home_path / resolved_root).resolve()
        else:
            resolved_root = resolved_root.resolve()
    except OSError:
        resolved_root = root

    payload = {
        "ok": True,
        "root": str(resolved_root),
        "displayRoot": display_path(resolved_root, home_path),
        "rootExists": resolved_root.is_dir(),
        "projects": [],
        "error": "",
    }
    if not payload["rootExists"]:
        payload["error"] = f"Work folder not found: {payload['displayRoot']}"
        return payload

    repos = discover_repos(resolved_root, max_depth=max_depth)
    if not repos:
        return payload

    workers = min(8, len(repos))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        projects = list(pool.map(lambda repo: git_state(repo, home=home_path), repos))

    assign_ports(projects, proc_root if proc_root is not None else Path("/proc"))
    payload["projects"] = sort_projects(projects)
    return payload


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Scan a work folder for git project state.")
    parser.add_argument("--root", default="~/Work", help="Work folder to scan (default: ~/Work)")
    parser.add_argument("--home", default=str(Path.home()), help="Home directory used for ~ display")
    parser.add_argument("--max-depth", type=int, default=6, help="Maximum search depth from the work root")
    parser.add_argument("--proc", default="/proc", help="proc filesystem used to map listening ports")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    home = Path(args.home)
    root = resolve_root(args.root, home)
    max_depth = args.max_depth if args.max_depth > 0 else 6
    payload = scan_work(root, home=home, proc_root=Path(args.proc), max_depth=max_depth)
    json.dump(payload, sys.stdout, separators=(",", ":"))
    sys.stdout.write("\n")
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
