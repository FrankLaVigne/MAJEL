#!/usr/bin/env python3
"""The adapter's console: a terminal UI, plus one-shot commands for scripts.

    ./console                       interactive console
    ./console status | start | stop | restart | sleep | wake
    ./console say "text"            the agent speaks exactly this
    ./console ask "question"        as if you'd said it to the agent
    ./console preview bf_emma       try a voice without saving it
    ./console volume [0-100]        robot speaker volume
    ./console get                   list settings and which servers serve each capability
    ./console set KEY VALUE         change a setting (then restart to apply)
    ./console servers               list servers and whether they answer
    ./console server add NAME TYPE URL [KEY_ENV]     types: ollama openai speech voice search
    ./console server rm NAME
    ./console use CAPABILITY SERVER[=MODEL] ...      e.g. use text_generation gpu-ollama=qwen3:4b
"""

import copy
import curses
import sys
import textwrap
import threading
import time

import backends as B
import settings as S

HELP = "↑↓ move  ⏎ change  f add fallback  x remove  n new server  p preview  s save  r restart  q quit"
ACTIONS = ["power", "restart", "sleep", "say", "ask", "volume"]


def chain_text(entries):
    return " → ".join(f"{e['server']} · {e['model']}" if e.get("model") else e["server"] for e in entries) or "off"


def parse_entry(cap, text):
    server, _, model = text.partition("=")
    return {"server": server, "model": model} if B.CAPABILITIES[cap]["model"] else {"server": server}


def one_shot(args):
    cmd, rest = args[0], args[1:]
    cfg = S.read_config()
    if cmd == "status":
        print(f"service: {S.service(cfg, 'status')}")
        try:
            st = S.control("status", cfg, timeout=5)
            print(f"{st.get('name') or S.agent_name(cfg)}: {'asleep' if st['asleep'] else 'listening'}, {st['llm']}, "
                  f"voice {st['voice']}, camera {'on' if st['camera'] else 'off'}")
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
            print(f"{s.key:22} {S.current(s, cfg)}")
        for cap, entries in S.load_registry(cfg)["capabilities"].items():
            print(f"{cap:22} {chain_text(entries)}")
    elif cmd == "set" and len(rest) >= 2:
        s = S.BY_KEY[rest[0]]
        probes = B.probe_all(S.load_registry(cfg), S.secrets(cfg)) if s.kind == "voice" else None
        S.write_config(S.validate(s, " ".join(rest[1:]), cfg, probes))
        print(f"saved {s.key}; restart to apply")
    elif cmd == "servers":
        data = S.load_registry(cfg)
        probes = B.probe_all(data, S.secrets(cfg))
        for srv in data["servers"]:
            p = probes.get(srv["name"], {})
            state = "online" if p.get("online") else f"offline ({p.get('error', '')[:60]})"
            print(f"{srv['name']:18} {srv['type']:7} {srv['url']:40} {state}")
    elif cmd == "server" and rest[:1] == ["add"] and len(rest) >= 4:
        data = S.load_registry(cfg)
        srv = {"name": rest[1], "type": rest[2], "url": rest[3]}
        if len(rest) > 4:
            srv["key_env"] = rest[4]
        data["servers"].append(srv)
        B.save(S.registry_path(cfg), data)
        p = B.probe(srv, S.secrets(cfg))
        print(f"added {srv['name']}: {'online' if p['online'] else 'offline: ' + p['error']}")
    elif cmd == "server" and rest[:1] == ["rm"] and len(rest) == 2:
        data = S.load_registry(cfg)
        data["servers"] = [x for x in data["servers"] if x["name"] != rest[1]]
        for cap in data["capabilities"]:
            data["capabilities"][cap] = [e for e in data["capabilities"][cap] if e["server"] != rest[1]]
        B.save(S.registry_path(cfg), data)
        print(f"removed {rest[1]}; restart to apply")
    elif cmd == "use" and rest and rest[0] in B.CAPABILITIES:
        data = S.load_registry(cfg)
        data["capabilities"][rest[0]] = [parse_entry(rest[0], t) for t in rest[1:]]
        B.save(S.registry_path(cfg), data)
        print(f"{rest[0]}: {chain_text(data['capabilities'][rest[0]])}; restart to apply")
    else:
        print(__doc__)


