"""Server registry and capability routing with failover.

A deployment registers servers (name, type, URL, optional API key) and, for each capability, an ordered
list of (server, model) entries. Requests go to the first entry that answers; a server that fails is
skipped for DOWN_S seconds. This is a stand-in for majel/capabilities (see ADR-0001), using its vocabulary.

The registry lives in a private JSON file (BACKENDS in the deployment config, default backends.json beside
it). API keys are not stored in it: a server names an environment variable, read from the process
environment or from the env file named by SECRETS in the deployment config.
"""

import json
import os
import re
import tempfile
import threading
import time
from pathlib import Path

import requests

DOWN_S = 30.0  # skip a failed server this long before trying it again
CONNECT_S = 2.0  # connect timeout, so failover is quick when a host is off

# Capability -> the server types that can provide it, and whether entries name a model.
CAPABILITIES = {
    "speech_to_text": {"label": "Speech to text", "types": ["speech"], "model": True},
    "text_generation": {"label": "Conversation", "types": ["ollama", "openai"], "model": True},
    # Optional: a stronger model for replies built on tool results (web pages, a peer agent's answer).
    # Small chat models often misread search results; when this is empty, text_generation is used.
    "tool_answers": {"label": "Answers from lookups", "types": ["ollama", "openai"], "model": True},
    "vision": {"label": "Vision", "types": ["ollama", "openai"], "model": True},
    "text_to_speech": {"label": "Voice", "types": ["voice"], "model": True},
    "web_search": {"label": "Web search", "types": ["search"], "model": False},
}
TYPES = {
    "ollama": "Ollama (conversation, vision)",
    "openai": "OpenAI-compatible LLM server: vLLM, llama.cpp, hosted APIs (conversation, vision)",
    "speech": "OpenAI-compatible speech to text (faster-whisper / Speaches)",
    "voice": "OpenAI-compatible text to speech (Kokoro)",
    "search": "SearXNG with the JSON format enabled",
}
NAME = re.compile(r"^[a-z0-9][a-z0-9._-]{0,29}$")
URL = re.compile(r"^https?://[A-Za-z0-9.-]+(:\d{1,5})?(/[A-Za-z0-9._~/-]*)?$")
ENV_VAR = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")


class BackendError(requests.RequestException):
    """Every entry for a capability failed (or none is configured). A RequestException, so callers that
    already handle network errors handle this too."""


# ---- the registry file -------------------------------------------------------
def backends_path(cfg, config_path):
    return Path(cfg.get("BACKENDS") or Path(config_path).parent / "backends.json").expanduser()


def load(path):
    if not path.exists():
        return {"servers": [], "capabilities": {c: [] for c in CAPABILITIES}}
    data = json.loads(path.read_text())
    data.setdefault("servers", [])
    caps = data.setdefault("capabilities", {})
    for c in CAPABILITIES:
        caps.setdefault(c, [])
    return data


def save(path, data):
    validate(data)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    with os.fdopen(fd, "w") as f:
        json.dump(data, f, indent=2)
        f.write("\n")
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def validate(data):
    """Raise ValueError unless the registry is well formed. Doesn't contact any server."""
    names = set()
    for s in data.get("servers", []):
        if not NAME.match(s.get("name", "")):
            raise ValueError(f"server name {s.get('name')!r}: use lowercase letters, digits, '.', '-' or '_'")
        if s["name"] in names:
            raise ValueError(f"two servers are named {s['name']!r}")
        names.add(s["name"])
        if s.get("type") not in TYPES:
            raise ValueError(f"{s['name']}: unknown type {s.get('type')!r}")
        if not URL.match(s.get("url", "")):
            raise ValueError(f"{s['name']}: {s.get('url')!r} isn't an http(s) URL")
        if s.get("key_env") and not ENV_VAR.match(s["key_env"]):
            raise ValueError(f"{s['name']}: API key variable {s['key_env']!r} should look like HOSTED_LLM_API_KEY")
    by_name = {s["name"]: s for s in data.get("servers", [])}
    for cap, entries in data.get("capabilities", {}).items():
        if cap not in CAPABILITIES:
            raise ValueError(f"unknown capability {cap!r}")
        for e in entries:
            server = by_name.get(e.get("server"))
            if not server:
                raise ValueError(f"{CAPABILITIES[cap]['label']}: no server named {e.get('server')!r}")
            if server["type"] not in CAPABILITIES[cap]["types"]:
                raise ValueError(f"{CAPABILITIES[cap]['label']}: {server['name']} is a {server['type']} server")
            if CAPABILITIES[cap]["model"] and not e.get("model"):
                raise ValueError(f"{CAPABILITIES[cap]['label']}: pick a model on {server['name']}")


