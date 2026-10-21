#!/usr/bin/env python3
"""The adapter's web console. Serves one page plus a small JSON API on the LAN.

Access needs the token in the file named by PANEL_TOKEN in the deployment config: open
http://<host>:8765/?t=<token> once and a cookie keeps you signed in. Every API write also needs a custom header, which other websites can't send.
"""

import argparse
import hmac
import html
import json
import os
import secrets
import threading
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import settings as S

PAGE = Path(__file__).resolve().parent / "panel.html"
COOKIE = "majel_reachy"
cache = {"choices": {}, "status": [], "lock": threading.Lock()}


def token(file):
    if not file.exists():
        file.parent.mkdir(parents=True, exist_ok=True)
        old = os.umask(0o177)
        try:
            file.write_text(secrets.token_urlsafe(24) + "\n")
        finally:
            os.umask(old)
    return file.read_text().strip()


def refresh_backends(cfg):
    choices, status = S.discover(cfg)
    with cache["lock"]:
        cache["choices"], cache["status"] = choices, status


def state(rediscover=False):
    cfg = S.read_config()
    if rediscover or not cache["status"]:
        refresh_backends(cfg)
    try:
        agent = S.control("status", cfg, timeout=2)
    except Exception:
        agent = None
    try:
        volume = S.robot_volume(cfg)
    except Exception:
        volume = None
    out = []
    for s in S.SETTINGS:
        item = {"key": s.key, "label": s.label, "help": s.help, "kind": s.kind, "group": s.group,
                "value": S.current(s, cfg)}
        if s.kind == "number":
            item.update(low=s.low, high=s.high, step=s.step)
        if s.kind in ("model", "vision", "stt", "voice"):
            item["options"] = S.options(s, cache["choices"], cfg)
        out.append(item)
    return {"service": S.service(cfg, "status"), "agent": agent, "volume": volume, "backends": cache["status"],
            "settings": out, "log": S.recent_log(cfg, 60)}


def save(updates, restart):
    cfg = S.read_config()
    changes, errors = {}, []
    for key, value in updates.items():
        try:
            changes.update(S.validate(S.BY_KEY[key], str(value), cache["choices"], cfg))
        except KeyError:
            errors.append(f"unknown setting {key}")
        except Exception as e:
            errors.append(str(e))
    if errors:
        return {"ok": False, "error": "; ".join(errors)}
    S.write_config(changes)
    if any(k in changes for k in ("MODEL_SERVERS", "STT_URL", "TTS_URL")):
        refresh_backends(S.read_config())
    if restart and S.service(cfg, "status") == "active":
        S.service(cfg, "restart")
        return {"ok": True, "message": f"Saved. {S.agent_name(cfg)} is restarting."}
    return {"ok": True, "message": "Saved. Applies next time she starts."}


def action(req):
    a, cfg = req.get("action"), S.read_config()
    if a in ("start", "stop", "restart"):
        err = S.service(cfg, a)
        return {"ok": not err, "message": err or f"{a.title()} requested."}
    if a == "volume":
        return {"ok": True, "message": f"Volume {S.robot_volume(cfg, int(req.get('volume')))}"}
    if a in ("sleep", "wake", "say", "ask", "preview"):
        fields = {k: req[k] for k in ("text", "voice", "speed") if k in req}
        r = S.control(a, cfg, **fields)
        return {"ok": r.get("ok", False), "message": r.get("said") or r.get("error") or "Done."}
    if a == "rediscover":
        refresh_backends(S.read_config())
        return {"ok": True, "message": "Rechecked backends."}
    return {"ok": False, "error": f"unknown action {a!r}"}


class Handler(BaseHTTPRequestHandler):
    server_version = "majel-reachy-console"

    def log_message(self, fmt, *args):  # keep requests (and tokens in URLs) out of the journal
        pass

    def authed(self):
        cookie = SimpleCookie(self.headers.get("Cookie", ""))
        given = cookie[COOKIE].value if COOKIE in cookie else ""
        return hmac.compare_digest(given, self.server.token)

    def send(self, code, body, ctype="application/json", headers=()):
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        for k, v in headers:
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        url = urlparse(self.path)
        if url.path == "/":
            given = parse_qs(url.query).get("t", [""])[0]
            if given and hmac.compare_digest(given, self.server.token):
                # Swap the token in the URL for a cookie, then drop it from the address bar.
                cookie = f"{COOKIE}={self.server.token}; HttpOnly; SameSite=Strict; Path=/; Max-Age=31536000"
                return self.send(303, b"", "text/plain", [("Set-Cookie", cookie), ("Location", "/")])
            if not self.authed():
                return self.send(403, b"Open the link with ?t=<token> from the PANEL_TOKEN file",
                                 "text/plain")
            page = PAGE.read_text().replace("{{NAME}}", html.escape(S.agent_name(S.read_config())))
            return self.send(200, page.encode(), "text/html; charset=utf-8")
        if not self.authed():
            return self.send(403, {"error": "not signed in"})
        if url.path == "/api/state":
            return self.send(200, state(rediscover="rediscover" in url.query))
        self.send(404, {"error": "not found"})

    def do_POST(self):
        if not self.authed() or self.headers.get("X-Majel-Reachy") != "1":
            return self.send(403, {"error": "not signed in"})
        try:
            req = json.loads(self.rfile.read(min(int(self.headers.get("Content-Length", 0)), 65536)) or b"{}")
            if self.path == "/api/save":
                return self.send(200, save(req.get("updates", {}), bool(req.get("restart"))))
            if self.path == "/api/action":
                return self.send(200, action(req))
            self.send(404, {"error": "not found"})
        except ConnectionError as e:
            self.send(200, {"ok": False, "error": str(e)})
        except Exception as e:
            self.send(200, {"ok": False, "error": f"{type(e).__name__}: {e}"})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=8765)
    args = ap.parse_args()
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    cfg = S.read_config()
    file = S.path(cfg, "PANEL_TOKEN", "~/.config/majel-reachy/panel.token")
    server.token = token(file)
    print(f"{S.agent_name(cfg)} console on http://{args.host}:{args.port}/  (token in {file})")
    server.serve_forever()


if __name__ == "__main__":
    main()
