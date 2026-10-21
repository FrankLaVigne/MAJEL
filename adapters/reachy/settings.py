"""Shared by the bridge and both consoles: deployment config, the agent profile, and what each setting may be.

The deployment config is an env-style file named by MAJEL_REACHY_CONFIG (default: config.env next to this file).
The agent profile is a private directory named by PROFILE in that config; see profile.example/.
Model, speech and voice choices come from what the configured servers report.
"""

import json
import os
import re
import socket
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

import requests
import yaml

HERE = Path(__file__).resolve().parent
CONFIG = Path(os.environ.get("MAJEL_REACHY_CONFIG", HERE / "config.env")).expanduser()
URL = re.compile(r"^https?://[A-Za-z0-9.-]+(:\d{1,5})?(/[A-Za-z0-9._~/-]*)?$")
HOST = re.compile(r"^[A-Za-z0-9.-]{1,253}$")


@dataclass
class Setting:
    key: str  # config key; model settings write <KEY>_URL and <KEY>_MODEL
    label: str
    help: str
    kind: str  # model, vision, stt, voice, number, toggle, url, servers, host
    group: str
    default: str = ""
    low: float = 0.0
    high: float = 0.0
    step: float = 1.0
    optional: bool = False  # offer "off"


SETTINGS = [
    Setting("LLM", "Conversation model", "The language model for conversation. Small models answer fastest.",
            "model", "Models"),
    Setting("VISION", "Vision model", "Used for questions like \"what do you see?\"", "vision", "Models",
            optional=True),
    Setting("VISION_FALLBACK", "Vision fallback", "Tried when the vision model doesn't answer.", "vision", "Models",
            optional=True),
    Setting("STT_MODEL", "Speech recognition", "Smaller models are faster; larger ones are more accurate.", "stt",
            "Models"),
    Setting("TTS_VOICE", "Voice", "Text-to-speech voice. Preview before saving.", "voice", "Voice"),
    Setting("TTS_SPEED", "Speaking speed", "1.0 is normal.", "number", "Voice", "1.0", 0.7, 1.4, 0.05),
    Setting("CAMERA", "Camera", "Off: no looking, no face tracking, and the video track is discarded.",
            "toggle", "Camera", "1"),
    Setting("FACE_TRACKING_WEIGHT", "Face tracking", "0 is off, 1 lets tracking fully own the head.", "number",
            "Camera", "0.8", 0, 1, 0.1),
    Setting("MIC_START_DB", "Mic threshold (dB)", "How far above background noise counts as speech. "
            "Raise it if the agent reacts to noise; lower it if it misses you.", "number", "Listening", "10",
            6, 20, 1),
    Setting("END_SILENCE_S", "End-of-turn pause (s)", "How long you pause before the agent answers.", "number",
            "Listening", "0.8", 0.4, 2.0, 0.1),
    Setting("FOLLOW_UP_S", "Follow-up window (s)", "How long after answering it listens without its name. "
            "0 means always say the name.", "number", "Listening", "20", 0, 60, 5),
    Setting("LOG_OVERHEARD", "Log overheard speech", "Off logs only word counts for speech not addressed to it.",
            "toggle", "Privacy", "1"),
    Setting("MODEL_SERVERS", "Model servers", "Name=URL pairs, comma separated. Ollama or OpenAI-compatible "
            "(vLLM, llama.cpp…). Conversations and camera frames go here.", "servers", "Backends"),
    Setting("STT_URL", "Speech recognition server", "OpenAI-compatible /v1 base. Microphone audio goes here.",
            "url", "Backends"),
    Setting("TTS_URL", "Voice server", "OpenAI-compatible /v1 base.", "url", "Backends"),
    Setting("REACHY_HOST", "Robot address", "IP or hostname of the Reachy Mini.", "host", "Backends"),
]
BY_KEY = {s.key: s for s in SETTINGS}
OFF = "off"


