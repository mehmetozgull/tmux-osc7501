#!/usr/bin/env python3
"""OSC 7501 (Program Status Protocol) watcher for a single tmux pane.

tmux starts one copy of this script per pane with `pipe-pane -O`, so stdin is
the raw byte stream the program in the pane writes to its terminal. tmux itself
drops OSC 7501 (it does not know it), but pipe-pane sees the bytes before tmux
parses them. We pull the reports out of the stream, keep the pane's records as
the spec describes, and mirror a summary into tmux user options:

  pane   @osc7501_state  @osc7501_app  @osc7501_kind  @osc7501_msg
         @osc7501_progress  @osc7501_title  @osc7501_ts  @osc7501_pid
  window @osc7501_label  @osc7501_window_state  @osc7501_window_app
         @osc7501_window_msg  @osc7501_window_pane

The status line shows #{@osc7501_label}. Sending SIGUSR1 marks the pane's
`done`/`error` records as seen and removes them (the tmux plugin does this when
you switch to the window).

Spec: https://superlogical.com/rex/docs/build/program-status
"""

import base64
import os
import re
import select
import signal
import subprocess
import sys
import time
import unicodedata

# --------------------------------------------------------------------------
# Protocol constants (from the spec)

STATES = ("idle", "working", "done", "blocked", "error")
PRIORITY = {"blocked": 5, "error": 4, "working": 3, "done": 2, "idle": 1}
KINDS = ("permission", "question", "auth")

MAX_SEQ = 4096
MAX_KEY = 16
MAX_MSG_ENC, MAX_MSG_DEC = 2732, 2048
MAX_TITLE_ENC, MAX_TITLE_DEC = 256, 192
MAX_ID, MAX_DEPTH = 128, 8
MAX_RECORDS = 256

KEY_RE = re.compile(r"[a-z]+")
VALUE_RE = re.compile(r"[A-Za-z0-9_.,+/=-]*")
SEG_RE = re.compile(r"[A-Za-z0-9_.+-]{1,32}")
APP_RE = re.compile(r"[A-Za-z0-9_.+-]{1,32}")
PROGRESS_RE = re.compile(r"[0-9]{1,3}")

INTRO = b"\x1b]7501;"
# OSC 7501 report, RIS (full reset) and OSC 133;A (shell prompt start).
SCAN_RE = re.compile(rb"\x1b(?:\]7501;|\]133;A|c)")


class Invalid(Exception):
    """The whole report must be discarded."""


def _b64_text(value, max_enc, max_dec):
    if len(value) > max_enc:
        raise Invalid("encoded text too long")
    core = value.rstrip("=")
    if "=" in core or len(core) % 4 == 1:
        raise Invalid("bad base64")
    try:
        raw = base64.b64decode(core + "=" * (-len(core) % 4), validate=True)
    except ValueError:
        raise Invalid("bad base64")
    if len(raw) > max_dec:
        raise Invalid("decoded text too long")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise Invalid("not utf-8")
    for ch in text:
        o = ord(ch)
        if o < 0x20 or 0x7F <= o <= 0x9F:
            raise Invalid("control character")
    return text


def valid_id(rid):
    if len(rid) > MAX_ID:
        return False
    segs = rid.split("/")
    return len(segs) <= MAX_DEPTH and all(SEG_RE.fullmatch(s) for s in segs)


def parse_report(body):
    """Parse the body of one OSC 7501 report. Returns a dict or None (ignored)."""
    text = body.decode("latin-1")
    fields = {}
    for pair in text.split(":"):
        if "=" not in pair:
            continue
        key, value = pair.split("=", 1)
        key, value = key.strip(" \t"), value.strip(" \t")
        if not key or not KEY_RE.fullmatch(key) or not VALUE_RE.fullmatch(value):
            continue  # malformed pair: skip it, keep the rest
        if len(key) > MAX_KEY:
            return None
        fields[key] = value  # repeated keys: last one wins

    state = fields.get("state")
    if state not in STATES and state != "clear":
        return None
    rid = fields.get("id")
    if rid is not None and not valid_id(rid):
        return None  # never fall back to the root record
    rec = {"state": state, "id": rid or ""}
    if state == "clear":
        return rec

    if state == "blocked" and fields.get("kind") in KINDS:
        rec["kind"] = fields["kind"]
    prog = fields.get("progress")
    if (state in ("working", "blocked") and prog is not None
            and PROGRESS_RE.fullmatch(prog) and int(prog) <= 100):
        rec["progress"] = int(prog)
    app = fields.get("app")
    if app is not None and APP_RE.fullmatch(app):
        rec["app"] = app
    try:
        if "msg" in fields:
            rec["msg"] = _b64_text(fields["msg"], MAX_MSG_ENC, MAX_MSG_DEC)
        if "title" in fields:
            rec["title"] = _b64_text(fields["title"], MAX_TITLE_ENC, MAX_TITLE_DEC)
    except Invalid:
        return None
    return rec


