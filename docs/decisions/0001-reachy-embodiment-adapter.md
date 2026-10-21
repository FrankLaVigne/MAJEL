# ADR-0001: Reachy Mini embodiment adapter with a separate agent profile

- **Status:** Proposed
- **Date:** 2026-10-21

## Context

[Experiment 0001](../experiments/0001-reachy-voice-loop.md) built a working voice loop. In it, a Reachy Mini acts as the microphone, speaker, camera and head of an agent. Speech recognition, the language model and speech synthesis run on a separate inference host, and a bridge on MAJEL-CORE ties them together. A web and terminal console manages the bridge.

The prototype mixed two kinds of material in one codebase:

- **Generic embodiment code**: the robot connection, voice activity detection, turn-taking, gestures, face tracking, camera capture, the control socket and the console.
- **One agent's identity**: its name and the spellings speech recognition produces for it, persona, standing facts, voice, self-introduction, and the address and key of a peer agent it consults.

MAJEL's repository rules forbid committing any agent's persona, memories, credentials or private config. The generic part is useful to any agent that should have a physical voice. `adapters/` is where MAJEL places integrations with external runtimes, and the Reachy Mini is listed there as one.

The experiment also showed two places where the prototype works around missing MAJEL pieces:

- **Backends are configured by address.** Each of speech to text, language model, text to speech and vision is pointed at a server, with a hand-written fallback. That is the problem `majel/capabilities` is meant to solve.
- **Calls to a peer agent are direct.** The bridge holds a bearer token for the peer's full-authority API, and limits it to read-only lookups only by instruction. That is the problem `majel/policy` and `majel/routing` are meant to solve.

## Decision

1. **The Reachy bridge and its console become a generic adapter in `adapters/reachy/`.** The adapter contains no agent identity, credentials, hostnames or addresses. It ships an example config (`config.env.example`) and a neutral example profile.
2. **Agent identity lives in a separate profile that the adapter loads at runtime.** A profile is a directory outside this repository holding `profile.yaml` (name, name variants, owner, voice, introduction, optional peer agent), a persona prompt and standing facts. One adapter deployment runs one profile. The profile and the deployment config are private to the operator.
3. **Deployment config is separate from code.** The adapter reads its config from a path given in the `MAJEL_REACHY_CONFIG` environment variable. Backend addresses, the robot address, the service name and the console token location live there, not in code.
4. **For now the adapter runs on MAJEL-CORE as a systemd user service.** The web console listens on the LAN and requires a token. Live control uses a Unix socket readable only by the service's user.
5. **The following are recorded as direction, not yet decided in detail:**
   - Run the adapter as a container, so the WebRTC plugin build is reproducible and the workload is isolated from the control plane.
   - Replace per-backend addresses with capability requests once `majel/capabilities` exists.
   - Route peer-agent calls through MAJEL policy so that authority limits are enforced, not requested.

   Each needs its own ADR.

## Consequences

- **Easier:** Any agent can be given a Reachy body by writing a profile. The adapter can be reviewed, versioned and shared without exposing anyone's agent.
- **Easier:** Agent identity survives replacing the robot, the inference host or the adapter itself, consistent with "control plane ≠ compute plane."
- **Harder:** A deployment now has three parts to keep in step: adapter code, deployment config and the agent profile.
- **Risk, accepted for now:** The adapter processes household audio and video on the control-plane host. That co-locates a sensitive workload with the control plane. It is acceptable while MAJEL has no other services there. It should be revisited by the containerization ADR before MAJEL control-plane services are deployed on MAJEL-CORE.
- **Risk, accepted for now:** While peer calls stay direct, the adapter holds a credential for a peer agent's full API. The read-only limit is an instruction, not enforcement. Operators should keep the peer's API LAN-only and firewall it to the adapter host.
- **Risk:** The web console can change where the adapter sends audio, transcripts and camera frames. It requires a token, but that token grants full control of the adapter.

## Evidence

- [Experiment 0001: Reachy Mini as a voice interface for a remotely hosted agent](../experiments/0001-reachy-voice-loop.md)
