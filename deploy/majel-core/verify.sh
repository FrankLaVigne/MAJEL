#!/usr/bin/env bash
# Read-only: no sudo, downloads, containers, builds, Git refreshes, or credentials.
set -uo pipefail
export GIT_OPTIONAL_LOCKS=0
passes=0 warns=0 fails=0
report() {
  printf '%-4s %s\n' "$1" "$2"
  case $1 in PASS) passes=$((passes+1));; WARN) warns=$((warns+1));; FAIL) fails=$((fails+1));; esac
}
if [[ $# -gt 1 || ${1:-} == -* ]]; then
  printf 'Usage: bash %s [repository-path] (default: /opt/majel)\n' "$0" >&2
  exit 2
fi
repo=${1:-/opt/majel}
if [[ -r /etc/os-release ]]; then
  source /etc/os-release
  if [[ ${ID:-} == ubuntu && ${VERSION_ID:-} == 24.04 ]]; then
    report PASS "OS: ${PRETTY_NAME:-Ubuntu 24.04}"
  else
    report FAIL "OS: ${PRETTY_NAME:-unknown}; expected Ubuntu 24.04"
  fi
else
  report FAIL 'Cannot read /etc/os-release'
fi
arch=$(uname -m)
case $arch in x86_64|aarch64) report PASS "Architecture: $arch";; *) report FAIL "Unsupported architecture: $arch";; esac
for tool in git curl node npm docker; do
  if command -v "$tool" >/dev/null 2>&1; then
    if version=$("$tool" --version 2>&1); then
      report PASS "$tool: ${version%%$'\n'*}"
    else
      report FAIL "$tool version command failed: $version"
    fi
  else
    report FAIL "$tool missing from PATH"
  fi
done
# Node exposes its release LTS designation without fetching release metadata.
if command -v node >/dev/null 2>&1; then
  if lts=$(node -p 'process.release.lts || ""' 2>/dev/null) && [[ -n $lts ]]; then
    report PASS "Node LTS release: $lts (support expiry is not checked offline)"
  else
    report FAIL 'Node is not an LTS release or cannot run'
  fi
fi
if command -v docker >/dev/null 2>&1; then
  for plugin in compose buildx; do
    if version=$(docker "$plugin" version 2>&1); then
      if [[ $plugin == compose && ! $version =~ version[[:space:]]+v?2\. ]]; then
        report FAIL "Expected Compose v2: $version"
      else
        report PASS "$version"
      fi
    else
      report FAIL "Docker $plugin unavailable: $version"
    fi
  done
fi
if command -v systemctl >/dev/null 2>&1; then
  for state in active enabled; do
    if systemctl "is-$state" --quiet docker.service 2>/dev/null; then
      report PASS "Docker daemon is $state"
    else
      report FAIL "Docker daemon is not $state (or systemd is unavailable)"
    fi
  done
else
  report FAIL 'systemctl unavailable; cannot check Docker daemon'
fi
if [[ $EUID -eq 0 ]]; then
  report WARN 'Running as root; Docker access without sudo cannot be verified. Rerun as the developer.'
elif command -v docker >/dev/null 2>&1; then
  # Force the local Engine socket; a remote context must not mask broken host access.
  if version=$(timeout 10s docker --host unix:///var/run/docker.sock info --format '{{.ServerVersion}}' 2>&1); then
    report PASS "Local Docker access without sudo; server $version"
  else
    report FAIL "Local Docker access without sudo failed: $version"
  fi
fi
if command -v dpkg-query >/dev/null 2>&1; then
  for package in docker-ce docker-ce-cli containerd.io docker-compose-plugin docker-buildx-plugin; do
    if version=$(dpkg-query -W -f='${Status} ${Version}' "$package" 2>/dev/null) && [[ $version == 'install ok installed '* ]]; then
      report PASS "Package $package: ${version#install ok installed }"
    else
      report FAIL "Expected official Docker package missing: $package"
    fi
  done
fi
if [[ -r /etc/apt/sources.list.d/docker.sources ]] && [[ -r /etc/apt/keyrings/docker.asc ]]; then
  # Bash-only check: no extra dependency or repository update.
  source_text=$(</etc/apt/sources.list.d/docker.sources)
  if [[ $source_text == *'URIs: https://download.docker.com/linux/ubuntu'* && $source_text == *'Suites: noble'* && $source_text == *'Signed-By: /etc/apt/keyrings/docker.asc'* ]]; then
    report PASS 'Docker official apt repository and readable key configured'
  else
    report FAIL 'Docker apt repository does not match expected configuration'
  fi
else
  report FAIL 'Docker apt repository or signing key missing'
fi
if [[ -d /opt/majel ]]; then
  report PASS '/opt/majel exists'
  [[ -w /opt/majel ]] && report PASS '/opt/majel is writable by current user' || report WARN '/opt/majel is not writable by current user'
else
  report FAIL '/opt/majel directory missing'
fi
if command -v git >/dev/null 2>&1 && [[ -d $repo ]] &&
   top=$(git -C "$repo" rev-parse --show-toplevel 2>/dev/null) &&
   [[ $(realpath "$repo") == "$top" ]]; then
  report PASS "MAJEL repository: $top"
  if head=$(git -C "$repo" log -1 --format='%h %s' 2>/dev/null); then
    report PASS "Repository HEAD: $head"
  else
    report WARN 'Repository has no readable commit'
  fi
  branch=$(git -C "$repo" symbolic-ref --short -q HEAD 2>/dev/null || true)
  [[ -n $branch ]] && report PASS "Branch: $branch" || report WARN 'Detached HEAD'
  if status=$(git -c core.fsmonitor=false -c core.untrackedCache=false -C "$repo" status --porcelain --untracked-files=normal 2>&1); then
    [[ -z $status ]] && report PASS 'Repository working tree clean' || report WARN "Repository has local changes: $status"
  else
    report FAIL "Cannot read repository status: $status"
  fi
  git -C "$repo" remote get-url origin >/dev/null 2>&1 && report PASS 'Origin remote configured (connectivity not checked)' || report WARN 'No origin remote configured'
else
  report WARN "No readable Git repository rooted at $repo; clone MAJEL separately or pass its path"
fi
for tool in codex claude; do
  # Detect only: do not invoke admin tools or inspect authentication state.
  if path=$(command -v "$tool" 2>/dev/null); then
    report PASS "Optional admin tool $tool detected: $path (version/authentication not inspected)"
  else
    report WARN "Optional admin tool $tool missing; see README.md for installation guidance"
  fi
done
printf '\nSummary: %s PASS, %s WARN, %s FAIL\n' "$passes" "$warns" "$fails"
(( fails == 0 ))