class Console:
    def __init__(self, scr):
        self.scr, self.pos, self.msg = scr, 0, "Checking servers…"
        self.cfg = S.read_config()
        self.name = S.agent_name(self.cfg)
        self.pending = {}  # setting key -> new value
        self.reg = S.load_registry(self.cfg)  # working copy; written on save
        self.reg_dirty = False
        self.probes = {}
        self.service, self.agent, self.volume, self.log = "?", None, None, []
        curses.curs_set(0)
        curses.use_default_colors()
        for i, c in enumerate((curses.COLOR_CYAN, curses.COLOR_GREEN, curses.COLOR_YELLOW, curses.COLOR_RED), 1):
            curses.init_pair(i, c, -1)
        threading.Thread(target=self.refresh_backends, daemon=True).start()

    @property
    def rows(self):
        return ([("action", a) for a in ACTIONS]
                + [("capability", c) for c in B.CAPABILITIES]
                + [("server", srv["name"]) for srv in self.reg["servers"]]
                + [("setting", s) for s in S.SETTINGS])

    # ---- data ----
    def refresh_backends(self):
        self.probes = B.probe_all(self.reg, S.secrets(self.cfg))
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

    def server(self, name):
        return next((x for x in self.reg["servers"] if x["name"] == name), None)

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
        scr.addnstr(0, len(self.name) + 10, f"service: {state}", max(1, w - len(self.name) - 10), color)
        rows = self.rows
        self.pos = min(self.pos, len(rows) - 1)
        top, group = 2, None
        visible = max(3, h - top - 10)
        start = max(0, min(self.pos - visible // 2, len(rows) - visible))
        y = top
        for i, (kind, item) in enumerate(rows[start:start + visible], start):
            label, value, attr = self.row_text(kind, item)
            g = {"action": "Controls", "capability": "Capabilities", "server": "Servers"}.get(kind) or item.group
            if g != group and y < top + visible:
                scr.addnstr(y, 0, g.upper(), w - 1, curses.color_pair(1) | curses.A_BOLD)
                group, y = g, y + 1
            if y >= top + visible:
                break
            if i == self.pos:
                attr = curses.A_REVERSE
            changed = (kind == "setting" and item.key in self.pending) or (kind in ("capability", "server") and self.reg_dirty)
            line = f" {'*' if changed else ' '}{label:<24}{value}"
            scr.addnstr(y, 0, line[: w - 1].ljust(min(w - 1, 78)), w - 1, attr)
            y += 1
        kind, item = rows[self.pos]
        help_text = {"capability": "⏎ choose the first server · f add a fallback · x remove the last one",
                     "server": "⏎ edit URL · x remove server · n add a new server"}.get(kind)
        if kind == "setting":
            help_text = item.help
        for j, line in enumerate(textwrap.wrap(help_text or "", w - 4)[:2]):
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
                "power": ("Power", "running — ⏎ to stop" if running else "stopped — ⏎ to start", 0),
                "restart": ("Restart", "⏎ to restart (applies saved settings)", 0),
                "sleep": ("Listening", "asleep — ⏎ to wake" if asleep else "awake — ⏎ to put to sleep", 0),
                "say": ("Say…", "speaks exactly what you type", 0),
                "ask": ("Ask…", "as if you'd said it", 0),
                "volume": ("Speaker volume", "?" if self.volume is None else f"{self.volume}  (⏎ to change, ←/→ ±5)", 0),
            }[item]
        if kind == "capability":
            return B.CAPABILITIES[item]["label"], chain_text(self.reg["capabilities"].get(item, [])), 0
        if kind == "server":
            srv, p = self.server(item), self.probes.get(item)
            state = "…" if p is None else ("online" if p["online"] else "OFFLINE")
            return item, f"{state:8}{srv['type']:8}{srv['url']}", curses.color_pair(4) if p and not p["online"] else 0
        value = self.shown(item)
        if item.kind == "toggle":
            value = "on" if value == "1" else "off"
        return item.label, value, 0

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

    def pick(self, title, opts, current=None, preview=False):
        """Choose from [(value, label)]; returns the value or None."""
        if not opts:
            self.msg = "No choices: is that server online?"
            return None
        values = [v for v, _ in opts]
        sel = values.index(current) if current in values else 0
        h, w = self.scr.getmaxyx()
        height = min(len(opts) + 2, h - 4)
        win = curses.newwin(height, min(w - 4, 76), (h - height) // 2, 2)
        win.keypad(True)
        while True:
            win.erase()
            win.box()
            win.addnstr(0, 2, f" {title}  (⏎ choose{', p preview' if preview else ''}, esc cancel) ", 72)
            rows = height - 2
            first = max(0, min(sel - rows // 2, len(opts) - rows))
            for j, (_, label) in enumerate(opts[first:first + rows]):
                win.addnstr(1 + j, 2, label[:70], 70, curses.A_REVERSE if first + j == sel else 0)
            win.refresh()
            k = win.getch()
            if k in (curses.KEY_UP, ord("k")):
                sel = max(0, sel - 1)
            elif k in (curses.KEY_DOWN, ord("j")):
                sel = min(len(opts) - 1, sel + 1)
            elif k in (10, 13, curses.KEY_ENTER):
                return values[sel]
            elif k == ord("p") and preview:
                self.preview(values[sel])
            elif k == 27:
                return None

    def cap_options(self, cap):
        spec = B.CAPABILITIES[cap]
        if not spec["model"]:
            return [((x["name"], ""), x["name"]) for x in self.reg["servers"] if x["type"] in spec["types"]]
        return [((srv, m), f"{srv} · {m}") for srv, m in B.model_choices(cap, self.reg, self.probes)]

    def edit_capability(self, cap, append):
        choice = self.pick(f"{B.CAPABILITIES[cap]['label']}: {'add a fallback' if append else 'first server'}",
                           self.cap_options(cap))
        if not choice:
            return
        entry = parse_entry(cap, f"{choice[0]}={choice[1]}")
        entries = self.reg["capabilities"].setdefault(cap, [])
        if append or not entries:
            entries.append(entry)
        else:
            entries[0] = entry
        self.reg_dirty, self.msg = True, f"{B.CAPABILITIES[cap]['label']} changed — s to save."

    def add_server(self):
        name = self.prompt("New server name (lowercase, e.g. gpu-ollama)")
        if not name:
            return
        kind = self.pick("Server type", [(t, f"{t:7} {label}") for t, label in B.TYPES.items()])
        if not kind:
            return
        url = self.prompt("URL (e.g. http://host:port/v1)")
        key_env = self.prompt("API key variable, if it needs one (blank for none)")
        srv = {"name": name, "type": kind, "url": url, **({"key_env": key_env} if key_env else {})}
        trial = copy.deepcopy(self.reg)
        trial["servers"].append(srv)
        try:
            B.validate(trial)
        except ValueError as e:
            self.msg = f"Not added: {e}"
            return
        self.reg, self.reg_dirty = trial, True
        self.msg = f"Checking {name}…"
        self.draw()
        p = B.probe(srv, S.secrets(self.cfg))
        self.probes[name] = p
        self.msg = f"{name} added ({'online' if p['online'] else 'offline'}) — s to save."

    def remove_server(self, name):
        if not self.prompt(f"Remove {name} and its capability entries? (y/n)").lower().startswith("y"):
            return
        self.reg["servers"] = [x for x in self.reg["servers"] if x["name"] != name]
        for cap in self.reg["capabilities"]:
            self.reg["capabilities"][cap] = [e for e in self.reg["capabilities"][cap] if e["server"] != name]
        self.reg_dirty, self.msg = True, f"{name} removed — s to save."

    def stage(self, s, value):
        try:
            S.validate(s, value, self.cfg, self.probes if self.probes else None)
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
                updates.update(S.validate(S.BY_KEY[key], value, self.cfg, self.probes or None))
            if self.reg_dirty:
                B.save(S.registry_path(self.cfg), self.reg)
        except Exception as e:
            self.msg = f"Not saved: {e}"
            return
        if updates:
            S.write_config(updates)
        self.cfg, self.pending, self.reg_dirty = S.read_config(), {}, False
        self.reg = S.load_registry(self.cfg)
        if self.service == "active" and self.prompt(f"Saved. Restart {self.name} to apply? (y/n)").lower().startswith("y"):
            self.run_bg(lambda: S.service(self.cfg, "restart"), "Restarting…")
        else:
            self.msg = "Saved. Applies the next time it starts."

    def activate(self):
        kind, item = self.rows[self.pos]
        if kind == "capability":
            return self.edit_capability(item, append=False)
        if kind == "server":
            srv = self.server(item)
            url = self.prompt(f"{item} URL", srv["url"])
            if url and url != srv["url"]:
                srv["url"], self.reg_dirty = url, True
                self.msg = f"{item} changed — s to save."
            return
        if kind == "setting":
            if item.kind == "toggle":
                self.stage(item, "0" if self.shown(item) == "1" else "1")
            elif item.kind in ("number", "host"):
                bounds = f" ({item.low:g}-{item.high:g})" if item.kind == "number" else ""
                value = self.prompt(f"{item.label}{bounds}", self.shown(item))
                if value:
                    self.stage(item, value)
            else:
                value = self.pick(item.label, S.options(item, self.cfg, self.probes), self.shown(item), preview=True)
                if value:
                    self.stage(item, value)
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
            elif k == ord("f") and kind == "capability":
                self.edit_capability(item, append=True)
            elif k == ord("x") and kind == "capability" and self.reg["capabilities"].get(item):
                self.reg["capabilities"][item].pop()
                self.reg_dirty, self.msg = True, "Removed the last entry — s to save."
            elif k == ord("x") and kind == "server":
                self.remove_server(item)
            elif k == ord("n"):
                self.add_server()
            elif k == ord("p"):
                self.preview(self.shown(S.BY_KEY["TTS_VOICE"]))
            elif k == ord("s"):
                if self.pending or self.reg_dirty:
                    self.save()
            elif k == ord("r"):
                self.run_bg(lambda: S.service(self.cfg, "restart"), "Restarting…")
            elif k == ord("q"):
                dirty = self.pending or self.reg_dirty
                if not dirty or self.prompt("Unsaved changes. Quit anyway? (y/n)").lower().startswith("y"):
                    return


if __name__ == "__main__":
    if len(sys.argv) > 1:
        try:
            one_shot(sys.argv[1:])
        except (ConnectionError, ValueError) as e:
            sys.exit(str(e))
    else:
        curses.wrapper(lambda scr: Console(scr).loop())
