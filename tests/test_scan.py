#!/usr/bin/env python3

import json
import os
import socket
import stat
import struct
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import scan


def run(args, cwd=None, check=True):
    return subprocess.run(
        args,
        cwd=cwd,
        check=check,
        capture_output=True,
        text=True,
    )


def git(cwd, *args):
    env = os.environ.copy()
    env["GIT_AUTHOR_NAME"] = "Omabench"
    env["GIT_AUTHOR_EMAIL"] = "omabench@example.test"
    env["GIT_COMMITTER_NAME"] = "Omabench"
    env["GIT_COMMITTER_EMAIL"] = "omabench@example.test"
    env["GIT_TERMINAL_PROMPT"] = "0"
    return subprocess.run(
        ["git", "-C", str(cwd), *args],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )


def init_repo(path, branch="main"):
    path.mkdir(parents=True, exist_ok=True)
    git(path, "init", "-b", branch)
    git(path, "config", "user.name", "Omabench")
    git(path, "config", "user.email", "omabench@example.test")
    (path / "README.md").write_text("hello\n")
    git(path, "add", "README.md")
    git(path, "commit", "-m", "init")
    return path


class DisplayPathTests(unittest.TestCase):
    def test_replaces_home_prefix(self):
        home = Path("/home/ada")
        path = Path("/home/ada/Work/echo")
        self.assertEqual(scan.display_path(path, home), "~/Work/echo")

    def test_leaves_foreign_paths_absolute(self):
        self.assertEqual(
            scan.display_path(Path("/opt/src"), Path("/home/ada")),
            "/opt/src",
        )


class GithubUrlTests(unittest.TestCase):
    def test_ssh_github(self):
        self.assertEqual(
            scan.github_url("git@github.com:modoterra/echo.git"),
            "https://github.com/modoterra/echo",
        )

    def test_https_github(self):
        self.assertEqual(
            scan.github_url("https://github.com/modoterra/echo.git"),
            "https://github.com/modoterra/echo",
        )

    def test_ssh_scheme_github(self):
        self.assertEqual(
            scan.github_url("ssh://git@github.com/modoterra/echo.git"),
            "https://github.com/modoterra/echo",
        )

    def test_ignores_non_github(self):
        self.assertEqual(scan.github_url("git@gitlab.com:acme/app.git"), "")
        self.assertEqual(scan.github_url("https://example.com/acme/app.git"), "")
        self.assertEqual(scan.github_url(""), "")


