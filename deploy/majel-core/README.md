# MAJEL-CORE development host

These scripts provision and inspect an Ubuntu 24.04 Server development host
(amd64 or arm64) with systemd. They do not deploy the application.

## Bootstrap

Run from a checkout as the intended developer:

```bash
sudo bash deploy/majel-core/bootstrap.sh
# When running directly as root, specify an existing non-root account:
sudo bash deploy/majel-core/bootstrap.sh frank
```

Bootstrap installs Git, curl, CA certificates, Python 3 and xz utilities;
the newest Node.js LTS from official release metadata with npm and npx;
and Docker Engine, containerd, Compose v2 and Buildx from Docker's official
signed apt repository. Node archives are checked against the official SHA-256
manifest retrieved over HTTPS. Node lives under `/usr/local/lib/nodejs`, with
links in `/usr/local/bin`. Ensure `/usr/local/bin` is in your PATH.

It enables and starts Docker and containerd, adds the developer to the Docker
group, and creates `/opt/majel` owned by that user. Existing directory contents
and ownership are preserved. Log out and back in before checking Docker access.
Docker group membership grants root-equivalent host access; published container
ports can bypass UFW rules.

Reruns reuse an installed Node release, maintain the same repository configuration
and group membership, and preserve checkouts and Docker data. Package installation
can upgrade the managed packages, and a newer LTS release can replace the active
Node links. Old Node installations remain on disk. Existing conflicting Docker
packages cause an explicit stop rather than automatic removal. An existing
non-symlink Node executable in `/usr/local/bin` also causes a stop. The script
requires network access and root privileges; it does not perform a full OS upgrade,
change firewall/SSH settings, clone a repository, or configure credentials.

Clone MAJEL separately using your chosen GitHub authentication method, for example:

```bash
git clone https://github.com/FrankLaVigne/MAJEL.git /opt/majel
```

The destination must be empty for that command. For an existing checkout, preserve
it and pass its location to verification.

## Development/admin tools

Codex CLI and Claude Code are optional developer/admin tools. Bootstrap detects
them in the developer's login PATH; verification detects them in the current PATH.
Neither script installs them, invokes login, reads authentication state, or creates
tokens or credentials. Verification only detects these executables, avoiding
possible startup side effects from invoking them.

For Codex, use a user-owned npm prefix rather than a root-owned global install:

```bash
npm config set prefix "$HOME/.local"
export PATH="$HOME/.local/bin:$PATH"
npm install -g @openai/codex
```

Persist that PATH in your shell configuration if needed. Review the official
[Codex CLI instructions](https://developers.openai.com/codex/cli/) and
[Claude Code native installer instructions](https://code.claude.com/docs/en/setup)
before installing. Authentication is a separate, explicit action performed by the
developer after installation.

## Read-only verification

Run as the developer, **without sudo**, after logging back in:

```bash
bash deploy/majel-core/verify.sh
bash deploy/majel-core/verify.sh /path/to/MAJEL
```

Each check prints PASS, WARN or FAIL. Exit status is 0 when no check fails, 1 for
configuration failures, or 2 for invalid arguments. Warnings are informational:
optional admin tools, an uncloned repository, local changes, detached HEAD or a
missing origin do not fail the baseline.

Checks cover Ubuntu version, architecture, required executable versions, Node's
LTS designation, Compose v2, Buildx, Docker packages/repository/key, Docker service
activity and startup enablement, local Docker access without sudo, `/opt/majel`,
and the selected repository's HEAD, branch, working tree and origin presence.
Root execution warns that developer Docker permissions cannot be verified.

Verification makes no configuration changes, installs nothing, does not use sudo,
does not pull or run containers, and does not fetch or update Git. Git optional
locks are disabled to avoid index refresh writes. Docker access checks query only
the local `/var/run/docker.sock`, regardless of a configured remote context.
It does not test network connectivity, authentication, Node support expiry or
whether newer releases are available. Tool versions are reported for baseline
tools; Codex and Claude are only detected. Repository URLs are not printed.

Installation references: [Docker on Ubuntu](https://docs.docker.com/engine/install/ubuntu/)
and [Node.js downloads](https://nodejs.org/en/download).
