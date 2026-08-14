#!/usr/bin/env python3
"""Discover git projects under a work root and print their live state as JSON."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shlex
import stat
import subprocess
import sys
import tomllib
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

OMAFILE_NAME = "Omafile"
OMAFILE_MAX_BYTES = 16 * 1024
OMAFILE_MAX_ACTIONS = 8
OMAFILE_MAX_NAME = 64
OMAFILE_MAX_SUMMARY = 140
OMAFILE_MAX_URL = 500
OMAFILE_MAX_LABEL = 32
OMAFILE_MAX_COMMAND = 200
OMAFILE_MAX_ARGV = 12

RESERVED_ACTION_LABELS = frozenset(
    {
        "terminal",
        "editor",
        "folder",
        "github",
        "site",
        "copy",
    }
)

# First token must be one of these. A set value is the allowed second token.
ALLOWED_PROGRAMS: dict[str, frozenset[str] | None] = {
    "bun": frozenset({"run", "test", "start"}),
    "cargo": frozenset({"test", "run", "build", "check", "clippy", "fmt", "doc"}),
    "docker": frozenset({"compose"}),
    "gmake": None,
    "go": frozenset({"test", "run", "build", "vet", "fmt"}),
    "just": None,
    "make": None,
    "mise": frozenset({"run"}),
    "npm": frozenset({"run", "test", "start", "run-script"}),
    "pnpm": frozenset({"run", "test", "start"}),
    "podman": frozenset({"compose"}),
    "poetry": frozenset({"run", "test"}),
    "pytest": None,
    "python": frozenset({"-m"}),
    "python3": frozenset({"-m"}),
    "task": None,
    "uv": frozenset({"run", "test", "sync"}),
    "yarn": frozenset({"run", "test", "start"}),
}

SAFE_ARG_RE = re.compile(
    r"^(?:"
    r"[A-Za-z0-9][A-Za-z0-9._:@+/-]*"
    r"|--?[A-Za-z0-9][A-Za-z0-9_-]*(?:=[A-Za-z0-9._:@+/-]+)?"
    r"|\./[A-Za-z0-9._/@+-]*(?:\.\.\.)?"
    r")$"
)

GIT_SAFE_CONFIG = (
    "-c",
    "core.fsmonitor=",
    "-c",
    "core.useBuiltinFSMonitor=false",
    "-c",
    "core.hooksPath=",
)

TRUST_STORE_VERSION = 1
CONTROL_CHARS_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")

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
        "warning": "",
    }


def empty_trust_store() -> dict:
    return {"version": TRUST_STORE_VERSION, "entries": []}


def sanitize_text(value: str, max_len: int) -> str:
    text = CONTROL_CHARS_RE.sub("", str(value or ""))
    text = text.replace("<", "").replace(">", "")
    text = " ".join(text.split())
    if max_len > 0 and len(text) > max_len:
        text = text[:max_len].rstrip()
    return text


def _optional_string(data: dict, key: str, errors: list[str], max_len: int) -> str:
    if key not in data:
        return ""
    value = data[key]
    if not isinstance(value, str):
        errors.append(f"{key} must be a string")
        return ""
    return sanitize_text(value, max_len)


def _arg_is_safe(token: str) -> bool:
    if not token or not SAFE_ARG_RE.fullmatch(token):
        return False
    return ".." not in token.split("/")


def validate_argv(argv: list[str]) -> str:
    if not argv:
        return "command is empty"
    if len(argv) > OMAFILE_MAX_ARGV:
        return "command has too many arguments"
    program = argv[0]
    if program not in ALLOWED_PROGRAMS:
        return "not an allowlisted command"
    if any(not _arg_is_safe(token) for token in argv):
        return "command contains an unsafe argument"
    allowed_subcommands = ALLOWED_PROGRAMS[program]
    if allowed_subcommands is not None:
        if len(argv) < 2 or argv[1] not in allowed_subcommands:
            return "not an allowlisted command"
    display = " ".join(argv)
    if len(display) > OMAFILE_MAX_COMMAND:
        return "command is too long"
    return ""


def parse_allowlisted_command(command: str) -> tuple[list[str] | None, str]:
    text = str(command or "").strip()
    if text == "":
        return None, "command must be a string"
    if len(text) > OMAFILE_MAX_COMMAND:
        return None, "command is too long"
    try:
        argv = shlex.split(text, posix=True)
    except ValueError:
        return None, "command could not be parsed"
    error = validate_argv(argv)
    if error:
        return None, error
    return argv, ""


def _parse_action_argv(item: dict, prefix: str) -> tuple[list[str] | None, str]:
    if "run" in item:
        raw_run = item["run"]
        if not isinstance(raw_run, list) or not raw_run:
            return None, f"{prefix}.run must be an array of strings"
        argv: list[str] = []
        for part in raw_run:
            if not isinstance(part, str) or part.strip() == "":
                return None, f"{prefix}.run must be an array of strings"
            argv.append(part.strip())
        error = validate_argv(argv)
        if error:
            return None, f"{prefix}.run {error}"
        return argv, ""

    command = item.get("command")
    if not isinstance(command, str) or command.strip() == "":
        return None, f"{prefix}.command must be a string"
    argv, error = parse_allowlisted_command(command)
    if error:
        return None, f"{prefix}.command {error}"
    return argv, ""


def _parse_actions(data: dict, errors: list[str], warnings: list[str]) -> list[dict]:
    if "actions" not in data:
        return []
    raw_actions = data["actions"]
    if not isinstance(raw_actions, list):
        errors.append("actions must be an array")
        return []

    actions: list[dict] = []
    skipped = 0
    for index, item in enumerate(raw_actions):
        prefix = f"actions[{index}]"
        if not isinstance(item, dict):
            warnings.append(f"{prefix} must be a table")
            continue
        argv, error = _parse_action_argv(item, prefix)
        if error:
            warnings.append(error)
            continue
        if argv is None:
            continue
        command = " ".join(argv)
        label_source = item.get("label", command)
        if not isinstance(label_source, str):
            warnings.append(f"{prefix}.label must be a string")
            continue
        label = sanitize_text(label_source, OMAFILE_MAX_LABEL)
        if label == "":
            warnings.append(f"{prefix}.label must be a string")
            continue
        if label.casefold() in RESERVED_ACTION_LABELS:
            warnings.append(f"{prefix}.label is reserved")
            continue
        if len(actions) >= OMAFILE_MAX_ACTIONS:
            skipped += 1
            continue
        actions.append(
            {
                "id": f"omafile:{index}",
                "label": label,
                "command": command,
                "argv": argv,
            }
        )
    if skipped:
        warnings.append(f"too many actions; keeping the first {OMAFILE_MAX_ACTIONS}")
    return actions


def parse_omafile(text: str) -> dict:
    result = empty_omafile()
    raw = str(text or "")
    if raw.strip() == "":
        return result
    if len(raw.encode("utf-8")) > OMAFILE_MAX_BYTES:
        result["error"] = f"Omafile is larger than {OMAFILE_MAX_BYTES} bytes"
        return result

    try:
        data = tomllib.loads(raw)
    except tomllib.TOMLDecodeError as exc:
        result["error"] = f"Invalid Omafile: {exc}"
        return result

    if not isinstance(data, dict):
        result["error"] = "Omafile must be a table"
        return result

    errors: list[str] = []
    warnings: list[str] = []
    name = _optional_string(data, "name", errors, OMAFILE_MAX_NAME)
    summary = _optional_string(data, "summary", errors, OMAFILE_MAX_SUMMARY)
    url = _optional_string(data, "url", errors, OMAFILE_MAX_URL)
    if url and not (url.startswith("http://") or url.startswith("https://")):
        errors.append("url must start with http:// or https://")
    actions = _parse_actions(data, errors, warnings)
    if errors:
        result["error"] = "; ".join(errors)
        return result

    result["name"] = name
    result["summary"] = summary
    result["url"] = url
    result["actions"] = actions
    result["warning"] = "; ".join(warnings)
    return result


def load_omafile(repo: Path) -> dict:
    path = repo / OMAFILE_NAME
    if not path.is_file():
        return empty_omafile()

    result = empty_omafile()
    result["present"] = True
    try:
        with path.open("r", encoding="utf-8") as handle:
            text = handle.read(OMAFILE_MAX_BYTES + 1)
    except OSError:
        result["error"] = "Could not read Omafile"
        return result

    if len(text.encode("utf-8")) > OMAFILE_MAX_BYTES:
        result["error"] = f"Omafile is larger than {OMAFILE_MAX_BYTES} bytes"
        return result

    parsed = parse_omafile(text)
    parsed["present"] = True
    return parsed


def default_trust_store(home: Path) -> Path:
    xdg = str(os.environ.get("XDG_STATE_HOME") or "").strip()
    if xdg:
        return Path(xdg) / "omabench" / "trust.json"
    return home / ".local" / "state" / "omabench" / "trust.json"


def action_digest(path: str, argv: list[str]) -> str:
    payload = json.dumps(
        {"path": path, "argv": argv},
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _is_symlink(path: Path) -> bool:
    try:
        return path.is_symlink()
    except OSError:
        return False


def load_trust_store(store_path: Path | None) -> dict:
    if store_path is None:
        return empty_trust_store()
    if _is_symlink(store_path):
        return empty_trust_store()
    if not store_path.is_file():
        return empty_trust_store()
    try:
        raw = store_path.read_text(encoding="utf-8")
        data = json.loads(raw)
    except (OSError, json.JSONDecodeError, UnicodeError):
        return empty_trust_store()
    if not isinstance(data, dict) or data.get("version") != TRUST_STORE_VERSION:
        return empty_trust_store()
    entries = data.get("entries")
    if not isinstance(entries, list):
        return empty_trust_store()

    cleaned: list[dict] = []
    for item in entries:
        if not isinstance(item, dict):
            continue
        path = item.get("path")
        argv = item.get("argv")
        digest = item.get("digest")
        if not isinstance(path, str) or path == "":
            continue
        if not isinstance(argv, list) or not all(isinstance(part, str) for part in argv):
            continue
        if not isinstance(digest, str) or digest != action_digest(path, argv):
            continue
        cleaned.append(
            {
                "digest": digest,
                "path": path,
                "argv": list(argv),
                "label": sanitize_text(str(item.get("label") or ""), OMAFILE_MAX_LABEL),
                "trustedAt": str(item.get("trustedAt") or ""),
            }
        )
    return {"version": TRUST_STORE_VERSION, "entries": cleaned}


def save_trust_store(store_path: Path, store: dict) -> str:
    if _is_symlink(store_path):
        return "Trust store must not be a symlink"
    parent = store_path.parent
    try:
        parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    except OSError as exc:
        return f"Could not create trust store: {exc}"
    if _is_symlink(parent):
        return "Trust store directory must not be a symlink"

    payload = json.dumps(store, indent=2, ensure_ascii=False) + "\n"
    tmp_path = store_path.with_name(store_path.name + ".tmp")
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        fd = os.open(tmp_path, flags, 0o600)
    except OSError as exc:
        return f"Could not write trust store: {exc}"
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload)
        os.replace(tmp_path, store_path)
        os.chmod(store_path, stat.S_IRUSR | stat.S_IWUSR)
    except OSError as exc:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
        return f"Could not write trust store: {exc}"
    return ""


def is_action_trusted(store: dict, path: str, argv: list[str]) -> bool:
    digest = action_digest(path, argv)
    for entry in store.get("entries") or []:
        if entry.get("digest") == digest:
            return True
    return False


def grant_trust(store_path: Path, repo: Path, argv: list[str], label: str = "") -> dict:
    if _is_symlink(store_path):
        return {"ok": False, "error": "Trust store must not be a symlink", "digest": ""}
    error = validate_argv(argv)
    if error:
        return {"ok": False, "error": error, "digest": ""}
    try:
        resolved = str(repo.resolve())
    except OSError:
        resolved = str(repo)
    store = load_trust_store(store_path)
    digest = action_digest(resolved, argv)
    entries = [entry for entry in store["entries"] if entry.get("digest") != digest]
    entries.append(
        {
            "digest": digest,
            "path": resolved,
            "argv": list(argv),
            "label": sanitize_text(label or " ".join(argv), OMAFILE_MAX_LABEL),
            "trustedAt": datetime.now(timezone.utc).isoformat(),
        }
    )
    store["entries"] = entries
    write_error = save_trust_store(store_path, store)
    if write_error:
        return {"ok": False, "error": write_error, "digest": ""}
    return {"ok": True, "error": "", "digest": digest}


def revoke_trust(store_path: Path, repo: Path, argv: list[str] | None = None) -> dict:
    if _is_symlink(store_path):
        return {"ok": False, "error": "Trust store must not be a symlink"}
    try:
        resolved = str(repo.resolve())
    except OSError:
        resolved = str(repo)
    store = load_trust_store(store_path)
    if argv is None:
        entries = [entry for entry in store["entries"] if entry.get("path") != resolved]
    else:
        digest = action_digest(resolved, argv)
        entries = [entry for entry in store["entries"] if entry.get("digest") != digest]
    store["entries"] = entries
    write_error = save_trust_store(store_path, store)
    if write_error:
        return {"ok": False, "error": write_error}
    return {"ok": True, "error": ""}


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
                if entry.is_symlink() or not entry.is_dir():
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
        ["git", "-C", str(repo), *GIT_SAFE_CONFIG, *args],
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


LS_FILES_DEBUG_MTIME = re.compile(r"mtime:\s*(\d+):")
LS_FILES_DEBUG_SIZE = re.compile(r"size:\s*(\d+)")


def parse_ls_files_debug(text: str) -> list[tuple[str, int, int]]:
    entries: list[tuple[str, int, int]] = []
    name = ""
    meta: list[str] = []

    def flush() -> None:
        if name == "":
            return
        blob = "\n".join(meta)
        mtime = LS_FILES_DEBUG_MTIME.search(blob)
        size = LS_FILES_DEBUG_SIZE.search(blob)
        if mtime and size:
            entries.append((name, int(mtime.group(1)), int(size.group(1))))

    for raw_line in str(text or "").splitlines():
        if raw_line.startswith("  "):
            meta.append(raw_line)
            continue
        flush()
        name = raw_line
        meta = []
    flush()
    return entries


GITLINK_MODE = 0o160000


def parse_ls_files_stage(text: str) -> dict[str, int]:
    modes: dict[str, int] = {}
    for line in str(text or "").splitlines():
        parts = line.split(None, 3)
        if len(parts) < 4:
            continue
        try:
            modes[parts[3]] = int(parts[0], 8)
        except ValueError:
            continue
    return modes


def worktree_stat_changed(repo: Path, debug_text: str, stage_text: str = "") -> set[str]:
    modes = parse_ls_files_stage(stage_text)
    changed: set[str] = set()
    for relpath, mtime_sec, size in parse_ls_files_debug(debug_text):
        if modes.get(relpath, 0) == GITLINK_MODE:
            continue
        path = repo / relpath
        try:
            info = path.lstat()
        except OSError:
            changed.add(relpath)
            continue
        if stat.S_ISDIR(info.st_mode):
            continue
        if int(info.st_mtime) != mtime_sec or int(info.st_size) != size:
            changed.add(relpath)
    return changed


def git_changed_count(repo: Path) -> int:
    # Do not use `git status`. Repo-local core.fsmonitor and filter.*.clean
    # can execute during a porcelain status of a copied checkout.
    staged = _git_text(repo, ["diff-index", "--cached", "--name-only", "HEAD"])
    untracked = _git_text(repo, ["ls-files", "--others", "--exclude-standard"])
    debug = _git_text(repo, ["ls-files", "--debug"])
    stage = _git_text(repo, ["ls-files", "-s"])
    names = {line for line in staged.splitlines() if line.strip()}
    names.update(line for line in untracked.splitlines() if line.strip())
    names.update(worktree_stat_changed(repo, debug, stage))
    return len(names)


def annotate_actions(actions: list[dict], repo_path: str, store: dict) -> list[dict]:
    annotated: list[dict] = []
    for action in actions:
        argv = list(action.get("argv") or [])
        item = dict(action)
        item["argv"] = argv
        item["digest"] = action_digest(repo_path, argv)
        item["trusted"] = is_action_trusted(store, repo_path, argv)
        annotated.append(item)
    return annotated


def git_state(
    repo: Path,
    home: Path | None = None,
    trust_store_path: Path | None = None,
    trust_store: dict | None = None,
) -> dict:
    resolved = repo
    try:
        resolved = repo.resolve()
    except OSError:
        pass

    branch = _git_text(resolved, ["branch", "--show-current"])
    if not branch:
        short = _git_text(resolved, ["rev-parse", "--short", "HEAD"])
        branch = f"detached {short}" if short else "HEAD"

    changed = git_changed_count(resolved)

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
        store = trust_store if trust_store is not None else load_trust_store(trust_store_path)
        actions = annotate_actions(omafile["actions"], str(resolved), store)

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
        "omafileError": omafile["error"] or omafile["warning"],
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
    trust_store_path: Path | None = None,
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

    store_path = trust_store_path if trust_store_path is not None else default_trust_store(home_path)
    store = load_trust_store(store_path)
    workers = min(8, len(repos))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        projects = list(
            pool.map(
                lambda repo: git_state(
                    repo,
                    home=home_path,
                    trust_store=store,
                ),
                repos,
            )
        )

    assign_ports(projects, proc_root if proc_root is not None else Path("/proc"))
    payload["projects"] = sort_projects(projects)
    return payload


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Scan a work folder for git project state.")
    parser.add_argument(
        "action",
        nargs="?",
        default="scan",
        choices=("scan", "grant", "revoke"),
        help="scan projects, or grant/revoke an Omafile command",
    )
    parser.add_argument("--root", default="~/Work", help="Work folder to scan (default: ~/Work)")
    parser.add_argument("--home", default=str(Path.home()), help="Home directory used for ~ display")
    parser.add_argument("--max-depth", type=int, default=6, help="Maximum search depth from the work root")
    parser.add_argument("--proc", default="/proc", help="proc filesystem used to map listening ports")
    parser.add_argument("--trust-store", default="", help="Trust store JSON path")
    parser.add_argument("--repo", default="", help="Repository path for grant/revoke")
    parser.add_argument("--argv-json", default="", help="JSON array of the command to grant or revoke")
    parser.add_argument("--label", default="", help="Label stored with a granted command")
    return parser


def _parse_argv_json(raw: str) -> tuple[list[str] | None, str]:
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        return None, f"Invalid argv JSON: {exc}"
    if not isinstance(value, list) or not value or not all(isinstance(part, str) for part in value):
        return None, "argv JSON must be an array of strings"
    argv = [part.strip() for part in value]
    error = validate_argv(argv)
    if error:
        return None, error
    return argv, ""


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    home = Path(args.home)
    store_path = Path(args.trust_store) if str(args.trust_store or "").strip() else default_trust_store(home)

    if args.action == "grant":
        repo = Path(args.repo)
        parsed_argv, error = _parse_argv_json(args.argv_json)
        if parsed_argv is None:
            json.dump({"ok": False, "error": error, "digest": ""}, sys.stdout, separators=(",", ":"))
            sys.stdout.write("\n")
            return 1
        payload = grant_trust(store_path, repo, parsed_argv, args.label)
        json.dump(payload, sys.stdout, separators=(",", ":"))
        sys.stdout.write("\n")
        return 0 if payload.get("ok") else 1

    if args.action == "revoke":
        repo = Path(args.repo)
        parsed_argv = None
        if str(args.argv_json or "").strip():
            parsed_argv, error = _parse_argv_json(args.argv_json)
            if parsed_argv is None:
                json.dump({"ok": False, "error": error}, sys.stdout, separators=(",", ":"))
                sys.stdout.write("\n")
                return 1
        payload = revoke_trust(store_path, repo, parsed_argv)
        json.dump(payload, sys.stdout, separators=(",", ":"))
        sys.stdout.write("\n")
        return 0 if payload.get("ok") else 1

    root = resolve_root(args.root, home)
    max_depth = args.max_depth if args.max_depth > 0 else 6
    payload = scan_work(
        root,
        home=home,
        proc_root=Path(args.proc),
        max_depth=max_depth,
        trust_store_path=store_path,
    )
    json.dump(payload, sys.stdout, separators=(",", ":"))
    sys.stdout.write("\n")
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