class DiscoverTests(unittest.TestCase):
    def test_finds_nested_git_repos_and_skips_inner_trees(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            echo = init_repo(root / "modoterra" / "echo")
            bills = init_repo(root / "modoterra" / "omabench")
            (echo / "node_modules" / "left-pad").mkdir(parents=True)
            init_repo(echo / "node_modules" / "left-pad")
            (root / "notes.txt").write_text("ignore me\n")
            (root / ".hidden" / "secret").mkdir(parents=True)
            init_repo(root / ".hidden" / "secret")

            found = [str(path) for path in scan.discover_repos(root)]
            self.assertEqual(sorted(found), [str(bills), str(echo)])

    def test_missing_root_is_empty(self):
        self.assertEqual(scan.discover_repos(Path("/no/such/work/root")), [])

    def test_respects_max_depth(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            init_repo(root / "a" / "b" / "deep")
            self.assertEqual(scan.discover_repos(root, max_depth=2), [])
            self.assertEqual(len(scan.discover_repos(root, max_depth=3)), 1)


class GitStateTests(unittest.TestCase):
    def test_clean_branch_and_github_remote(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = init_repo(Path(tmp) / "echo", branch="alpha.2")
            git(repo, "remote", "add", "origin", "git@github.com:modoterra/echo.git")
            state = scan.git_state(repo)
            self.assertEqual(state["name"], "echo")
            self.assertEqual(state["branch"], "alpha.2")
            self.assertFalse(state["dirty"])
            self.assertEqual(state["changed"], 0)
            self.assertEqual(state["ahead"], 0)
            self.assertEqual(state["behind"], 0)
            self.assertFalse(state["hasUpstream"])
            self.assertEqual(state["githubUrl"], "https://github.com/modoterra/echo")

    def test_counts_dirty_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = init_repo(Path(tmp) / "kestrion")
            (repo / "README.md").write_text("changed\n")
            (repo / "new.txt").write_text("added\n")
            state = scan.git_state(repo)
            self.assertTrue(state["dirty"])
            self.assertEqual(state["changed"], 2)

    def test_ahead_of_upstream(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            upstream = init_repo(tmp_path / "upstream.git")
            clone = tmp_path / "kestrion"
            run(["git", "clone", str(upstream), str(clone)])
            git(clone, "config", "user.name", "Omabench")
            git(clone, "config", "user.email", "omabench@example.test")
            (clone / "ahead.txt").write_text("one more\n")
            git(clone, "add", "ahead.txt")
            git(clone, "commit", "-m", "ahead")
            state = scan.git_state(clone)
            self.assertEqual(state["ahead"], 1)
            self.assertEqual(state["behind"], 0)
            self.assertTrue(state["hasUpstream"])


class ProcPortTests(unittest.TestCase):
    def test_parses_ipv4_listen_inode(self):
        # 127.0.0.1:5173, state LISTEN (0A), inode 42
        ip_hex = struct.pack("<I", struct.unpack(">I", socket.inet_aton("127.0.0.1"))[0]).hex().upper()
        line = (
            "  sl  local_address rem_address   st tx_queue rx_queue tr "
            "tm->when retrnsmt   uid  timeout inode\n"
            f"   0: {ip_hex}:1435 00000000:0000 0A 00000000:00000000 "
            "00:00000000 00000000     0        0 42 1 0000000000000000 100 0 0 10 0\n"
        )
        inodes = scan.parse_tcp_table(line, ipv6=False)
        self.assertEqual(inodes, {"42": 5173})

    def test_assigns_listen_port_by_process_cwd(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            echo = init_repo(tmp_path / "work" / "echo")
            bills = init_repo(tmp_path / "work" / "omabench")
            proc = tmp_path / "proc"
            proc.mkdir()
            ip_hex = struct.pack("<I", struct.unpack(">I", socket.inet_aton("127.0.0.1"))[0]).hex().upper()
            (proc / "net").mkdir()
            (proc / "net" / "tcp").write_text(
                "  sl  local_address rem_address   st tx_queue rx_queue tr "
                "tm->when retrnsmt   uid  timeout inode\n"
                f"   0: {ip_hex}:1F41 00000000:0000 0A 00000000:00000000 "
                "00:00000000 00000000     0        0 99 1 0000000000000000 100 0 0 10 0\n"
            )
            (proc / "net" / "tcp6").write_text("")
            pid_dir = proc / "4242"
            (pid_dir / "fd").mkdir(parents=True)
            os.symlink("socket:[99]", pid_dir / "fd" / "3")
            os.symlink(str(echo), pid_dir / "cwd")

            projects = [
                scan.git_state(echo),
                scan.git_state(bills),
            ]
            scan.assign_ports(projects, proc)
            by_name = {project["name"]: project["ports"] for project in projects}
            self.assertEqual(by_name["echo"], [8001])
            self.assertEqual(by_name["omabench"], [])


class ScanIntegrationTests(unittest.TestCase):
    def test_scan_json_sorts_dirty_first_and_formats_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            work = home / "Work"
            echo = init_repo(work / "modoterra" / "echo")
            bills = init_repo(work / "modoterra" / "omabench")
            (bills / "dirty.txt").write_text("wip\n")

            payload = scan.scan_work(work, home=home, proc_root=Path("/no/proc"))
            self.assertTrue(payload["ok"])
            self.assertTrue(payload["rootExists"])
            self.assertEqual(payload["root"], str(work.resolve()))
            self.assertEqual(payload["displayRoot"], "~/Work")
            names = [project["name"] for project in payload["projects"]]
            self.assertEqual(names, ["omabench", "echo"])
            self.assertEqual(payload["projects"][0]["displayPath"], "~/Work/modoterra/omabench")
            self.assertTrue(payload["projects"][0]["dirty"])
            self.assertFalse(payload["projects"][1]["dirty"])

    def test_cli_prints_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp) / "Work"
            init_repo(work / "echo")
            result = run(
                [sys.executable, str(ROOT / "scan.py"), "--root", str(work), "--home", tmp],
            )
            payload = json.loads(result.stdout)
            self.assertTrue(payload["ok"])
            self.assertEqual(len(payload["projects"]), 1)
            self.assertEqual(payload["projects"][0]["name"], "echo")


class ModelFormatTests(unittest.TestCase):
    def test_status_line_matches_overview(self):
        dirty = {
            "dirty": True,
            "changed": 3,
            "ahead": 2,
            "behind": 0,
            "ports": [5173, 8000],
        }
        clean = {
            "dirty": False,
            "changed": 0,
            "ahead": 0,
            "behind": 0,
            "ports": [],
        }
        self.assertEqual(scan.status_line(dirty), "● 3 changed   ↑2   :5173 :8000")
        self.assertEqual(scan.status_line(clean), "✓ clean")
        self.assertEqual(
            scan.status_line({"dirty": True, "changed": 1, "ahead": 0, "behind": 0, "ports": []}),
            "● 1 change",
        )


if __name__ == "__main__":
    unittest.main()
