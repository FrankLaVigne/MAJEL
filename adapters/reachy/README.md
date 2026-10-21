# Reachy Mini adapter

Gives an agent a physical voice through a [Reachy Mini](https://github.com/pollen-robotics/reachy_mini) Wireless robot. The robot is only an interface. It streams microphone audio and camera video over WebRTC, plays speech and moves its head. Speech recognition, the language model, speech synthesis and vision run on servers named in the deployment config.

**Status:** experimental. It's the code from [experiment 0001](../../docs/experiments/0001-reachy-voice-loop.md), and its boundaries are set by [ADR-0001](../../docs/decisions/0001-reachy-embodiment-adapter.md), which is still proposed. It doesn't use `majel/` yet, because there's nothing there to use.

## What it does

- Listens continuously. It responds only when addressed by name, or within a short follow-up window after its last reply.
- Streams the reply sentence by sentence, so speech starts before the language model has finished. In the experiment, replies began 0.9–1.7 s after the user stopped talking.
- Understands a few spoken commands itself: sleep and wake, "introduce yourself" (which lists the commands), and "what do you see?" (one camera frame, sent to a vision model, never stored).
- Can consult one peer agent through an OpenAI-compatible chat API. Requests are framed as read-only lookups, which is an instruction rather than enforcement; see ADR-0001.
- Follows faces with the robot's own on-board tracker, and gestures while listening, thinking and speaking.
- Exits when the robot connection drops, so systemd can restart it.
- Includes a web console and a terminal console for settings and live control.

## Layout

| Path | Purpose |
|---|---|
| `bridge.py` | The voice loop, gestures, vision and the local control socket |
| `settings.py` | Deployment config, agent profile, the settings the consoles may change, backend discovery |
| `console.py`, `console` | Terminal console and one-shot commands |
| `panel.py`, `panel.html` | Web console |
| `run.sh` | Starts the bridge with the GStreamer plugin path set |
| `config.env.example` | Deployment config template |
| `profile.example/` | A neutral example agent profile |
| `systemd/` | User unit templates |

## The three parts of a deployment

1. **This code**, from the MAJEL checkout.
2. **A deployment config** (copied from `config.env.example`), kept outside the repo. Its location is set with `MAJEL_REACHY_CONFIG`. It holds server addresses, the robot address, the service name and the console token location.
3. **An agent profile**, kept outside the repo and private. It holds the agent's name and its misheard variants, owner, voice, introduction, persona, standing facts and, optionally, a peer agent. The peer's address and key go in a separate env file the profile points to. See `profile.example/profile.yaml`.

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
cp config.env.example ~/.config/my-agent/reachy.env      # then edit both
export MAJEL_REACHY_CONFIG=~/.config/my-agent/reachy.env
export MAJEL_REACHY_PYTHON=~/.local/share/majel-reachy/venv/bin/python
./run.sh
```

To run it as a service, copy the templates in `systemd/` to `~/.config/systemd/user/`, fix the paths, and enable them. Run `loginctl enable-linger` so they start at boot without a login.

## Consoles

- **Terminal:** `./console` is interactive. `./console help` lists one-shot commands such as `say`, `ask`, `sleep`, `wake`, `volume 60`, `preview <voice>` and `set KEY VALUE`.
- **Web:** `panel.py` serves on port 8765 on all interfaces. On first start it writes a random token to the `PANEL_TOKEN` file. Open `http://<host>:8765/?t=<token>` once; a cookie then keeps that browser signed in.

Both consoles change the deployment config and restart the service, and both can trigger speech. Live commands go over a Unix socket in `$XDG_RUNTIME_DIR` that only the service's user can open.

## Security notes

- The web console can change where audio, transcripts and camera frames are sent. Its token grants full control of the adapter, so keep it private. Firewall port 8765 to trusted machines if you can.
- A peer agent's key in the profile is a credential for that agent's API. Keep the peer's API LAN-only and restricted to the adapter host. The read-only limit on peer requests is only an instruction.
- With `LOG_OVERHEARD=1`, everything the microphone hears is written to the journal as text, including speech not addressed to the agent.

## Known limitations

- Energy-based voice detection. Sustained background sound that starts within 15 s of speech merges with it.
- Half-duplex: you can't interrupt the agent while it's talking.
- Turning the camera off discards the video track but still decodes it, which roughly halves the bridge's CPU use rather than removing it.
- One profile per deployment, and English-only speech recognition settings.
