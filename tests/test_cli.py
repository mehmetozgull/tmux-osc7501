#!/usr/bin/env python3
"""Unit tests for bin/osc7501: Claude Code hook mapping and the installer."""

import importlib.machinery
import importlib.util
import json
import os
import sys
import tempfile
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import osc7501_watch as w  # noqa: E402


def load_cli():
    loader = importlib.machinery.SourceFileLoader("osc7501_cli", os.path.join(ROOT, "bin", "osc7501"))
    module = importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name, loader))
    loader.exec_module(module)
    return module


cli = load_cli()


def report(event, **fields):
    """Run one hook event through the mapping and parse what it would send."""
    seq = cli.claude_report(dict(fields, hook_event_name=event))
    if seq is None:
        return None
    assert seq.startswith(b"\x1b]7501;") and seq.endswith(b"\x1b\\"), seq
    return w.parse_report(seq[len(b"\x1b]7501;"):-2])


def state(event, **fields):
    rec = report(event, **fields)
    return rec and (rec["state"], rec.get("kind"))


class ClaudeReportTests(unittest.TestCase):
    def test_session_flow(self):
        self.assertEqual(state("SessionStart"), ("idle", None))
        self.assertEqual(state("UserPromptSubmit"), ("working", None))
        self.assertEqual(state("PostToolUse", tool_name="Bash"), ("working", None))
        self.assertEqual(state("PostToolUseFailure", tool_name="Bash"), ("working", None))
        self.assertEqual(state("Stop"), ("done", None))
        self.assertEqual(report("SessionEnd"), {"state": "clear", "id": ""})
        self.assertEqual(report("UserPromptSubmit")["app"], "claude-code")

    def test_permission_request_blocks_at_once(self):
        rec = report("PermissionRequest", tool_name="Bash", tool_input={"command": "rm -rf x"})
        self.assertEqual((rec["state"], rec["kind"]), ("blocked", "permission"))
        self.assertIn("Bash", rec["msg"])

    def test_ask_user_question_blocks(self):
        rec = report("PreToolUse", tool_name="AskUserQuestion",
                     tool_input={"questions": [{"question": "Which framework?"}]})
        self.assertEqual((rec["state"], rec["kind"], rec["msg"]),
                         ("blocked", "question", "Which framework?"))
        rec = report("PreToolUse", tool_name="AskUserQuestion", tool_input={"questions": "junk"})
        self.assertEqual(rec["state"], "blocked")  # odd input still blocks

    def test_plan_approval_blocks(self):
        self.assertEqual(state("PreToolUse", tool_name="ExitPlanMode", tool_input={}),
                         ("blocked", "permission"))

    def test_other_tools_are_ignored(self):
        self.assertIsNone(report("PreToolUse", tool_name="Read", tool_input={}))

    def test_api_error_is_error(self):
        rec = report("StopFailure", error="rate_limit",
                     last_assistant_message="API Error: Rate limit reached")
        self.assertEqual((rec["state"], rec["msg"]), ("error", "API Error: Rate limit reached"))
        self.assertEqual(report("StopFailure", error="overloaded")["msg"], "overloaded")
        self.assertEqual(state("StopFailure", error={"type": "new"}), ("error", None))

    def test_notifications_that_block(self):
        cases = {"permission_prompt": "permission", "elicitation_dialog": "question",
                 "elicitation_url_dialog": "question", "agent_needs_input": "question",
                 "quota_auto_resume_stale": "question"}
        for ntype, kind in cases.items():
            rec = report("Notification", notification_type=ntype, message="Look at me")
            self.assertEqual((rec["state"], rec["kind"], rec["msg"]),
                             ("blocked", kind, "Look at me"), ntype)

    def test_notifications_that_do_not_block(self):
        for ntype in ("idle_prompt", "auth_success", "elicitation_complete",
                      "elicitation_response", "agent_completed",
                      "quota_auto_resume_disabled", "some_future_type"):
            self.assertIsNone(report("Notification", notification_type=ntype,
                                     message="needs your permission"), ntype)
        self.assertEqual(state("Notification", notification_type="quota_auto_resume_fired",
                               message="Resuming"), ("working", None))

    def test_notification_without_type(self):
        # Claude Code versions that do not send notification_type
        self.assertEqual(state("Notification", message="Claude needs your permission to use Bash"),
                         ("blocked", "permission"))
        self.assertIsNone(report("Notification", message="Claude is waiting for your input"))


class InstallTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.dir.name, "settings.json")
        mine = {"type": "command", "command": "~/.claude/bitti.sh"}
        self.settings = {"model": "opus", "hooks": {"Stop": [{"hooks": [mine]}]}}
        with open(self.path, "w") as f:
            json.dump(self.settings, f)

    def tearDown(self):
        self.dir.cleanup()

    def install(self):
        with open(os.devnull, "w") as null:
            stdout, sys.stdout = sys.stdout, null
            try:
                return cli.cmd_install(self.path, dry_run=False)
            finally:
                sys.stdout = stdout

    def load(self):
        with open(self.path) as f:
            return json.load(f)

    def ours(self, settings, event):
        return [(g, h) for g in settings["hooks"].get(event, []) for h in g["hooks"]
                if "claude-hook" in h["command"]]

    def test_adds_every_event_once_and_keeps_user_hooks(self):
        self.assertEqual(self.install(), 0)
        self.assertEqual(self.install(), 0)
        s = self.load()
        self.assertEqual(s["model"], "opus")
        self.assertEqual(s["hooks"]["Stop"][0]["hooks"][0]["command"], "~/.claude/bitti.sh")
        for event, _matcher in cli.HOOKS:
            self.assertEqual(len(self.ours(s, event)), 1, event)

    def test_matcher_and_no_background_hooks(self):
        self.install()
        s = self.load()
        group, _ = self.ours(s, "PreToolUse")[0]
        self.assertEqual(group["matcher"], "AskUserQuestion|ExitPlanMode")
        # async hooks are not ordered: a late PostToolUse could undo Stop's done
        for event, _matcher in cli.HOOKS:
            _, hook = self.ours(s, event)[0]
            self.assertNotIn("async", hook, event)

    def test_backup_never_overwrites_an_older_one(self):
        with open(self.path + ".bak", "w") as f:
            f.write("an older backup")
        self.install()
        with open(self.path + ".bak") as f:
            self.assertEqual(f.read(), "an older backup")
        backups = [n for n in os.listdir(self.dir.name) if n.startswith("settings.json.osc7501-")]
        self.assertEqual(len(backups), 1, backups)
        with open(os.path.join(self.dir.name, backups[0])) as f:
            self.assertEqual(json.load(f), self.settings)

    def test_add_hooks_does_not_mutate_its_input(self):
        before = json.dumps(self.settings, sort_keys=True)
        cli.add_hooks(self.settings, "osc7501 claude-hook")
        self.assertEqual(json.dumps(self.settings, sort_keys=True), before)


if __name__ == "__main__":
    unittest.main()