# ---- deployment config --------------------------------------------------------
def read_config():
    cfg = {}
    for line in CONFIG.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            cfg[key.strip()] = value.strip()
    return cfg


def write_config(updates):
    """Rewrite changed KEY=value lines in place, keeping comments; append new keys at the end."""
    for k, v in updates.items():
        if "\n" in v or "\r" in v:
            raise ValueError(f"{k}: line breaks aren't allowed")
    lines = CONFIG.read_text().splitlines()
    remaining = dict(updates)
    for i, line in enumerate(lines):
        key = line.split("=", 1)[0].strip()
        if not line.lstrip().startswith("#") and "=" in line and key in remaining:
            lines[i] = f"{key}={remaining.pop(key)}"
    if remaining:
        if "# Set by the console" not in lines:
            lines += ["", "# Set by the console"]
        lines += [f"{k}={v}" for k, v in remaining.items()]
    fd, tmp = tempfile.mkstemp(dir=CONFIG.parent, prefix=f".{CONFIG.name}.")
    with os.fdopen(fd, "w") as f:
        f.write("\n".join(lines) + "\n")
    os.chmod(tmp, CONFIG.stat().st_mode & 0o777)
    os.replace(tmp, CONFIG)


def path(cfg, key, default):
    return Path(cfg.get(key) or default).expanduser()


def service_name(cfg):
    return cfg.get("SERVICE", "majel-reachy")


