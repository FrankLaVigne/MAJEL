# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project state

MAJEL (Multi-Agent Junction & Execution Layer) is a **Day Zero scaffold**: an architectural thesis for a control plane that coordinates specialized AI agents without sharing credentials between them. There is no application code yet. There is no build, lint, or test tooling. `majel/`, `adapters/`, `deploy/docker/` and `tests/` contain only `.gitkeep`, and `compose.yaml` defines `services: {}` on purpose.

Don't invent tooling, frameworks, services, or APIs that aren't here. Per `compose.yaml`, services and dependencies are added only after an ADR in `docs/decisions/` justifies them.

The only executable code is the MAJEL-CORE host scripts in `deploy/majel-core/`. These target Ubuntu 24.04 (amd64/arm64) with systemd:

```bash
sudo bash deploy/majel-core/bootstrap.sh [developer-user]   # provision: Node LTS, Docker CE, /opt/majel
bash deploy/majel-core/verify.sh [repo-path]                # read-only check, run WITHOUT sudo; default repo /opt/majel
```

Keep the constraints documented in `deploy/majel-core/README.md` intact when editing these scripts:
- `verify.sh` must stay strictly read-only. That means no sudo, no downloads, no container runs, no git fetch, and `GIT_OPTIONAL_LOCKS=0`. It prints PASS/WARN/FAIL and exits 0 (no failures), 1 (failures) or 2 (bad args).
- `bootstrap.sh` must stay idempotent. It stops rather than removing conflicting packages or overwriting non-symlink binaries, and it preserves existing `/opt/majel` contents. It verifies Node archives against the official SHA256 manifest.
- Neither script installs or authenticates Codex or Claude Code. They only detect them.
- The README describes the scripts' behavior in detail. Update it when behavior changes.

## Architecture (intended, not implemented)

The README is the source of truth for the architectural principles. The ones that shape any code or docs you write:
- **Agents exchange results, not credentials.** An orchestrator never inherits a specialist's credentials.
- **Control plane ≠ compute plane.** Agent identity and state must survive the loss of an inference host.
- **Request capabilities, not machines.** For example, `speech_to_text` is resolved by routing to a provider.
- **Containers are workloads; VMs, networks, or hosts are trust boundaries.** Don't co-locate workloads across trust levels for convenience.
- **Progressive authority:** observe → notify → recommend → act with approval → autonomous.

The intended package layout under `majel/` is `core` (identity/lifecycle/state), `routing`, `agents` (registration), `capabilities` (vocabulary and provider matching), and `policy` (authorization and progressive authority). External runtimes (Hermes Agent, OpenClaw, Reachy Mini, compute providers) integrate through `adapters/`.

**Bailey is not MAJEL.** Bailey, Herman, Clawdia and Worf are a personal reference implementation built on top of MAJEL. Never add their memories, credentials, persona prompts, or private config to this repo. MAJEL itself stays generic.

## Documentation conventions

- Label anything proposed or speculative as such. Docs describe only what exists or what has been decided. Keep the tone plain and hype-free (see "Voice" in `docs/brand/README.md`).
- **ADRs** go in `docs/decisions/NNNN-short-title.md`. Required fields: Title, Status, Date, Context, Decision, Consequences, Evidence. Accepted ADRs are immutable; supersede them, don't edit them. Update the Index in that README.
- **Experiments** go in `docs/experiments/NNNN-short-title.md` or a directory. Required sections: Hypothesis, Architecture/configuration, Procedure, Measurements, Observations, Failures, Conclusions, Resulting architectural changes. Sanitize logs; never include credentials, hostnames, or personal data.
- **Naming:** "MAJEL" in all caps in prose, `majel` in code identifiers.
- **Mermaid diagrams:** use the theme block and `classDef`s from `docs/brand/README.md`. Blue marks control, routing and agents; gold marks providers and execution; dashed Steel marks trust boundaries. Don't set `fontFamily`, because it causes a serif fallback. The README diagram is the working example.

## Secrets

`.gitignore` is deliberately broad. It excludes env files, keys and certs, and anything matching `*secret*` / `*credential*` / `*token*`. It also excludes agent state (`state/`, `memory/`, `data/`, `*.db`), model weights, and `compose.override.*`. Example configs must use an `.example` suffix and contain no real values.