def migrate(cfg):
    """Build a registry from the older single-address settings (LLM_URL, STT_URL, ...), if present."""
    servers, caps, by_url = [], {c: [] for c in CAPABILITIES}, {}

    def server(url, kind, hint):
        url = url.rstrip("/")
        if url not in by_url:
            base = re.sub(r"[^a-z0-9]+", "-", hint.lower()).strip("-")[:24] or kind
            name, i = base, 2
            while any(s["name"] == name for s in servers):
                name, i = f"{base}-{i}", i + 1
            servers.append({"name": name, "type": kind, "url": url})
            by_url[url] = name
        return by_url[url]

    for part in cfg.get("MODEL_SERVERS", "").split(","):
        if "=" in part:
            label, url = (x.strip() for x in part.split("=", 1))
            server(url, "ollama" if "ollama" in label.lower() or ":11434" in url else "openai", label)
    for cap, prefix in (("text_generation", "LLM"), ("vision", "VISION"), ("vision", "VISION_FALLBACK")):
        if cfg.get(f"{prefix}_URL") and cfg.get(f"{prefix}_MODEL"):
            url = cfg[f"{prefix}_URL"]
            kind = "ollama" if ":11434" in url else "openai"
            caps[cap].append({"server": server(url, kind, kind), "model": cfg[f"{prefix}_MODEL"]})
    if cfg.get("STT_URL"):
        caps["speech_to_text"].append({"server": server(cfg["STT_URL"], "speech", "speech"),
                                       "model": cfg.get("STT_MODEL", "")})
    if cfg.get("TTS_URL"):
        caps["text_to_speech"].append({"server": server(cfg["TTS_URL"], "voice", "voice"),
                                       "model": cfg.get("TTS_MODEL", "kokoro")})
    if cfg.get("SEARCH_URL"):
        caps["web_search"].append({"server": server(cfg["SEARCH_URL"], "search", "search")})
    return {"servers": servers, "capabilities": caps}


# ---- secrets -------------------------------------------------------------------
def read_secrets(cfg):
    path = cfg.get("SECRETS")
    env = {}
    if path and Path(path).expanduser().exists():
        for line in Path(path).expanduser().read_text().splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()
    return env


def headers(server, secrets):
    var = server.get("key_env")
    key = os.environ.get(var) or secrets.get(var) if var else None
    return {"Authorization": f"Bearer {key}"} if key else {}