# --------------------------------------------------------------------------
# Records

class Tracker:
    """All records of one pane (the pane is our 'terminal')."""

    def __init__(self):
        self.records = {}  # id ("" = root) -> record; dict order = LRU order
        self.clock = 0

    def apply(self, rec):
        rid = rec["id"]
        if rec["state"] == "clear":
            if not rid:
                self.records.clear()
            else:
                for k in list(self.records):
                    if k == rid or k.startswith(rid + "/"):
                        del self.records[k]
            return
        self.clock += 1
        rec["seq"] = self.clock
        self.records.pop(rid, None)  # a report replaces its record completely
        self.records[rid] = rec
        while len(self.records) > MAX_RECORDS:
            del self.records[next(iter(self.records))]

    def drop_states(self, states):
        for k in [k for k, r in self.records.items() if r["state"] in states]:
            del self.records[k]

    def reset(self):
        self.records.clear()

    def app_for(self, rid):
        while True:
            rec = self.records.get(rid)
            if rec and rec.get("app"):
                return rec["app"]
            if not rid:
                return ""
            rid = rid.rsplit("/", 1)[0] if "/" in rid else ""

    def summary(self):
        """The record that best describes the pane, or None."""
        if not self.records:
            return None
        top = max(self.records.values(),
                  key=lambda r: (PRIORITY[r["state"]], r["seq"]))
        return {
            "state": top["state"],
            "app": self.app_for(top["id"]),
            "kind": top.get("kind", ""),
            "msg": top.get("msg", ""),
            "progress": str(top["progress"]) if "progress" in top else "",
            "title": top.get("title", ""),
        }


# --------------------------------------------------------------------------
# Byte stream scanner

class Scanner:
    def __init__(self, on_report, on_reset, on_prompt):
        self.buf = b""
        self.on_report, self.on_reset, self.on_prompt = on_report, on_reset, on_prompt

    def feed(self, data):
        buf = self.buf + data if self.buf else data
        pos = 0
        n = len(buf)
        while True:
            m = SCAN_RE.search(buf, pos)
            if not m:
                # Keep a possibly incomplete introducer at the very end.
                i = buf.rfind(b"\x1b", max(pos, n - len(INTRO) + 1))
                self.buf = buf[i:] if i != -1 else b""
                return
            tok = m.group()
            if tok == b"\x1bc":
                self.on_reset()
                pos = m.end()
                continue
            if tok == b"\x1b]133;A":
                self.on_prompt()
                pos = m.end()
                continue
            start, body = m.start(), m.end()
            limit = min(n, start + MAX_SEQ)
            j = body
            end = term = None
            while j < limit:
                c = buf[j]
                if c == 0x07:
                    end, term = j, 1
                    break
                if c == 0x1B:
                    if j + 1 >= n:
                        break  # need the next byte
                    if buf[j + 1] == 0x5C:  # ESC \  (ST)
                        if j + 2 - start <= MAX_SEQ:
                            end, term = j, 2
                        break
                    end, term = j, 0  # another escape aborts the OSC
                    break
                j += 1
            if end is None:
                if n - start < MAX_SEQ:
                    self.buf = buf[start:]  # wait for more data
                    return
                pos = body  # longer than allowed: drop it
                continue
            if term:
                self.on_report(buf[body:end])
            pos = end + term


# --------------------------------------------------------------------------
# tmux side

DEFAULTS = {
    "format-working": "#[fg=yellow]● {app} {progress}#[default]",
    "format-blocked": "#[fg=red,bold]! {app}#[default]",
    "format-error": "#[fg=red]✗ {app}#[default]",
    "format-done": "#[fg=green]✓ {app}#[default]",
    "format-idle": "",
    "app-aliases": "claude-code=claude",
    "notify": "blocked,error,done",
    "notify-format": "[#{window_index}:#{window_name}] {app} {state} {kind} {msg}",
    "notify-duration": "5000",
    "label-max": "30",
}

PLACEHOLDER_RE = re.compile(r"( ?)\{(app|state|kind|progress|msg|title)\}")


