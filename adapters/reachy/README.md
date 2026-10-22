# Reachy Mini adapter

Gives an agent a physical voice through a [Reachy Mini](https://github.com/pollen-robotics/reachy_mini) Wireless robot. The robot is only an interface. It streams microphone audio and camera video over WebRTC, plays speech and moves its head. Speech recognition, the language model, speech synthesis, vision and web search run on servers listed in a registry, each with failover.

**Status:** experimental. It's the code from [experiment 0001](../../docs/experiments/0001-reachy-voice-loop.md), and its boundaries are set by [ADR-0001](../../docs/decisions/0001-reachy-embodiment-adapter.md), which is still proposed. It doesn't use `majel/` yet, because there's nothing there to use.

## What it does

- Listens continuously. It responds only when addressed by name, or within a short follow-up window after its last reply.
- Streams the reply sentence by sentence, so speech starts before the language model has finished. In the experiment, replies began 0.9–1.7 s after the user stopped talking.
- Understands a few spoken commands itself: sleep and wake, "introduce yourself" (which lists the commands), and "what do you see?" (one camera frame, sent to a vision model, never stored).
- Optionally looks things up on the internet, read-only: search through a [SearXNG](https://docs.searxng.org) instance, and reading single public pages. Questions about news, weather, prices and other current facts are routed to search in code, because small models rarely choose the tool themselves.
- Can consult one peer agent through an OpenAI-compatible chat API. Requests are framed as read-only lookups, which is an instruction rather than enforcement; see ADR-0001.
- Follows faces with the robot's own on-board tracker, and gestures while listening, thinking and speaking. By default it does both only during a conversation, from being addressed by name until the follow-up window closes, and holds still otherwise (`STILL_WHEN_IDLE`).
- Routes each capability through an ordered list of servers and moves to the next when one is down (see [Servers and capabilities](#servers-and-capabilities)).
- Exits when the robot connection drops, so systemd can restart it. It keeps running, and keeps listening, when backend servers are unreachable.
- Includes a web console and a terminal console for settings and live control.

## Layout

| Path | Purpose |
|---|---|
| `bridge.py` | The voice loop, gestures, vision and the local control socket |
| `backends.py` | Server registry, probing, and capability routing with failover |
| `web.py` | Read-only search and page reading, with the public-address guard |
| `settings.py` | Deployment config, agent profile, the settings the consoles may change, backend discovery |
| `console.py`, `console` | Terminal console and one-shot commands |
| `panel.py`, `panel.html` | Web console |
| `run.sh` | Starts the bridge with the GStreamer plugin path set |
| `config.env.example` | Deployment config template |
| `backends.json.example` | Server registry template |
| `profile.example/` | A neutral example agent profile |
| `systemd/` | User unit templates |

## The parts of a deployment

1. **This code**, from the MAJEL checkout.
2. **A deployment config** (copied from `config.env.example`), kept outside the repo. Its location is set with `MAJEL_REACHY_CONFIG`. It holds the robot address, the service name, file locations and the tunable settings.
3. **A server registry** (copied from `backends.json.example`), usually beside the config. It lists servers and which of them serve each capability. API keys for servers that need one go in a separate secrets file the config points to.
4. **An agent profile**, kept outside the repo and private. It holds the agent's name and its misheard variants, owner, voice, introduction, persona, standing facts and, optionally, a peer agent. The peer's address and key go in a separate env file the profile points to. See `profile.example/profile.yaml`.

Never commit a real config or profile here. `.gitignore` already excludes `*.env` and token files.

## Install (Ubuntu 24.04)

System packages for GStreamer and for building PyGObject (the SDK pins PyGObject ≤ 3.46, which builds from source):

```bash
sudo apt install build-essential pkg-config python3-dev python3-venv libcairo2-dev libgirepository1.0-dev \
  gir1.2-gstreamer-1.0 gir1.2-gst-plugins-base-1.0 gir1.2-gst-plugins-bad-1.0 \
  gstreamer1.0-plugins-good gstreamer1.0-plugins-bad gstreamer1.0-nice \
  libgstreamer1.0-dev libgstreamer-plugins-base1.0-dev libgstreamer-plugins-good1.0-dev \
  libgstreamer-plugins-bad1.0-dev libssl-dev
```

Ubuntu doesn't package the Rust `webrtcsrc` element the SDK needs. Build it from gst-plugins-rs with a Rust toolchain from [rustup](https://rustup.rs) and [cargo-c](https://github.com/lu-zero/cargo-c) (about 7 minutes on two cores):

```bash
git clone --branch 0.14.5 --depth 1 https://gitlab.freedesktop.org/gstreamer/gst-plugins-rs.git
cd gst-plugins-rs
cargo cinstall -p gst-plugin-webrtc --prefix="$HOME/.local/gst-plugins-rs" --release
```

`run.sh` looks for the plugin there; set `MAJEL_REACHY_GST` to use another location.

Python dependencies, in a virtual environment outside the repo:

```bash
python3 -m venv ~/.local/share/majel-reachy/venv
~/.local/share/majel-reachy/venv/bin/pip install -r requirements.txt
```

## Configure and run

```bash
mkdir -p ~/.config/my-agent && cp -r profile.example/. ~/.config/my-agent/
cp config.env.example ~/.config/my-agent/reachy.env      # then edit these
cp backends.json.example ~/.config/my-agent/backends.json
export MAJEL_REACHY_CONFIG=~/.config/my-agent/reachy.env
export MAJEL_REACHY_PYTHON=~/.local/share/majel-reachy/venv/bin/python
./run.sh
```

To run it as a service, copy the templates in `systemd/` to `~/.config/systemd/user/`, fix the paths, and enable them. Run `loginctl enable-linger` so they start at boot without a login.

## Servers and capabilities

The registry has two parts.

**Servers** each have a name, a type and a URL, plus optionally the name of an environment variable holding an API key. The types are:

| Type | For | Example |
|---|---|---|
| `ollama` | conversation, vision | Ollama |
| `openai` | conversation, vision | vLLM, llama.cpp server, hosted OpenAI-compatible APIs |
| `speech` | speech to text | faster-whisper behind an OpenAI-compatible API (Speaches) |
| `voice` | text to speech | Kokoro-FastAPI |
| `search` | web search | SearXNG with the JSON format enabled |

**Capabilities** (`speech_to_text`, `text_generation`, `tool_answers`, `vision`, `text_to_speech`, `web_search`) each have an ordered list of entries, each naming a server and, where it applies, a model. A request goes to the first entry. If that server can't be reached, times out or returns a 5xx error, the request moves to the next entry, and the failed server is skipped for 30 seconds. A 4xx answer is treated as a real error, not an outage. The exception is vision, which moves on after any failure, because a server may answer but refuse images. Conversation fails over only before the first token: if a server dies mid-reply, that reply ends.

`tool_answers` is optional. It's the model for replies built on tool results (search results, pages, a peer agent's answer). In testing, a 4B chat model misread or argued with correct search results, while an 8B model answered from them reliably, so this lets lookups use a larger model while chat stays fast. When it's empty, `text_generation` is used.

An empty list turns a capability off. Both consoles can add, test and remove servers and reorder each list. If no registry exists yet, one is built from older single-address settings (`LLM_URL`, `STT_URL` and so on), if present.

This is a stand-in for `majel/capabilities`, which doesn't exist yet. It uses the same vocabulary so it can be replaced.

## Consoles

- **Terminal:** `./console` is interactive. `./console help` lists one-shot commands such as `say`, `ask`, `sleep`, `wake`, `volume 60`, `preview <voice>`, `set KEY VALUE`, `servers`, `server add NAME TYPE URL [KEY_ENV]` and `use CAPABILITY SERVER=MODEL …`.
- **Web:** `panel.py` serves on port 8765 on all interfaces. On first start it writes a random token to the `PANEL_TOKEN` file. Open `http://<host>:8765/?t=<token>` once; a cookie then keeps that browser signed in.

Both consoles change the deployment config and restart the service, and both can trigger speech. Live commands go over a Unix socket in `$XDG_RUNTIME_DIR` that only the service's user can open.

## Security notes

- The web console can change where audio, transcripts and camera frames are sent. Its token grants full control of the adapter, so keep it private. Firewall port 8765 to trusted machines if you can.
- Server API keys live only in the secrets file, never in the registry or the console. The registry names the variable; the console can't read or show the key.
- A peer agent's key in the profile is a credential for that agent's API. Keep the peer's API LAN-only and restricted to the adapter host. The read-only limit on peer requests is only an instruction.
- **One outside source per request.** Once a request has used the peer agent, web tools are unavailable for the rest of it, and once it has read web content, the peer is unavailable. This is enforced in code. It stops web pages from steering requests to an agent with more authority, and stops the peer's private answers from leaving through a search query or page URL. Across requests, earlier replies stay in the conversation history, so the separation isn't complete.
- Page reading refuses anything that doesn't resolve to a public internet address, re-checking every redirect, so links can't reach the robot, the console or other agents on the LAN. A host that changes its DNS answer between the check and the request could get past this.
- Search queries are the user's words, sent to SearXNG and from there to its upstream engines without cookies or accounts. Run SearXNG on the adapter host or LAN, not as a public instance.
- With `LOG_OVERHEARD=1`, everything the microphone hears is written to the journal as text, including speech not addressed to the agent.

## Known limitations

- Energy-based voice detection. Sustained background sound that starts within 15 s of speech merges with it.
- Half-duplex: you can't interrupt the agent while it's talking.
- Turning the camera off discards the video track but still decodes it, which roughly halves the bridge's CPU use rather than removing it.
- One profile per deployment, and English-only speech recognition settings.
- Small models summarize search results unreliably: they mostly get the facts right, but can garble dates or add details the results don't contain.
- With a 4,096-token context, a long persona leaves little room for web results. Results are capped (four search results, 1,500 characters per page), and older conversation turns are dropped from requests that use the web.