def control_socket(cfg):
    return Path(os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")) / f"{service_name(cfg)}.sock"


def servers(cfg):
    """[(name, url)] from MODEL_SERVERS."""
    out = []
    for part in cfg.get("MODEL_SERVERS", "").split(","):
        if "=" in part:
            name, url = part.split("=", 1)
            out.append((name.strip(), url.strip().rstrip("/")))
    return out


# ---- the agent profile ----------------------------------------------------------
@dataclass
class Peer:
    name: str
    pronouns: tuple  # (subject, object), e.g. ("he", "him")
    url: str
    key: str
    model: str
    can_read: str
    route: re.Pattern | None
    wait_lines: list


@dataclass
class Profile:
    name: str
    aliases: list
    owner: str
    voice: str
    persona: str
    facts: str
    about: str
    stt_hint: str
    peer: Peer | None

    @property
    def slug(self):
        return re.sub(r"[^a-z0-9]+", "-", self.name.lower()).strip("-") or "agent"


def load_profile(cfg):
    """Read profile.yaml and the files it names. Paths in it are relative to the profile directory."""
    file = path(cfg, "PROFILE", HERE / "profile.example" / "profile.yaml")
    base, p = file.parent, yaml.safe_load(file.read_text()) or {}
    rel = lambda v: (base / Path(v).expanduser()) if v else None  # noqa: E731
    name = p.get("name", "Agent")

    persona, meta = "", {}
    if (pf := rel(p.get("persona"))) and pf.exists():
        persona = pf.read_text()
        if persona.startswith("---"):  # optional YAML front matter, e.g. a default voice
            _, front, persona = persona.split("---", 2)
            meta = yaml.safe_load(front) or {}
    facts = ""
    if (ff := rel(p.get("facts"))) and ff.exists():
        facts = ff.read_text().split("\n", 1)[-1].strip()  # drop the title line

    peer = None
    if q := p.get("peer"):
        env = {}
        if (ef := rel(q.get("env"))) and ef.exists():
            for line in ef.read_text().splitlines():
                if "=" in line and not line.lstrip().startswith("#"):
                    k, v = line.split("=", 1)
                    env[k.strip()] = v.strip()
        url, key = env.get(q.get("url_var", "PEER_URL"), ""), env.get(q.get("key_var", "PEER_API_KEY"), "")
        if url and key:
            subject, _, obj = str(q.get("pronouns", "they/them")).partition("/")
            route = q.get("route")
            peer = Peer(
                name=q["name"], pronouns=(subject or "they", obj or "them"), url=url, key=key,
                model=q.get("model", "default"), can_read=q.get("can_read", "personal information"),
                route=re.compile(r"\b(" + "|".join([re.escape(q["name"].lower())] + list(route)) + r")\b", re.I)
                if route else None,
                wait_lines=q.get("wait_lines") or [f"Still waiting on {q['name']}."],
            )

    return Profile(
        name=name,
        aliases=[a.lower() for a in (p.get("aliases") or [name])],
        owner=p.get("owner", "the user"),
        voice=p.get("voice") or meta.get("voice", ""),
        persona=persona.strip(),
        facts=facts,
        about=p.get("about", f"I'm {name}, speaking to you through this robot."),
        stt_hint=p.get("stt_hint", f"{name}."),
        peer=peer,
    )


def agent_name(cfg):
    try:
        return load_profile(cfg).name
    except Exception:
        return "Agent"


# ---- what the backends offer -------------------------------------------------
def _get(url, timeout=3):
    r = requests.get(url, timeout=timeout)
    r.raise_for_status()
    return r.json()


def probe_server(url):
    """[(model, takes_images)] from an Ollama or OpenAI-compatible server. Raises if unreachable."""
    base = url.removesuffix("/v1")
    try:
        tags = _get(f"{base}/api/tags")["models"]
    except Exception:
        tags = None
    if tags is not None:  # Ollama: it can say which models take images
        out = []
        for m in tags:
            caps = requests.post(f"{base}/api/show", json={"model": m["name"]}, timeout=3).json()
            caps = caps.get("capabilities", [])
            if "completion" in caps:
                out.append((m["name"], "vision" in caps))
        return out
    # OpenAI-compatible servers don't say whether a model takes images, so offer each for both.
    return [(m["id"], True) for m in _get(f"{url}/models")["data"]]


def discover(cfg=None):
    """Ask each backend what it has. Returns ({kind: [(value, label)]}, [status lines])."""
    cfg, status = cfg or read_config(), []
    chat, vision = [], []
    for name, url in servers(cfg):
        try:
            for model, images in probe_server(url):
                choice = (f"{url}|{model}", f"{name} · {model}")
                chat.append(choice)
                if images:
                    vision.append(choice)
            status.append(f"{name}: online")
        except Exception:
            status.append(f"{name}: offline")
    stt, voices = [], []
    try:
        stt = [(m["id"], m["id"]) for m in _get(f"{cfg['STT_URL']}/models")["data"]]
        status.append("Speech recognition: online")
    except Exception:
        status.append("Speech recognition: offline")
    try:
        voices = [(v["id"], v["id"]) for v in _get(f"{cfg['TTS_URL']}/audio/voices")["voices"]]
        status.append("Voice server: online")
    except Exception:
        status.append("Voice server: offline")
    return {"model": chat, "vision": vision, "stt": stt, "voice": voices}, status


def current(s, cfg):
    """The setting's value as the UIs show it ("url|model" for model settings)."""
    if s.kind in ("model", "vision"):
        url, model = cfg.get(f"{s.key}_URL", ""), cfg.get(f"{s.key}_MODEL", "")
        return f"{url}|{model}" if url and model else OFF
    if s.kind == "voice":
        if cfg.get(s.key):
            return cfg[s.key]
        try:
            return load_profile(cfg).voice
        except Exception:
            return ""
    return cfg.get(s.key, s.default)


def options(s, choices, cfg):
    """The values a choice setting may take. The current value stays selectable even if its server is offline."""
    opts = list(choices.get(s.kind, []))
    cur = current(s, cfg)
    if s.optional:
        opts.insert(0, (OFF, "Off"))
    if cur and cur not in [v for v, _ in opts]:
        opts.insert(0, (cur, label_for(cur, cfg) + " (unavailable)"))
    return opts


def label_for(value, cfg):
    if "|" in value:
        url, model = value.split("|", 1)
        name = next((n for n, u in servers(cfg) if u == url), url)
        return f"{name} · {model}"
    return value


def validate(s, value, choices, cfg):
    """Return the config updates for this setting, or raise ValueError."""
    value = value.strip()
    if s.kind == "number":
        v = float(value)
        if not s.low <= v <= s.high:
            raise ValueError(f"{s.label} must be between {s.low:g} and {s.high:g}")
        return {s.key: f"{v:g}"}
    if s.kind == "toggle":
        if value not in ("0", "1"):
            raise ValueError(f"{s.label} must be on or off")
        return {s.key: value}
    if s.kind == "url":
        if not URL.match(value):
            raise ValueError(f"{s.label}: {value!r} isn't an http(s) URL")
        _get(f"{value.rstrip('/')}/models")  # must answer before the agent is pointed at it
        return {s.key: value.rstrip("/")}
    if s.kind == "host":
        if not HOST.match(value):
            raise ValueError(f"{s.label}: {value!r} isn't a hostname or IP")
        return {s.key: value}
    if s.kind == "servers":
        pairs = []
        for part in [p for p in value.split(",") if p.strip()]:
            name, _, url = part.partition("=")
            name, url = name.strip(), url.strip().rstrip("/")
            if not re.fullmatch(r"[A-Za-z0-9 ._-]{1,30}", name) or not URL.match(url):
                raise ValueError(f"Model servers: {part.strip()!r} should look like Name=http://host:port/v1")
            pairs.append(f"{name}={url}")
        if not pairs:
            raise ValueError("Model servers: list at least one")
        return {s.key: ",".join(pairs)}  # unreachable servers are allowed: they just show as offline
    if value not in [v for v, _ in options(s, choices, cfg)]:
        raise ValueError(f"{value!r} isn't one of the choices for {s.label}")
    if s.kind in ("model", "vision"):
        url, model = ("", "") if value == OFF else value.split("|", 1)
        return {f"{s.key}_URL": url, f"{s.key}_MODEL": model}
    return {s.key: value}


# ---- live controls that don't need a restart --------------------------------
def robot_volume(cfg, volume=None):
    """Read, or set (0-100), the robot's speaker volume. Setting it plays the robot's own test sound."""
    base = f"http://{cfg['REACHY_HOST']}:8000/api/volume"
    if volume is None:
        return int(_get(f"{base}/current")["volume"])
    volume = int(volume)
    if not 0 <= volume <= 100:
        raise ValueError("volume must be 0-100")
    r = requests.post(f"{base}/set", json={"volume": volume}, timeout=5)
    r.raise_for_status()
    return int(r.json()["volume"])


def service(cfg, action):
    """status → 'active'/'inactive'/...; start, stop, restart → error text or ''."""
    cmd = ["systemctl", "--user", "is-active" if action == "status" else action, service_name(cfg)]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    return r.stdout.strip() if action == "status" else r.stderr.strip()


def recent_log(cfg, lines=40):
    r = subprocess.run(["journalctl", "--user", "-u", service_name(cfg), "-n", str(lines), "-o", "cat", "--no-pager"],
                       capture_output=True, text=True, timeout=10)
    skip = ("INFO:reachy_mini", "WARNING:", "Signalling", "SCTP")
    return [l for l in r.stdout.splitlines() if not l.startswith(skip)]


def control(cmd, cfg=None, timeout=120, **fields):
    """Send one command to the running bridge over its private socket. Raises ConnectionError if it's not up."""
    sock = control_socket(cfg or read_config())
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(timeout)
            s.connect(str(sock))
            s.sendall((json.dumps({"cmd": cmd, **fields}) + "\n").encode())
            reply = s.makefile(encoding="utf-8").readline()
    except (FileNotFoundError, ConnectionRefusedError) as e:
        raise ConnectionError("the agent isn't running") from e
    return json.loads(reply)
