#!/bin/sh
# Cloud Run has no display. Xvfb provides one so Chromium can run headed.
set -eu
export DISPLAY="${DISPLAY:-:99}"
screen="${DISPLAY#:}"
Xvfb "$DISPLAY" -screen 0 1600x1000x24 -nolisten tcp >/tmp/xvfb.log 2>&1 &
i=0
while [ ! -S "/tmp/.X11-unix/X${screen}" ]; do
  i=$((i + 1))
  if [ "$i" -gt 50 ]; then
    echo "Xvfb did not start" >&2
    cat /tmp/xvfb.log >&2 || true
    exit 1
  fi
  sleep 0.1
done
exec python -m aria_runner "$@"
