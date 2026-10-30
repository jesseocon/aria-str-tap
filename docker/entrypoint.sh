#!/bin/sh
set -eu
export DISPLAY="${DISPLAY:-:99}"
screen="${DISPLAY#:}"
socket="/tmp/.X11-unix/X${screen}"
# A cold container can take several seconds to bring the display up.
wait_seconds="${XVFB_WAIT_SECONDS:-30}"

start_xvfb() {
  Xvfb "$DISPLAY" -screen 0 1600x1000x24 -nolisten tcp >/tmp/xvfb.log 2>&1 &
  xvfb_pid=$!
}

# An exited Xvfb the shell has not reaped yet still answers kill -0.
xvfb_alive() {
  kill -0 "$xvfb_pid" 2>/dev/null || return 1
  ! grep -q '^State:[[:space:]]*Z' "/proc/$xvfb_pid/status" 2>/dev/null
}

# The runner reads the last line a failed container prints, so the reason goes last.
fail() {
  cat /tmp/xvfb.log >&2 || true
  echo "Xvfb did not start: $1" >&2
  exit 1
}

start_xvfb
restarted=0
i=0
while [ ! -S "$socket" ]; do
  if ! xvfb_alive; then
    [ "$restarted" -eq 0 ] || fail "it exited twice"
    echo "Xvfb exited before its display was ready; starting it again" >&2
    cat /tmp/xvfb.log >&2 || true
    rm -f "/tmp/.X${screen}-lock" "$socket"
    restarted=1
    start_xvfb
  fi
  i=$((i + 1))
  [ "$i" -le $((wait_seconds * 10)) ] || fail "no display after ${wait_seconds}s"
  sleep 0.1
done
if [ "$i" -gt 50 ]; then
  echo "Xvfb took $((i / 10))s to start" >&2
fi
exec python -m aria_runner "$@"