def tmux_arg(value):
    """tmux splits commands on arguments that end in ';' – escape that."""
    return value[:-1] + "\\;" if value.endswith(";") else value


def display_text(text, limit=None):
    # Drop bidi overrides and invisible formatting characters (Unicode Cf).
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Cf")
    if limit and len(text) > limit:
        text = text[: max(1, limit - 1)] + "…"
    return text


def fmt_escape(text):
    return text.replace("#", "##")


def render(template, values):
    def sub(m):
        v = values.get(m.group(2), "")
        return m.group(1) + v if v else ""
    return PLACEHOLDER_RE.sub(sub, template)


class Tmux:
    def __init__(self, socket):
        self.base = ["tmux", "-S", socket] if socket else ["tmux"]

    def run(self, *cmds):
        argv = list(self.base)
        for i, cmd in enumerate(cmds):
            if i:
                argv.append(";")
            argv.extend(tmux_arg(a) for a in cmd)
        try:
            out = subprocess.run(argv, stdout=subprocess.PIPE,
                                 stderr=subprocess.DEVNULL, timeout=5)
            return out.stdout.decode("utf-8", "replace")
        except (OSError, subprocess.SubprocessError):
            return ""


class Publisher:
    PANE_KEYS = ("state", "app", "kind", "msg", "progress", "title")
    # Window options use their own names: pane options inherit from window
    # options, so sharing names would make an unset pane value look set.
    WIN_OPTS = {"state": "@osc7501_window_state", "label": "@osc7501_label",
                "app": "@osc7501_window_app", "msg": "@osc7501_window_msg",
                "pane": "@osc7501_window_pane"}

    def __init__(self, tmux, pane):
        self.tmux, self.pane = tmux, pane
        self.last = None
        self.last_notify = 0.0
        self.cfg = self._load_config()
        aliases = {}
        for item in self.cfg["app-aliases"].split(","):
            if "=" in item:
                k, v = item.split("=", 1)
                aliases[k.strip()] = v.strip()
        self.aliases = aliases
        notify = "" if self.cfg["notify"] == "off" else self.cfg["notify"]
        self.notify_states = {s.strip() for s in notify.split(",") if s.strip()}

    def _load_config(self):
        names = list(DEFAULTS)
        fmt = "\t".join("#{@osc7501-%s}" % n for n in names)
        out = self.tmux.run(["display-message", "-p", "-t", self.pane, fmt])
        vals = out.rstrip("\n").split("\t")
        cfg = dict(DEFAULTS)
        if len(vals) == len(names):
            for n, v in zip(names, vals):
                if v != "":
                    cfg[n] = v
        return cfg

    def start(self):
        self.tmux.run(["set-option", "-p", "-t", self.pane, "@osc7501_pid", str(os.getpid())])

    def stop(self):
        out = self.tmux.run(["display-message", "-p", "-t", self.pane, "#{@osc7501_pid}"])
        if out.strip() == str(os.getpid()):
            self.tmux.run(["set-option", "-pu", "-t", self.pane, "@osc7501_pid"])

    def update(self, summary):
        key = tuple(sorted(summary.items())) if summary else None
        if key == self.last:
            return
        prev = dict(self.last) if self.last else None
        self.last = key

        cmds = []
        for k in self.PANE_KEYS:
            v = summary.get(k, "") if summary else ""
            name = "@osc7501_" + k
            if v:
                cmds.append(["set-option", "-p", "-t", self.pane, name, v])
            else:
                cmds.append(["set-option", "-pu", "-t", self.pane, name])
        if summary:
            cmds.append(["set-option", "-p", "-t", self.pane, "@osc7501_ts", "%.6f" % time.time()])
        else:
            cmds.append(["set-option", "-pu", "-t", self.pane, "@osc7501_ts"])
        self.tmux.run(*cmds)
        self.update_window()

        if summary and summary["state"] in self.notify_states:
            if not prev or (prev.get("state"), prev.get("msg")) != (summary["state"], summary["msg"]):
                self.notify(summary)

    def _values(self, s):
        limit = int(self.cfg["label-max"]) if self.cfg["label-max"].isdigit() else 30
        app = self.aliases.get(s["app"], s["app"])
        return {
            "app": fmt_escape(app),
            "state": s["state"],
            "kind": s["kind"],
            "progress": s["progress"] + "%" if s["progress"] else "",
            "msg": fmt_escape(display_text(s["msg"], limit)),
            "title": fmt_escape(display_text(s["title"], limit)),
        }

    def update_window(self):
        fmt = "\t".join("#{%s}" % f for f in (
            "pane_id", "@osc7501_state", "@osc7501_ts", "@osc7501_app", "@osc7501_kind",
            "@osc7501_progress", "@osc7501_title", "@osc7501_msg"))
        out = self.tmux.run(["list-panes", "-t", self.pane, "-F", fmt])
        best = None
        for line in out.splitlines():
            parts = line.split("\t")
            if len(parts) != 8:
                continue
            pid, state, ts, app, kind, prog, title, msg = parts
            if state not in PRIORITY or not ts:
                continue
            try:
                tsf = float(ts)
            except ValueError:
                tsf = 0.0
            cand = (PRIORITY[state], tsf, pid, {
                "state": state, "app": app, "kind": kind, "progress": prog,
                "title": title, "msg": msg})
            if best is None or cand[:2] > best[:2]:
                best = cand
        win = self.pane
        if best is None:
            self.tmux.run(*[["set-option", "-wu", "-t", win, n] for n in self.WIN_OPTS.values()])
            return
        s = best[3]
        label = render(self.cfg["format-" + s["state"]], self._values(s))
        vals = {"state": s["state"], "label": label, "app": s["app"], "msg": s["msg"], "pane": best[2]}
        cmds = []
        for k, name in self.WIN_OPTS.items():
            if vals[k]:
                cmds.append(["set-option", "-w", "-t", win, name, vals[k]])
            else:
                cmds.append(["set-option", "-wu", "-t", win, name])
        self.tmux.run(*cmds)

    def notify(self, s):
        now = time.monotonic()
        if now - self.last_notify < 1.0:
            return  # rate limit
        out = self.tmux.run(["display-message", "-p", "-t", self.pane,
                             "#{window_active} #{session_attached}"]).split()
        if len(out) == 2 and out[0] == "1" and out[1] != "0":
            return  # the window is on screen already
        self.last_notify = now
        vals = self._values(s)
        vals["msg"] = fmt_escape(display_text(s["msg"], 120))
        text = render(self.cfg["notify-format"], vals)
        dur = self.cfg["notify-duration"] if self.cfg["notify-duration"].isdigit() else "5000"
        self.tmux.run(["display-message", "-d", dur, "-t", self.pane, text])


