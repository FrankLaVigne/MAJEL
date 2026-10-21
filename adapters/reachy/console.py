#!/usr/bin/env python3
"""The adapter's console: a terminal UI, plus one-shot commands for scripts.

    ./console                     interactive console
    ./console status | start | stop | restart | sleep | wake
    ./console say "text"          the agent speaks exactly this
    ./console ask "question"      as if you'd said it to the agent
    ./console preview bf_emma     try a voice without saving it
    ./console volume [0-100]      robot speaker volume
    ./console get                 list settings
    ./console set KEY VALUE       change a setting (then restart to apply)
"""

import curses
import sys
import textwrap
import threading
import time

import settings as S

HELP = "↑↓ move  ⏎ change  p preview voice  s save  r restart  q quit"
ACTIONS = ["power", "restart", "sleep", "say", "ask", "volume"]


def one_shot(args):
    cmd, rest = args[0], args[1:]
    cfg = S.read_config()
    if cmd == "status":
        print(f"service: {S.service(cfg, 'status')}")
        try:
            st = S.control("status", cfg, timeout=5)
            print(f"{st.get('name') or S.agent_name(cfg)}: {'asleep' if st['asleep'] else 'listening'}, {st['llm']}, voice {st['voice']}, "
                  f"camera {'on' if st['camera'] else 'off'}")
        except ConnectionError:
            print(f"{S.agent_name(cfg)}: not running")
        print(f"speaker volume: {S.robot_volume(cfg)}")
    elif cmd in ("start", "stop", "restart"):
        err = S.service(cfg, cmd)
        print(err or f"{cmd}: ok")
    elif cmd in ("sleep", "wake", "say", "ask", "preview"):
        fields = {"text": " ".join(rest)}
        if cmd == "preview":
            fields = {"voice": rest[0] if rest else "", "text": " ".join(rest[1:])}
        reply = S.control(cmd, cfg, **fields)
        print(reply.get("said") or reply.get("error") or "ok")
    elif cmd == "volume":
        print(S.robot_volume(cfg, int(rest[0]) if rest else None))
    elif cmd == "get":
        for s in S.SETTINGS:
            print(f"{s.key:22} {S.label_for(S.current(s, cfg), cfg)}")
    elif cmd == "set" and len(rest) >= 2:
        s = S.BY_KEY[rest[0]]
        choices, _ = S.discover(cfg) if s.kind in ("model", "vision", "stt", "voice") else ({}, [])
        S.write_config(S.validate(s, " ".join(rest[1:]), choices, cfg))
        print(f"saved {s.key}; restart to apply")
    else:
        print(__doc__)


