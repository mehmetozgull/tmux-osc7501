#!/usr/bin/env python3
"""Unit tests for the OSC 7501 parser, records and stream scanner."""

import base64
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import osc7501_watch as w  # noqa: E402


def b64(s):
    return base64.b64encode(s.encode()).decode()


def seq(body, st=b"\x1b\\"):
    return b"\x1b]7501;" + body.encode() + st


class ParseTests(unittest.TestCase):
    def p(self, body):
        return w.parse_report(body.encode("latin-1"))

    def test_basic(self):
        r = self.p("state=blocked:kind=permission:app=terraform:msg=" + b64("Apply 3?"))
        self.assertEqual(r, {"state": "blocked", "id": "", "kind": "permission",
                             "app": "terraform", "msg": "Apply 3?"})

    def test_state_required(self):
        self.assertIsNone(self.p("app=x"))
        self.assertIsNone(self.p("state=sleeping"))
        self.assertIsNone(self.p("?"))

    def test_malformed_pairs_skipped(self):
        r = self.p("state=working:junk:=x:BAD=1:app=a b:progress=5")
        self.assertEqual(r["state"], "working")
        self.assertEqual(r["progress"], 5)
        self.assertNotIn("app", r)

    def test_whitespace_and_last_wins(self):
        r = self.p(" state = idle :state=done")
        self.assertEqual(r["state"], "done")

    def test_long_key_discards(self):
        self.assertIsNone(self.p("state=done:abcdefghijklmnopq=1"))

    def test_kind_progress_rules(self):
        r = self.p("state=working:kind=permission:progress=101")
        self.assertNotIn("kind", r)
        self.assertNotIn("progress", r)
        r = self.p("state=done:progress=50")
        self.assertNotIn("progress", r)
        r = self.p("state=blocked:kind=nope:progress=100")
        self.assertNotIn("kind", r)
        self.assertEqual(r["progress"], 100)
        self.assertNotIn("progress", self.p("state=working:progress=+5"))

    def test_ids(self):
        self.assertEqual(self.p("state=done:id=deploy/us-east")["id"], "deploy/us-east")
        self.assertIsNone(self.p("state=done:id="))
        self.assertIsNone(self.p("state=done:id=a//b"))
        self.assertIsNone(self.p("state=done:id=" + "/".join("a" * 9)))
        self.assertIsNone(self.p("state=done:id=" + "a" * 33))

    def test_base64_rules(self):
        self.assertEqual(self.p("state=done:msg=aGk")["msg"], "hi")  # no padding
        self.assertIsNone(self.p("state=done:msg=a=b="))
        self.assertIsNone(self.p("state=done:msg=a"))
        self.assertIsNone(self.p("state=done:msg=" + b64("a\nb")))  # control char
        self.assertIsNone(self.p("state=done:msg=" + base64.b64encode(b"\xff").decode()))
        self.assertIsNone(self.p("state=done:msg=" + b64("x" * 2049)))
        self.assertEqual(self.p("state=done:msg=" + b64("ü" * 1024))["msg"], "ü" * 1024)

    def test_app(self):
        self.assertNotIn("app", self.p("state=done:app=" + "a" * 33))
        self.assertEqual(self.p("state=done:app=claude-code")["app"], "claude-code")


class TrackerTests(unittest.TestCase):
    def apply(self, t, body):
        r = w.parse_report(body.encode())
        self.assertIsNotNone(r, body)
        t.apply(r)

    def test_priority_and_replace(self):
        t = w.Tracker()
        self.apply(t, "state=working:app=claude-code")
        self.assertEqual(t.summary()["state"], "working")
        self.apply(t, "state=done:id=build")
        self.assertEqual(t.summary()["state"], "working")
        self.apply(t, "state=blocked:id=build:kind=auth")
        s = t.summary()
        self.assertEqual((s["state"], s["kind"], s["app"]), ("blocked", "auth", "claude-code"))
        # A report replaces the record: keys not sent are gone.
        self.apply(t, "state=working")
        self.assertEqual(t.app_for(""), "")

    def test_clear(self):
        t = w.Tracker()
        for body in ("state=working", "state=done:id=a", "state=done:id=a/b", "state=done:id=ab"):
            self.apply(t, body)
        self.apply(t, "state=clear:id=a")
        self.assertEqual(sorted(t.records), ["", "ab"])
        self.apply(t, "state=clear")
        self.assertIsNone(t.summary())

    def test_app_inheritance(self):
        t = w.Tracker()
        self.apply(t, "state=working:app=deployer:id=deploy")
        self.apply(t, "state=blocked:id=deploy/eu-west")
        self.assertEqual(t.summary()["app"], "deployer")

    def test_lru_cap(self):
        t = w.Tracker()
        for i in range(300):
            self.apply(t, "state=idle:id=r%d" % i)
        self.assertEqual(len(t.records), w.MAX_RECORDS)
        self.assertNotIn("r0", t.records)
        self.assertIn("r299", t.records)

    def test_drop_states(self):
        t = w.Tracker()
        self.apply(t, "state=working:id=a")
        self.apply(t, "state=done:id=b")
        self.apply(t, "state=blocked:id=c")
        t.drop_states(("working", "blocked"))
        self.assertEqual(list(t.records), ["b"])


