#!/usr/bin/env bash
# Start the bridge. Ctrl+C (or SIGTERM) puts the robot back to sleep.
#   MAJEL_REACHY_CONFIG   deployment config (default: ./config.env)
#   MAJEL_REACHY_PYTHON   interpreter with requirements.txt installed (default: python3)
#   MAJEL_REACHY_GST      directory holding the gst-plugins-rs webrtc plugin
set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")"
gst=${MAJEL_REACHY_GST:-$HOME/.local/gst-plugins-rs/lib/$(uname -m)-linux-gnu/gstreamer-1.0}
export GST_PLUGIN_PATH="$gst${GST_PLUGIN_PATH:+:$GST_PLUGIN_PATH}"
exec "${MAJEL_REACHY_PYTHON:-python3}" -u bridge.py "$@" 2> >(grep --line-buffered -v -i -E 'warn|USB device found' >&2)