class Console:
    def __init__(self, scr):
        self.scr, self.pos, self.msg = scr, 0, "Loading…"
        self.cfg = S.read_config()
        self.pending = {}  # setting key -> new display value
        self.choices, self.status = {}, []
        self.service, self.agent, self.volume, self.log = "?", None, None, []
        self.name = S.agent_name(self.cfg)
        self.rows = [("action", a) for a in ACTIONS] + [("setting", s) for s in S.SETTINGS]
        curses.curs_set(0)
        curses.use_default_colors()
        for i, c in enumerate((curses.COLOR_CYAN, curses.COLOR_GREEN, curses.COLOR_YELLOW, curses.COLOR_RED), 1):
            curses.init_pair(i, c, -1)
        threading.Thread(target=self.refresh_backends, daemon=True).start()

    # ---- data ----
    def refresh_backends(self):
        self.choices, self.status = S.discover(self.cfg)
        self.msg = "Ready."

    def refresh_live(self):
        self.service = S.service(self.cfg, "status")
        try:
            self.agent = S.control("status", self.cfg, timeout=2)
        except Exception:
            self.agent = None
        try:
            self.volume = S.robot_volume(self.cfg)
        except Exception:
            self.volume = None
        self.log = S.recent_log(self.cfg, 60)

    def shown(self, s):
        return self.pending.get(s.key, S.current(s, self.cfg))

    # ---- drawing ----
    def draw(self):
        scr = self.scr
        scr.erase()
        h, w = scr.getmaxyx()
        state = self.service
        if self.agent:
            state += ", asleep" if self.agent["asleep"] else ", listening"
        color = curses.color_pair(2 if self.service == "active" else 4)
        scr.addnstr(0, 0, f"{self.name.upper()} CONSOLE", w, curses.A_BOLD | curses.color_pair(1))
        scr.addnstr(0, len(self.name) + 10, f"service: {state}", w - len(self.name) - 10, color)
        scr.addnstr(1, 0, " · ".join(self.status) or "checking backends…", w - 1, curses.A_DIM)
        top, group = 3, None
        visible = h - top - 10
        start = max(0, min(self.pos - visible // 2, len(self.rows) - visible))
        y = top
        for i, (kind, item) in enumerate(self.rows[start:start + visible], start):
            label, value = self.row_text(kind, item)
            g = "Controls" if kind == "action" else item.group
            if g != group and y < top + visible:
                scr.addnstr(y, 0, g.upper(), w - 1, curses.color_pair(1) | curses.A_BOLD)
                group, y = g, y + 1
            if y >= top + visible:
                break
            attr = curses.A_REVERSE if i == self.pos else 0
            mark = "*" if kind == "setting" and item.key in self.pending else " "
            scr.addnstr(y, 0, f" {mark}{label:<28}{value}"[: w - 1].ljust(min(w - 1, 70)), w - 1, attr)
            y += 1
        kind, item = self.rows[self.pos]
        if kind == "setting":
            for j, line in enumerate(textwrap.wrap(item.help, w - 4)[:2]):
                scr.addnstr(h - 9 + j, 2, line, w - 3, curses.A_DIM)
        scr.addnstr(h - 7, 0, "ACTIVITY", w - 1, curses.color_pair(1) | curses.A_BOLD)
        for j, line in enumerate(self.log[-5:]):
            scr.addnstr(h - 6 + j, 1, line, w - 2)
        scr.addnstr(h - 1, 0, (self.msg + "   " + HELP)[: w - 1], w - 1, curses.color_pair(3))
        scr.refresh()

    def row_text(self, kind, item):
        if kind == "action":
            running = self.service == "active"
            asleep = bool(self.agent and self.agent["asleep"])
            return {
                "power": ("Power", "running — ⏎ to stop" if running else "stopped — ⏎ to start"),
                "restart": ("Restart", "⏎ to restart (applies saved settings)"),
                "sleep": ("Listening", "asleep — ⏎ to wake" if asleep else "awake — ⏎ to put to sleep"),
                "say": ("Say…", "she speaks exactly what you type"),
                "ask": ("Ask…", "as if you'd said it to her"),
                "volume": ("Speaker volume", "?" if self.volume is None else f"{self.volume}  (⏎ to change, ←/→ ±5)"),
            }[item]
        value = self.shown(item)
        if item.kind == "toggle":
            value = "on" if value == "1" else "off"
        elif item.kind in ("model", "vision"):
            value = "off" if value == S.OFF else S.label_for(value, self.cfg)
        return item.label, value

    # ---- input ----
    def prompt(self, question, initial=""):
        h, w = self.scr.getmaxyx()
        curses.curs_set(1)
        win = curses.newwin(3, w - 4, h // 2 - 1, 2)
        win.box()
        win.addnstr(0, 2, f" {question} ", w - 8)
        win.addnstr(1, 2, initial, w - 8)
        win.refresh()
        curses.echo()
        try:
            text = win.getstr(1, 2 + len(initial), 500).decode()
        finally:
            curses.noecho()
            curses.curs_set(0)
        return (initial + text).strip()

    def pick(self, s):
        opts = S.options(s, self.choices, self.cfg)
        if not opts:
            self.msg = "No choices: is that server online?"
            return
        values = [v for v, _ in opts]
        sel = values.index(self.shown(s)) if self.shown(s) in values else 0
        h, w = self.scr.getmaxyx()
        height = min(len(opts) + 2, h - 4)
        win = curses.newwin(height, min(w - 4, 70), (h - height) // 2, 2)
        win.keypad(True)
        while True:
            win.erase()
            win.box()
            win.addnstr(0, 2, f" {s.label}  (⏎ choose, p preview, esc cancel) ", 66)
            rows = height - 2
            first = max(0, min(sel - rows // 2, len(opts) - rows))
            for j, (v, label) in enumerate(opts[first:first + rows]):
                win.addnstr(1 + j, 2, label[:64], 64, curses.A_REVERSE if first + j == sel else 0)
            win.refresh()
            k = win.getch()
            if k in (curses.KEY_UP, ord("k")):
                sel = max(0, sel - 1)
            elif k in (curses.KEY_DOWN, ord("j")):
                sel = min(len(opts) - 1, sel + 1)
            elif k in (10, 13, curses.KEY_ENTER):
                self.stage(s, values[sel])
                return
            elif k == ord("p") and s.kind == "voice":
                self.preview(values[sel])
            elif k == 27:
                return

    def stage(self, s, value):
        try:
            S.validate(s, value, self.choices, self.cfg)
        except Exception as e:
            self.msg = f"Not changed: {e}"
            return
        if value == S.current(s, self.cfg):
            self.pending.pop(s.key, None)
        else:
            self.pending[s.key] = value
        self.msg = f"{s.label} changed — s to save."

    def preview(self, voice):
        speed = self.pending.get("TTS_SPEED", S.current(S.BY_KEY["TTS_SPEED"], self.cfg))
        self.run_bg(lambda: S.control("preview", self.cfg, voice=voice, speed=float(speed)), f"Previewing {voice}…")

    def run_bg(self, fn, msg):
        self.msg = msg

        def work():
            try:
                r = fn()
                self.msg = (r.get("said") or r.get("error") or "Done.") if isinstance(r, dict) else (r or "Done.")
            except Exception as e:
                self.msg = f"Failed: {e}"
            self.refresh_live()
        threading.Thread(target=work, daemon=True).start()

    def save(self):
        updates = {}
        try:
            for key, value in self.pending.items():
                updates.update(S.validate(S.BY_KEY[key], value, self.choices, self.cfg))
        except Exception as e:
            self.msg = f"Not saved: {e}"
            return
        S.write_config(updates)
        self.cfg, self.pending = S.read_config(), {}
        if any(k in updates for k in ("MODEL_SERVERS", "STT_URL", "TTS_URL")):
            threading.Thread(target=self.refresh_backends, daemon=True).start()
        if self.service == "active" and self.prompt(f"Saved. Restart {self.name} to apply? (y/n)").lower().startswith("y"):
            self.run_bg(lambda: S.service(self.cfg, "restart"), "Restarting…")
        else:
            self.msg = "Saved. Applies next time she starts."

    def activate(self):
        kind, item = self.rows[self.pos]
        if kind == "setting":
            if item.kind == "toggle":
                self.stage(item, "0" if self.shown(item) == "1" else "1")
            elif item.kind in ("number", "url", "servers", "host"):
                value = self.prompt(f"{item.label}" + (f" ({item.low:g}-{item.high:g})" if item.kind == "number" else ""),
                                    self.shown(item))
                if value:
                    if item.kind == "url":
                        self.msg = "Checking server…"
                        self.draw()
                    self.stage(item, value)
            else:
                self.pick(item)
            return
        if item == "power":
            action = "stop" if self.service == "active" else "start"
            self.run_bg(lambda: S.service(self.cfg, action), f"{action.title()}ing…")
        elif item == "restart":
            self.run_bg(lambda: S.service(self.cfg, "restart"), "Restarting…")
        elif item == "sleep":
            cmd = "wake" if self.agent and self.agent["asleep"] else "sleep"
            self.run_bg(lambda: S.control(cmd, self.cfg), f"{cmd.title()}…")
        elif item in ("say", "ask"):
            text = self.prompt(f"{self.name}, say:" if item == "say" else f"Ask {self.name}:")
            if text:
                self.run_bg(lambda: S.control(item, self.cfg, text=text), f"{self.name} is talking…")
        elif item == "volume":
            v = self.prompt("Speaker volume (0-100)", str(self.volume or ""))
            if v.isdigit():
                self.run_bg(lambda: f"Volume {S.robot_volume(self.cfg, int(v))}", "Setting volume…")

    def loop(self):
        self.scr.timeout(250)
        self.scr.keypad(True)
        last = 0.0
        while True:
            if time.monotonic() - last > 2:
                self.refresh_live()
                last = time.monotonic()
            self.draw()
            k = self.scr.getch()
            kind, item = self.rows[self.pos]
            if k in (curses.KEY_UP, ord("k")):
                self.pos = max(0, self.pos - 1)
            elif k in (curses.KEY_DOWN, ord("j")):
                self.pos = min(len(self.rows) - 1, self.pos + 1)
            elif k in (10, 13, curses.KEY_ENTER):
                self.activate()
            elif k in (curses.KEY_LEFT, curses.KEY_RIGHT) and item == "volume" and self.volume is not None:
                v = max(0, min(100, self.volume + (5 if k == curses.KEY_RIGHT else -5)))
                self.run_bg(lambda: f"Volume {S.robot_volume(self.cfg, v)}", "Setting volume…")
                self.volume = v
            elif k == ord("p"):
                self.preview(self.shown(S.BY_KEY["TTS_VOICE"]))
            elif k == ord("s"):
                self.save() if self.pending else None
            elif k == ord("r"):
                self.run_bg(lambda: S.service(self.cfg, "restart"), "Restarting…")
            elif k == ord("q"):
                if not self.pending or self.prompt("Unsaved changes. Quit anyway? (y/n)").lower().startswith("y"):
                    return


if __name__ == "__main__":
    if len(sys.argv) > 1:
        try:
            one_shot(sys.argv[1:])
        except ConnectionError as e:
            sys.exit(str(e))
    else:
        curses.wrapper(lambda scr: Console(scr).loop())
