"""Shared by the bridge and both consoles: deployment config, agent profile, server registry, and settings.

The deployment config is an env-style file named by MAJEL_REACHY_CONFIG (default: config.env next to this file).
The agent profile is a private directory named by PROFILE in that config; see profile.example/.
Servers and which ones serve each capability live in the registry (backends.py), not here.
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

import backends as B

HERE = Path(__file__).resolve().parent
CONFIG = Path(os.environ.get("MAJEL_REACHY_CONFIG", HERE / "config.env")).expanduser()
HOST = re.compile(r"^[A-Za-z0-9.-]{1,253}$")


@dataclass
class Setting:
    key: str  # config key
    label: str
    help: str
    kind: str  # voice, number, toggle, host
    group: str
    default: str = ""
    low: float = 0.0
    high: float = 0.0
    step: float = 1.0


SETTINGS = [
    Setting("TTS_VOICE", "Voice", "Text-to-speech voice. Preview before saving.", "voice", "Voice"),
    Setting("TTS_SPEED", "Speaking speed", "1.0 is normal.", "number", "Voice", "1.0", 0.7, 1.4, 0.05),
    Setting("CAMERA", "Camera", "Off: no looking, no face tracking, and the video track is discarded.",
            "toggle", "Camera", "1"),
    Setting("FACE_TRACKING_WEIGHT", "Face tracking", "0 is off, 1 lets tracking fully own the head.", "number",
            "Motion", "0.8", 0, 1, 0.1),
    Setting("STILL_WHEN_IDLE", "Still between conversations", "On: no face tracking or gestures until someone "
            "says the name. Both stop again when the follow-up window closes.", "toggle", "Motion", "1"),
    Setting("MIC_START_DB", "Mic threshold (dB)", "How far above background noise counts as speech. "
            "Raise it if the agent reacts to noise; lower it if it misses you.", "number", "Listening", "10",
            6, 20, 1),
    Setting("END_SILENCE_S", "End-of-turn pause (s)", "How long you pause before the agent answers.", "number",
            "Listening", "0.8", 0.4, 2.0, 0.1),
    Setting("FOLLOW_UP_S", "Follow-up window (s)", "How long after answering it listens without its name. "
            "0 means always say the name.", "number", "Listening", "20", 0, 60, 5),
    Setting("WEB", "Internet lookups", "Read-only web search and page reading, through the web search "
            "servers. Pages are fetched from the public internet only.", "toggle", "Internet", "0"),
    Setting("LOG_OVERHEARD", "Log overheard speech", "Off logs only word counts for speech not addressed to it.",
            "toggle", "Privacy", "1"),
    Setting("REACHY_HOST", "Robot address", "IP or hostname of the Reachy Mini.", "host", "Robot"),
]
BY_KEY = {s.key: s for s in SETTINGS}


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


# ---- server registry ------------------------------------------------------------
def registry_path(cfg):
    return B.backends_path(cfg, CONFIG)


def load_registry(cfg):
    """The registry, created from older single-address settings the first time if there isn't one yet."""
    p = registry_path(cfg)
    if not p.exists():
        data = B.migrate(cfg)
        if data["servers"]:
            B.save(p, data)
        return data
    return B.load(p)


def secrets(cfg):
    return B.read_secrets(cfg)


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


# ---- choices for the remaining settings ------------------------------------------
def voice_choices(cfg, probes):
    """Voices offered by the voice servers serving text to speech, primary first."""
    data, out = load_registry(cfg), []
    for e in data["capabilities"].get("text_to_speech", []):
        for v in probes.get(e["server"], {}).get("voices", []):
            if v not in out:
                out.append(v)
    return out


def current(s, cfg):
    if s.kind == "voice":
        if cfg.get(s.key):
            return cfg[s.key]
        try:
            return load_profile(cfg).voice
        except Exception:
            return ""
    return cfg.get(s.key, s.default)


def options(s, cfg, probes):
    """[(value, label)] for a choice setting; the current value stays selectable even if its server is offline."""
    opts = [(v, v) for v in voice_choices(cfg, probes)] if s.kind == "voice" else []
    cur = current(s, cfg)
    if cur and cur not in [v for v, _ in opts]:
        opts.insert(0, (cur, f"{cur} (unavailable)"))
    return opts


def validate(s, value, cfg, probes=None):
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
    if s.kind == "host":
        if not HOST.match(value):
            raise ValueError(f"{s.label}: {value!r} isn't a hostname or IP")
        return {s.key: value}
    if s.kind == "voice":
        if probes is not None and value not in [v for v, _ in options(s, cfg, probes)]:
            raise ValueError(f"{value!r} isn't a voice the voice servers offer")
        if not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", value):
            raise ValueError(f"{value!r} isn't a voice name")
        return {s.key: value}
    raise ValueError(f"unknown setting kind {s.kind}")


# ---- live controls that don't need a restart --------------------------------
def _get(url, timeout=3):
    r = requests.get(url, timeout=timeout)
    r.raise_for_status()
    return r.json()


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
