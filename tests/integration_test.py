#!/usr/bin/env python3
"""End-to-end test against a real tmux server (separate socket, no config).

    python3 tests/integration_test.py
"""

import json
import os
import pty
import select
import shlex
import subprocess
import sys
import time
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
EMIT = os.path.join(ROOT, "bin", "osc7501")
SOCK = "osc7501-test-%d" % os.getpid()


def tmux(*args, check=True):
    out = subprocess.run(["tmux", "-L", SOCK, "-f", "/dev/null"] + list(args),
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if check and out.returncode != 0:
        raise RuntimeError("tmux %s: %s" % (args, out.stderr))
    return out.stdout.strip()


def opt(target, name, scope="-p"):
    return tmux("show-options", scope, "-qv", "-t", target, name, check=False)


def wait_for(fn, expect, timeout=4.0):
    end = time.time() + timeout
    val = fn()
    while val != expect and time.time() < end:
        time.sleep(0.05)
        val = fn()
    return val


def run_in(pane, command):
    tmux("send-keys", "-t", pane, command, "Enter")


class Integration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        tmux("new-session", "-d", "-s", "t", "-x", "160", "-y", "40")
        tmux("set", "-g", "@osc7501-inbox-key", "A")
        tmux("run-shell", os.path.join(ROOT, "osc7501.tmux"))
        cls.p0 = tmux("display", "-p", "-t", "t:0", "#{pane_id}")
        wait_for(lambda: opt(cls.p0, "@osc7501_pid") != "", True)

    @classmethod
    def tearDownClass(cls):
        tmux("kill-server", check=False)

    def emit(self, pane, *args):
        run_in(pane, "%s %s" % (shlex.quote(EMIT), " ".join(shlex.quote(a) for a in args)))

    def test_01_watcher_running(self):
        pid = wait_for(lambda: opt(self.p0, "@osc7501_pid") != "", True)
        self.assertTrue(pid)
        self.assertEqual(tmux("display", "-p", "-t", self.p0, "#{pane_pipe}"), "1")
        hooks = tmux("show-hooks", "-g")
        self.assertIn("after-new-window[7501]", hooks)
        self.assertIn("@osc7501_label", tmux("show", "-gv", "window-status-format"))

    def test_02_states_and_label(self):
        self.emit(self.p0, "working", "-a", "claude-code", "-p", "40")
        self.assertEqual(wait_for(lambda: opt(self.p0, "@osc7501_state"), "working"), "working")
        self.assertEqual(opt(self.p0, "@osc7501_progress"), "40")
        label = wait_for(lambda: opt("t:0", "@osc7501_label", "-w"),
                         "#[fg=yellow]● claude 40%#[default]")
        self.assertEqual(label, "#[fg=yellow]● claude 40%#[default]")
        self.emit(self.p0, "clear")
        self.assertEqual(wait_for(lambda: opt("t:0", "@osc7501_window_state", "-w"), ""), "")

    def test_03_new_window_split_and_priority(self):
        tmux("new-window", "-t", "t:1")
        pa = tmux("display", "-p", "-t", "t:1.0", "#{pane_id}")
        self.assertTrue(wait_for(lambda: opt(pa, "@osc7501_pid") != "", True))
        tmux("split-window", "-t", "t:1")
        pb = tmux("display", "-p", "-t", "t:1.1", "#{pane_id}")
        self.assertTrue(wait_for(lambda: opt(pb, "@osc7501_pid") != "", True))
        time.sleep(0.3)  # let the shells start
        self.emit(pa, "working", "-a", "cargo")
        wait_for(lambda: opt("t:1", "@osc7501_window_state", "-w"), "working")
        self.emit(pb, "blocked", "-a", "terraform", "-k", "permission", "-m", "Apply #3; ok?")
        self.assertEqual(wait_for(lambda: opt("t:1", "@osc7501_window_state", "-w"), "blocked"), "blocked")
        self.assertEqual(opt("t:1", "@osc7501_window_app", "-w"), "terraform")
        self.assertEqual(opt(pb, "@osc7501_msg"), "Apply #3; ok?")
        self.assertEqual(opt("t:1", "@osc7501_window_pane", "-w"), pb)
        # pane b gives up -> window falls back to pane a
        self.emit(pb, "clear")
        self.assertEqual(wait_for(lambda: opt("t:1", "@osc7501_window_state", "-w"), "working"), "working")
        self.assertEqual(opt("t:1", "@osc7501_window_app", "-w"), "cargo")

    def test_04_seen_on_visit(self):
        tmux("select-window", "-t", "t:0")
        pa = tmux("display", "-p", "-t", "t:1.0", "#{pane_id}")
        self.emit(pa, "done", "-a", "cargo")
        self.assertEqual(wait_for(lambda: opt("t:1", "@osc7501_window_state", "-w"), "done"), "done")
        time.sleep(0.2)
        tmux("select-window", "-t", "t:1")
        self.assertEqual(wait_for(lambda: opt("t:1", "@osc7501_window_state", "-w"), ""), "")
        tmux("select-window", "-t", "t:0")

    def test_05_claude_hooks(self):
        pane = self.p0

        def hook(payload, prefix=""):
            js = shlex.quote(json.dumps(payload))
            run_in(pane, "echo %s | %s%s claude-hook > /tmp/osc7501-hook-out-%s" % (
                js, prefix, shlex.quote(EMIT), SOCK))

        hook({"hook_event_name": "UserPromptSubmit", "prompt": "hi"})
        self.assertEqual(wait_for(lambda: opt(pane, "@osc7501_state"), "working"), "working")
        self.assertEqual(opt(pane, "@osc7501_app"), "claude-code")
        hook({"hook_event_name": "Notification",
              "message": "Claude needs your permission to use Bash"})
        self.assertEqual(wait_for(lambda: opt(pane, "@osc7501_state"), "blocked"), "blocked")
        self.assertEqual(opt(pane, "@osc7501_kind"), "permission")
        self.assertEqual(opt(pane, "@osc7501_msg"), "Claude needs your permission to use Bash")
        hook({"hook_event_name": "PostToolUse", "tool_name": "Bash"})
        self.assertEqual(wait_for(lambda: opt(pane, "@osc7501_state"), "working"), "working")
        hook({"hook_event_name": "Stop"})
        self.assertEqual(wait_for(lambda: opt(pane, "@osc7501_state"), "done"), "done")
        hook({"hook_event_name": "Notification", "message": "Claude is waiting for your input"})
        time.sleep(0.5)
        self.assertEqual(opt(pane, "@osc7501_state"), "done")
        # no controlling terminal: falls back to the pane tty via $TMUX_PANE
        hook({"hook_event_name": "UserPromptSubmit"}, prefix="setsid ")
        self.assertEqual(wait_for(lambda: opt(pane, "@osc7501_state"), "working"), "working")
        hook({"hook_event_name": "SessionEnd"})
        self.assertEqual(wait_for(lambda: opt(pane, "@osc7501_state"), ""), "")
        with open("/tmp/osc7501-hook-out-%s" % SOCK) as f:
            self.assertEqual(f.read(), "")  # nothing on stdout
        os.unlink("/tmp/osc7501-hook-out-%s" % SOCK)

    def test_06_prompt_and_reset(self):
        pane = self.p0
        self.emit(pane, "blocked", "-a", "x")
        wait_for(lambda: opt(pane, "@osc7501_state"), "blocked")
        self.emit(pane, "done", "-i", "job", "-a", "y")
        time.sleep(0.3)
        run_in(pane, r"printf '\033]133;A\007'")
        self.assertEqual(wait_for(lambda: opt(pane, "@osc7501_state"), "done"), "done")
        run_in(pane, r"printf '\033c'")
        self.assertEqual(wait_for(lambda: opt(pane, "@osc7501_state"), ""), "")

    def test_07_run_wrapper(self):
        pane = self.p0
        run_in(pane, "%s run -- sh -c 'sleep 1; exit 3'" % shlex.quote(EMIT))
        self.assertEqual(wait_for(lambda: opt(pane, "@osc7501_state"), "working"), "working")
        self.assertEqual(opt(pane, "@osc7501_app"), "sh")
        self.assertEqual(wait_for(lambda: opt(pane, "@osc7501_state"), "error"), "error")
        self.assertIn("exit 3", opt(pane, "@osc7501_msg"))
        self.emit(pane, "clear")

    def test_08_reload_is_idempotent(self):
        pid = opt(self.p0, "@osc7501_pid")
        fmt = tmux("show", "-gv", "window-status-format")
        tmux("run-shell", os.path.join(ROOT, "osc7501.tmux"))
        self.assertEqual(opt(self.p0, "@osc7501_pid"), pid)
        self.assertEqual(tmux("show", "-gv", "window-status-format"), fmt)

    def test_09_status_line_and_notification(self):
        pa = tmux("display", "-p", "-t", "t:1.0", "#{pane_id}")
        tmux("select-window", "-t", "t:0")
        pid, fd = pty.fork()
        if pid == 0:
            os.environ.pop("TMUX", None)
            os.environ["TERM"] = "xterm-256color"
            os.environ["LANG"] = "C.UTF-8"
            os.execvp("tmux", ["tmux", "-u", "-L", SOCK, "attach", "-t", "t"])
        screen = b""

        def pump(seconds):
            nonlocal screen
            end = time.time() + seconds
            while time.time() < end:
                r, _, _ = select.select([fd], [], [], 0.1)
                if r:
                    try:
                        screen += os.read(fd, 65536)
                    except OSError:
                        return

        pump(1.0)
        self.emit(pa, "blocked", "-a", "claude-code", "-k", "permission", "-m", "Run rm -rf build?")
        wait_for(lambda: opt("t:1", "@osc7501_window_state", "-w"), "blocked")
        pump(1.5)
        msgs = tmux("show-messages", check=False)
        tmux("detach-client", "-a", check=False)
        tmux("detach-client", check=False)
        pump(0.3)
        os.close(fd)
        os.waitpid(pid, 0)
        self.assertIn("! claude".encode(), screen)
        self.assertIn("[1:", msgs)
        self.assertIn("claude blocked permission Run rm -rf build?", msgs)

    def test_09b_stale_message_not_inherited(self):
        pa = tmux("display", "-p", "-t", "t:1.0", "#{pane_id}")
        self.emit(pa, "blocked", "-a", "x", "-m", "old question")
        wait_for(lambda: opt("t:1", "@osc7501_window_msg", "-w"), "old question")
        self.emit(pa, "done", "-a", "x")
        self.assertEqual(wait_for(lambda: opt("t:1", "@osc7501_window_state", "-w"), "done"), "done")
        self.assertEqual(opt("t:1", "@osc7501_window_msg", "-w"), "")
        self.assertEqual(opt(pa, "@osc7501_msg"), "")

    def test_10_inbox_binding(self):
        keys = tmux("list-keys", "-T", "prefix", "A")
        self.assertIn("choose-tree", keys)


if __name__ == "__main__":
    unittest.main(verbosity=2)
