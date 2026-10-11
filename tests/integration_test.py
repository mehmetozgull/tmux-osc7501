#!/usr/bin/env python3
"""End-to-end test against a real tmux server (separate socket, no config).

    python3 tests/integration_test.py
"""

import json
import os
import pty
import re
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
        self.assertIn("@osc7501_status", tmux("show", "-gv", "window-status-format"))

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

    def both_done(self, pa, pb):
        self.emit(pa, "done", "-a", "cargo")
        self.emit(pb, "done", "-a", "make")
        both = lambda: opt(pa, "@osc7501_state") + opt(pb, "@osc7501_state")
        self.assertEqual(wait_for(both, "donedone"), "donedone")
        time.sleep(0.2)

    def test_04_seen_on_visit(self):
        # pane scope (default): a window switch shows its active pane only
        tmux("select-window", "-t", "t:0")
        pa = tmux("display", "-p", "-t", "t:1.0", "#{pane_id}")
        pb = tmux("display", "-p", "-t", "t:1.1", "#{pane_id}")
        tmux("select-pane", "-t", pa)  # t:1 is not on screen: nothing seen yet
        self.both_done(pa, pb)
        tmux("select-window", "-t", "t:1")
        self.assertEqual(wait_for(lambda: opt(pa, "@osc7501_state"), ""), "")
        time.sleep(0.3)
        self.assertEqual(opt(pb, "@osc7501_state"), "done")
        tmux("select-pane", "-t", pb)  # keyboard or mouse: both run select-pane
        self.assertEqual(wait_for(lambda: opt(pb, "@osc7501_state"), ""), "")
        tmux("select-window", "-t", "t:0")

    def test_04c_unknown_scope_means_pane(self):
        tmux("set", "-g", "@osc7501-scope", "Pane")
        try:
            pa = tmux("display", "-p", "-t", "t:1.0", "#{pane_id}")
            pb = tmux("display", "-p", "-t", "t:1.1", "#{pane_id}")
            tmux("select-window", "-t", "t:1")
            tmux("select-pane", "-t", pa)  # tmux runs no hook for the active pane
            self.emit(pb, "done", "-a", "make")
            self.assertEqual(wait_for(lambda: opt(pb, "@osc7501_state"), "done"), "done")
            time.sleep(0.2)
            tmux("select-pane", "-t", pb)
            self.assertEqual(wait_for(lambda: opt(pb, "@osc7501_state"), ""), "")
        finally:
            tmux("set", "-gu", "@osc7501-scope")
            tmux("select-window", "-t", "t:0")

    def test_04b_seen_window_scope(self):
        tmux("set", "-g", "@osc7501-scope", "window")
        try:
            tmux("select-window", "-t", "t:0")
            pa = tmux("display", "-p", "-t", "t:1.0", "#{pane_id}")
            pb = tmux("display", "-p", "-t", "t:1.1", "#{pane_id}")
            self.both_done(pa, pb)
            tmux("select-window", "-t", "t:1")
            self.assertEqual(wait_for(lambda: opt("t:1", "@osc7501_window_state", "-w"), ""), "")
            self.assertEqual(opt(pa, "@osc7501_state") + opt(pb, "@osc7501_state"), "")
        finally:
            tmux("set", "-gu", "@osc7501-scope")
            tmux("select-window", "-t", "t:0")

    def test_05_claude_hooks(self):
        pane = self.p0
        out = "/tmp/osc7501-hook-out-%s" % SOCK
        # Claude Code runs hooks without a controlling terminal, so run them
        # in a new session: the report has to reach the pane via $TMUX_PANE.
        no_ctty = "python3 -c %s" % shlex.quote(
            "import subprocess, sys; sys.exit(subprocess.call(sys.argv[1:], start_new_session=True))")

        def hook(event, **fields):
            js = shlex.quote(json.dumps(dict(fields, hook_event_name=event)))
            run_in(pane, "echo %s | %s %s claude-hook >> %s" % (js, no_ctty, shlex.quote(EMIT), out))

        state = lambda: opt(pane, "@osc7501_state")
        hook("UserPromptSubmit", prompt="hi")
        self.assertEqual(wait_for(state, "working"), "working")
        self.assertEqual(opt(pane, "@osc7501_app"), "claude-code")
        hook("PermissionRequest", tool_name="Bash", tool_input={"command": "ls"})
        self.assertEqual(wait_for(state, "blocked"), "blocked")
        self.assertEqual(opt(pane, "@osc7501_kind"), "permission")
        self.assertEqual(opt(pane, "@osc7501_msg"), "Claude needs your permission to use Bash")
        hook("PostToolUse", tool_name="Bash")
        self.assertEqual(wait_for(state, "working"), "working")
        hook("Stop")
        self.assertEqual(wait_for(state, "done"), "done")
        hook("Notification", notification_type="idle_prompt",
             message="Claude is waiting for your input")
        time.sleep(0.5)
        self.assertEqual(state(), "done")
        hook("StopFailure", error="rate_limit", last_assistant_message="API Error: Rate limit reached")
        self.assertEqual(wait_for(state, "error"), "error")
        hook("SessionEnd")
        self.assertEqual(wait_for(state, ""), "")
        with open(out) as f:
            self.assertEqual(f.read(), "")  # nothing on stdout
        os.unlink(out)

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
        # pane scope: the pane next to the one you are in still notifies
        pb = tmux("display", "-p", "-t", "t:1.1", "#{pane_id}")
        tmux("select-window", "-t", "t:1")
        tmux("select-pane", "-t", pa)
        pump(0.5)
        self.emit(pb, "blocked", "-a", "make", "-m", "next door")
        wait_for(lambda: opt(pb, "@osc7501_state"), "blocked")
        pump(1.5)
        msgs_pane = tmux("show-messages", check=False)
        self.emit(pb, "clear")
        tmux("select-window", "-t", "t:0")
        tmux("detach-client", "-a", check=False)
        tmux("detach-client", check=False)
        pump(0.3)
        os.close(fd)
        os.waitpid(pid, 0)
        plain = re.sub(rb"\x1b\[[0-9;]*m", b"", screen)
        self.assertIn(b" 0!", plain)  # pane 0 icon in the window list
        self.assertIn("[1.0:", msgs)
        self.assertIn("claude blocked permission Run rm -rf build?", msgs)
        self.assertIn("[1.1:", msgs_pane)
        self.assertIn("make blocked next door", msgs_pane)

    def test_09b_stale_message_not_inherited(self):
        pa = tmux("display", "-p", "-t", "t:1.0", "#{pane_id}")
        self.emit(pa, "blocked", "-a", "x", "-m", "old question")
        wait_for(lambda: opt("t:1", "@osc7501_window_msg", "-w"), "old question")
        self.emit(pa, "done", "-a", "x")
        self.assertEqual(wait_for(lambda: opt("t:1", "@osc7501_window_state", "-w"), "done"), "done")
        self.assertEqual(opt("t:1", "@osc7501_window_msg", "-w"), "")
        self.assertEqual(opt(pa, "@osc7501_msg"), "")

    def test_10_inbox_binding(self):
        # `list-keys -T prefix A` prints nothing on tmux 3.7, so search the table
        keys = [k for k in tmux("list-keys", "-T", "prefix").splitlines()
                if " A " in k and "choose-tree" in k]
        self.assertEqual(len(keys), 1, keys)
        self.assertIn("#{@osc7501_state}", keys[0])  # pane scope lists panes

    def test_11_pane_status_and_border(self):
        pa = tmux("display", "-p", "-t", "t:1.0", "#{pane_id}")
        pb = tmux("display", "-p", "-t", "t:1.1", "#{pane_id}")
        self.emit(pa, "working", "-a", "cargo")
        self.emit(pb, "blocked", "-a", "terraform")
        both = lambda: opt(pa, "@osc7501_state") + opt(pb, "@osc7501_state")
        self.assertEqual(wait_for(both, "workingblocked"), "workingblocked")
        time.sleep(0.2)
        self.assertEqual(tmux("display", "-p", "-t", "t:1", "#{E:@osc7501_status}"),
                         "0#[fg=yellow]●#[default] 1#[fg=red,bold]!#[default]")  # strip()ped
        self.emit(self.p0, "done", "-a", "claude-code")
        single = lambda: tmux("display", "-p", "-t", "t:0", "#{E:@osc7501_status}")
        self.assertEqual(wait_for(single, "#[fg=green]✓ claude#[default]"),
                         "#[fg=green]✓ claude#[default]")
        tmux("set", "-g", "@osc7501-pane-border", "on")
        try:
            tmux("run-shell", os.path.join(ROOT, "osc7501.tmux"))
            self.assertEqual(tmux("show", "-gv", "pane-border-status"), "top")
            fmt = tmux("show", "-gv", "pane-border-format")
            self.assertEqual(fmt.count("@osc7501_pane_status"), 1)
            self.assertEqual(tmux("display", "-p", "-t", pb, "#{E:@osc7501_pane_status}"),
                             "#[fg=red,bold]! terraform#[default]")
        finally:
            tmux("set", "-gu", "@osc7501-pane-border")
            for p in (pa, pb, self.p0):
                self.emit(p, "clear")


    def test_12_placeholder_and_upgrade(self):
        plugin = os.path.join(ROOT, "osc7501.tmux")
        old = "#[bold]#I:#W#{?@osc7501_label, #{@osc7501_label},}"  # older version
        tmux("set", "-g", "window-status-format", "#I:#W#{osc7501}!")
        tmux("set", "-g", "window-status-current-format", old)
        tmux("run-shell", plugin)
        self.assertEqual(tmux("show", "-gv", "window-status-format"),
                         "#I:#W#{E:@osc7501_status}!")
        self.assertEqual(tmux("show", "-gv", "window-status-current-format"),
                         "#[bold]#I:#W#{E:@osc7501_status}")

    def new_pane(self, *split):
        tmux(*split)
        pane = tmux("display", "-p", "-t", split[split.index("-t") + 1], "#{pane_id}")
        self.assertTrue(wait_for(lambda: opt(pane, "@osc7501_pid") != "", True))
        return pane

    def test_13_closed_or_moved_pane_leaves_no_label(self):
        tmux("new-window", "-t", "t:5")
        win_state = lambda: opt("t:5", "@osc7501_window_state", "-w")
        gone = self.new_pane("split-window", "-t", "t:5")
        time.sleep(0.3)
        self.emit(gone, "blocked", "-a", "claude-code")
        self.assertEqual(wait_for(win_state, "blocked"), "blocked")
        tmux("kill-pane", "-t", gone)
        self.assertEqual(wait_for(win_state, ""), "")
        self.assertEqual(opt("t:5", "@osc7501_label", "-w"), "")
        moved = self.new_pane("split-window", "-t", "t:5")
        time.sleep(0.3)
        self.emit(moved, "done", "-a", "make")
        self.assertEqual(wait_for(win_state, "done"), "done")
        tmux("break-pane", "-d", "-s", moved)
        self.emit(moved, "working", "-a", "make")  # first report after the move
        self.assertEqual(wait_for(win_state, ""), "")
        tmux("kill-window", "-t", "t:5")

if __name__ == "__main__":
    unittest.main(verbosity=2)
