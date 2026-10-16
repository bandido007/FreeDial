#!/usr/bin/env bash
# Restart Asterisk and make sure it is really back.
#
#   cd ~/AlgoDial && sudo deploy/asterisk/restart.sh
#
# Why not `systemctl restart asterisk`: Asterisk's LSB init script returns from "stop" before
# Asterisk has exited, so "start" finds it "already running", starts nothing, and once the old
# process ends nothing runs at all (16 Oct 2026, 16:03 and 16:59: no SIP, no ARI, every browser
# phone "not connected"). So: stop, wait until the process is gone, start, wait for the ports.
set -euo pipefail

[ "$(id -u)" = 0 ] || { echo "Run with sudo."; exit 1; }

systemctl stop asterisk || true
for _ in $(seq 1 30); do
  pgrep -x asterisk >/dev/null || break
  sleep 1
done
if pgrep -x asterisk >/dev/null; then
  echo "Asterisk has not stopped after 30 s; asking it once more, then ending it."
  asterisk -rx "core stop now" >/dev/null 2>&1 || true
  sleep 5
  pkill -x asterisk || true
  sleep 2
fi

systemctl start asterisk
for _ in $(seq 1 30); do
  if ss -ltn | grep -q ':8091 ' && ss -lun | grep -q ':5062 '; then
    asterisk -rx "core show uptime" | head -n 1
    echo "Asterisk is back: SIP on 5062, ARI and the browser phones' WebSocket on 8091."
    exit 0
  fi
  sleep 1
done
echo "Asterisk did not come back within 30 s. Its log: /var/log/asterisk/messages.log"
exit 1