# ---- probing: what a server offers ----------------------------------------------
def probe(server, secrets, timeout=4):
    """{"online": bool, "models": [...], "vision": [...], "error": str}. Models are what the server lists."""
    url, kind, h = server["url"].rstrip("/"), server["type"], headers(server, secrets)
    out = {"online": False, "models": [], "vision": [], "error": ""}
    try:
        if kind == "ollama":
            base = url.removesuffix("/v1")
            r = requests.get(f"{base}/api/tags", headers=h, timeout=(CONNECT_S, timeout))
            r.raise_for_status()
            for m in r.json()["models"]:
                caps = requests.post(f"{base}/api/show", json={"model": m["name"]}, headers=h,
                                     timeout=(CONNECT_S, timeout)).json().get("capabilities", [])
                if "completion" in caps:
                    out["models"].append(m["name"])
                if "vision" in caps:
                    out["vision"].append(m["name"])
        elif kind == "search":
            r = requests.get(f"{url}/search", params={"q": "test", "format": "json"}, headers=h,
                             timeout=(CONNECT_S, timeout + 6))
            r.raise_for_status()
            r.json()
        else:
            r = requests.get(f"{url}/models", headers=h, timeout=(CONNECT_S, timeout))
            r.raise_for_status()
            out["models"] = [m["id"] for m in r.json().get("data", [])]
            if kind == "openai":
                out["vision"] = list(out["models"])  # OpenAI-style listings don't say which take images
            if kind == "voice":
                v = requests.get(f"{url}/audio/voices", headers=h, timeout=(CONNECT_S, timeout))
                out["voices"] = [x["id"] if isinstance(x, dict) else x for x in v.json().get("voices", [])] \
                    if v.ok else []
        out["online"] = True
    except Exception as e:
        out["error"] = f"{type(e).__name__}: {str(e)[:160]}"
    return out


def probe_all(data, secrets):
    results, threads = {}, []
    for s in data["servers"]:
        t = threading.Thread(target=lambda s=s: results.__setitem__(s["name"], probe(s, secrets)), daemon=True)
        t.start()
        threads.append(t)
    for t in threads:
        t.join(20)
    return results


def model_choices(cap, data, probes):
    """[(server, model)] that could serve this capability, from servers that answered."""
    spec, out = CAPABILITIES[cap], []
    for s in data["servers"]:
        if s["type"] not in spec["types"]:
            continue
        p = probes.get(s["name"], {})
        if not spec["model"]:
            out.append((s["name"], ""))
        else:
            for m in p.get("vision" if cap == "vision" else "models", []):
                out.append((s["name"], m))
    return out


# ---- routing with failover ---------------------------------------------------------
class Router:
    """Tries a capability's entries in order. A server that fails is skipped for DOWN_S seconds."""

    def __init__(self, data, secrets, log=print):
        self.data, self.secrets, self.log = data, secrets, log
        self.servers = {s["name"]: s for s in data["servers"]}
        self.down_until = {}
        self.lock = threading.Lock()

    def configured(self, cap):
        return bool(self.data["capabilities"].get(cap))

    def entries(self, cap):
        entries = [(self.servers[e["server"]], e.get("model", "")) for e in self.data["capabilities"].get(cap, [])
                   if e.get("server") in self.servers]
        now = time.monotonic()
        live = [e for e in entries if self.down_until.get(e[0]["name"], 0) <= now]
        return live or entries  # if everything is marked down, try them all anyway

    def primary(self, cap):
        e = self.entries(cap)
        return (e[0][0]["name"], e[0][1]) if e else (None, None)

    def call(self, cap, fn):
        """fn(url, model, headers) -> result. Fails over on connection errors, timeouts and 5xx answers."""
        entries, last = self.entries(cap), None
        if not entries:
            raise BackendError(f"no server is configured for {CAPABILITIES[cap]['label'].lower()}")
        for i, (server, model) in enumerate(entries):
            try:
                result = fn(server["url"].rstrip("/"), model, headers(server, self.secrets))
                with self.lock:
                    self.down_until.pop(server["name"], None)
                return result
            except (requests.ConnectionError, requests.Timeout) as e:
                last = e
            except requests.HTTPError as e:
                if e.response is None or e.response.status_code < 500:
                    raise  # the server answered; a 4xx is a real error, not an outage
                last = e
            with self.lock:
                self.down_until[server["name"]] = time.monotonic() + DOWN_S
            nxt = f"; trying {entries[i + 1][0]['name']}" if i + 1 < len(entries) else ""
            self.log(f"  [{cap}: {server['name']} failed ({type(last).__name__}){nxt}]")
        raise BackendError(f"every {CAPABILITIES[cap]['label'].lower()} server failed: {last}") from last
