#!/usr/bin/env bash
# Provision a development host; run with sudo as the intended developer.
set -euo pipefail
trap 'printf "ERROR: bootstrap failed at line %s\n" "$LINENO" >&2' ERR

die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
[[ $# -le 1 ]] || die "Usage: sudo bash $0 [developer-user]"
[[ $EUID -eq 0 ]] || die "Run with sudo or as root."
developer=${1:-${SUDO_USER:-}}
[[ -n $developer && $developer != root ]] || die "Specify a non-root developer user."
id "$developer" >/dev/null 2>&1 || die "User does not exist: $developer"
source /etc/os-release
[[ $ID == ubuntu && $VERSION_ID == 24.04 ]] || die "Ubuntu 24.04 is required."
arch=$(dpkg --print-architecture)
case $arch in
  amd64) node_arch=x64 ;;
  arm64) node_arch=arm64 ;;
  *) die "Supported host architectures: amd64 and arm64 (found $arch)." ;;
esac
[[ -d /run/systemd/system ]] || die "A host running systemd is required."

# Do not remove existing container runtimes or workloads automatically.
for package in docker.io docker-compose docker-compose-v2 docker-doc docker-buildx podman-docker containerd runc; do
  if [[ $(dpkg-query -W -f='${Status}' "$package" 2>/dev/null || true) == 'install ok installed' ]]; then
    die "Conflicting package $package is installed. Review and remove it before retrying."
  fi
done

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y git curl ca-certificates python3 xz-utils

work=$(mktemp -d)
trap 'rm -rf -- "$work"' EXIT
# Resolve the newest LTS from Node's official release metadata, not Ubuntu's older nodejs package.
curl --fail --silent --show-error --location https://nodejs.org/dist/index.json -o "$work/index.json"
node_version=$(python3 - "$work/index.json" <<'PY'
import json, re, sys
releases = json.load(open(sys.argv[1]))
versions = [r['version'] for r in releases if r.get('lts') and re.fullmatch(r'v\d+\.\d+\.\d+', r['version'])]
print(max(versions, key=lambda v: tuple(map(int, v[1:].split('.')))))
PY
)
node_dir="/usr/local/lib/nodejs/node-${node_version}-linux-${node_arch}"
if [[ ! -x $node_dir/bin/node || ! -x $node_dir/bin/npm ]]; then
  archive="node-${node_version}-linux-${node_arch}.tar.xz"
  base="https://nodejs.org/dist/${node_version}"
  curl --fail --silent --show-error --location "$base/$archive" -o "$work/$archive"
  curl --fail --silent --show-error --location "$base/SHASUMS256.txt" -o "$work/SHASUMS256.txt"
  (cd "$work"; awk -v file="$archive" '$2 == file { print; found=1 } END { if (!found) exit 1 }' SHASUMS256.txt > selected.sha256; sha256sum --check selected.sha256)
  tar -xJf "$work/$archive" -C "$work"
  install -d -m 0755 /usr/local/lib/nodejs
  [[ ! -e $node_dir ]] || die "Incomplete Node installation at $node_dir; review before retrying."
  mv "$work/node-${node_version}-linux-${node_arch}" "$node_dir"
fi
for tool in node npm npx; do
  target="/usr/local/bin/$tool"
  [[ ! -e $target || -L $target ]] || die "Refusing to replace existing non-symlink $target."
  ln -sfn "$node_dir/bin/$tool" "$target"
done

install -d -m 0755 /etc/apt/keyrings
curl --fail --silent --show-error --location https://download.docker.com/linux/ubuntu/gpg -o "$work/docker.asc"
install -m 0644 "$work/docker.asc" /etc/apt/keyrings/docker.asc
cat > /etc/apt/sources.list.d/docker.sources <<EOF
Types: deb
URIs: https://download.docker.com/linux/ubuntu
Suites: noble
Components: stable
Architectures: $arch
Signed-By: /etc/apt/keyrings/docker.asc
EOF
apt-get update
apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
systemctl enable --now docker.service containerd.service
getent group docker >/dev/null || groupadd docker
usermod -aG docker "$developer"

# Preserve ownership and contents of an existing directory or checkout.
if [[ ! -e /opt/majel ]]; then
  install -d -m 0755 -o "$developer" -g "$(id -gn "$developer")" /opt/majel
else
  [[ -d /opt/majel && ! -L /opt/majel ]] || die "/opt/majel must be a real directory."
  printf 'Preserving existing /opt/majel ownership and contents.\n'
fi

printf '\nDevelopment/admin tools (checked in the developer login PATH):\n'
for tool in codex claude; do
  if runuser -u "$developer" -- bash -lc 'command -v "$1" >/dev/null 2>&1' bash "$tool"; then
    printf 'Detected %s.\n' "$tool"
  else
    printf 'Optional %s is missing; see README.md for installation guidance.\n' "$tool"
  fi
done
printf '\nBaseline installed. Log out and back in to activate Docker group membership.\n'
printf 'The docker group grants root-equivalent access. Run verify.sh as %s, without sudo.\n' "$developer"
printf 'Codex: https://developers.openai.com/codex/cli/ (npm install -g @openai/codex in a user-owned npm prefix).\n'
printf 'Claude Code: https://code.claude.com/docs/en/setup (review the native installer).\n'