def main(argv):
    if len(argv) < 2:
        sys.stderr.write("usage: osc7501_watch.py PANE_ID [SOCKET]\n")
        return 2
    pane = argv[1]
    socket = argv[2] if len(argv) > 2 else ""
    tmux = Tmux(socket)
    pub = Publisher(tmux, pane)
    tracker = Tracker()

    def publish():
        pub.update(tracker.summary())

    def on_report(body):
        rec = parse_report(body)
        if rec is not None:
            tracker.apply(rec)
            publish()

    def on_reset():
        if tracker.records:
            tracker.reset()
            publish()

    def on_prompt():
        # A new shell prompt: whatever was running is gone.
        before = len(tracker.records)
        tracker.drop_states(("working", "blocked"))
        if len(tracker.records) != before:
            publish()

    def on_seen():
        before = len(tracker.records)
        tracker.drop_states(("done", "error"))
        if len(tracker.records) != before:
            publish()

    # SIGUSR1 = "the user has looked at this window". The handler only sets a
    # flag; the wakeup fd makes select() return so the main loop handles it
    # without touching the records from inside a signal handler.
    seen = [False]
    wake_r, wake_w = os.pipe()
    os.set_blocking(wake_r, False)
    os.set_blocking(wake_w, False)
    signal.set_wakeup_fd(wake_w)

    def on_usr1(signum, frame):
        seen[0] = True

    signal.signal(signal.SIGUSR1, on_usr1)
    signal.signal(signal.SIGHUP, signal.SIG_IGN)
    pub.start()
    scanner = Scanner(on_report, on_reset, on_prompt)
    try:
        while True:
            ready, _, _ = select.select([0, wake_r], [], [])
            if wake_r in ready:
                try:
                    os.read(wake_r, 512)
                except BlockingIOError:
                    pass
            if seen[0]:
                seen[0] = False
                on_seen()
            if 0 in ready:
                data = os.read(0, 65536)
                if not data:
                    break
                scanner.feed(data)
    except KeyboardInterrupt:
        pass
    finally:
        # EOF means the pane closed or the pipe was restarted. Either way the
        # options stay as they are; a new watcher takes over from here.
        pub.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