class ScannerTests(unittest.TestCase):
    def setUp(self):
        self.events = []
        self.s = w.Scanner(lambda b: self.events.append(("r", b)),
                           lambda: self.events.append(("reset",)),
                           lambda: self.events.append(("prompt",)))

    def test_both_terminators_and_noise(self):
        self.s.feed(b"hello \x1b[31mred" + seq("state=working") + b"x" +
                    seq("state=done", b"\x07") + b"\x1b]0;title\x07")
        self.assertEqual(self.events, [("r", b"state=working"), ("r", b"state=done")])

    def test_split_everywhere(self):
        data = b"ab" + seq("state=blocked:kind=question") + b"cd" + b"\x1b]133;A\x07" + b"\x1bc"
        for cut in range(1, len(data)):
            self.events = []
            s = w.Scanner(lambda b: self.events.append(("r", b)),
                          lambda: self.events.append(("reset",)),
                          lambda: self.events.append(("prompt",)))
            s.feed(data[:cut])
            s.feed(data[cut:])
            self.assertEqual(self.events, [("r", b"state=blocked:kind=question"),
                                           ("prompt",), ("reset",)], cut)

    def test_byte_by_byte(self):
        for b in seq("state=done:msg=" + b64("ok")):
            self.s.feed(bytes([b]))
        self.assertEqual(len(self.events), 1)

    def test_too_long_dropped(self):
        self.s.feed(seq("state=done:msg=" + "A" * 5000))
        self.s.feed(seq("state=idle"))
        self.assertEqual(self.events, [("r", b"state=idle")])

    def test_max_length_accepted(self):
        body = "state=done:x=" + "A" * (w.MAX_SEQ - 7 - 2 - len("state=done:x="))
        data = seq(body)
        self.assertEqual(len(data), w.MAX_SEQ)
        self.s.feed(data)
        self.assertEqual(len(self.events), 1)
        self.events = []
        self.s.feed(seq(body + "A"))
        self.assertEqual(self.events, [])

    def test_aborted_by_other_escape(self):
        self.s.feed(b"\x1b]7501;state=done\x1b[0m" + seq("state=idle"))
        self.assertEqual(self.events, [("r", b"state=idle")])


class RenderTests(unittest.TestCase):
    def test_placeholders_eat_space(self):
        tpl = "#[fg=yellow]● {app} {progress}#[default]"
        self.assertEqual(w.render(tpl, {"app": "claude", "progress": "40%"}),
                         "#[fg=yellow]● claude 40%#[default]")
        self.assertEqual(w.render(tpl, {"app": "", "progress": ""}),
                         "#[fg=yellow]●#[default]")

    def test_escaping(self):
        self.assertEqual(w.fmt_escape("a#{b}"), "a##{b}")
        self.assertEqual(w.tmux_arg("x;"), "x\\;")
        self.assertEqual(w.display_text("a‮b", None), "ab")
        self.assertEqual(w.display_text("abcdef", 4), "abc…")


class FakeTmux:
    """Records the commands it is given; answers display-message queries."""

    def __init__(self, answers=None):
        self.calls = []
        self.answers = answers or {}

    def run(self, *cmds):
        self.calls.extend(cmds)
        first = cmds[0] if cmds else []
        if first[:1] == ["display-message"] and "-p" in first:
            return self.answers.get(first[-1], "")
        return ""

    def pane_sets(self):
        return {c[4]: c[5] for c in self.calls if c[:2] == ["set-option", "-p"]}

    def pane_unsets(self):
        return {c[4] for c in self.calls if c[:2] == ["set-option", "-pu"]}

    def messages(self):
        return [c[-1] for c in self.calls if c[0] == "display-message" and "-d" in c]


def summary(state, app="claude-code", **kw):
    s = {"state": state, "app": app, "kind": "", "msg": "", "progress": "", "title": ""}
    s.update(kw)
    return s


class PublisherTests(unittest.TestCase):
    def test_pane_label_and_icon(self):
        tm = FakeTmux()
        w.Publisher(tm, "%1").update(summary("blocked", kind="permission"))
        sets = tm.pane_sets()
        self.assertEqual(sets["@osc7501_pane_label"], "#[fg=red,bold]! claude#[default]")
        self.assertEqual(sets["@osc7501_pane_icon"], "#[fg=red,bold]!#[default]")

    def test_idle_has_no_label_or_icon(self):
        tm = FakeTmux()
        w.Publisher(tm, "%1").update(summary("idle"))
        self.assertTrue({"@osc7501_pane_label", "@osc7501_pane_icon"} <= tm.pane_unsets())

    def notified(self, where, scope=""):
        tm = FakeTmux({w.NOTIFY_QUERY: (where + " " + scope).strip()})
        w.Publisher(tm, "%1").notify(summary("done"))
        return bool(tm.messages())

    def test_notify_pane_scope(self):
        # "window_active session_attached pane_active"
        self.assertTrue(self.notified("1 1 0"))   # a neighbour pane is focused
        self.assertFalse(self.notified("1 1 1"))  # this pane is focused
        self.assertTrue(self.notified("0 1 1"))   # another window is on screen
        self.assertTrue(self.notified("1 0 1"))   # nobody is attached

    def test_notify_window_scope(self):
        self.assertFalse(self.notified("1 1 0", "window"))
        self.assertTrue(self.notified("0 1 1", "window"))


if __name__ == "__main__":
    unittest.main()
