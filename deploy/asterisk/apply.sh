#!/usr/bin/env bash
# Apply AlgoDial patches to /etc/asterisk, one step at a time: show it, ask, commit, reload.
#
#   cd ~/AlgoDial && sudo deploy/asterisk/apply.sh m2 m3
#
# Each step is a <step>-*.patch here plus its lines below (commit message, how Asterisk
# takes the change, what to show after). Saying no, or any failure, puts every file back.
# A step already applied is skipped.
set -euo pipefail

ETC=/etc/asterisk
HERE=$(cd "$(dirname "$0")" && pwd)
GIT=(git -C "$ETC" -c safe.directory="$ETC")  # as root, like every earlier commit there

declare -A MESSAGE RELOAD SHOW
MESSAGE[m2]="M2: 800 passes the linkedid to AlgoDial, and falls back to Queue(support) when the brain is down"
RELOAD[m2]="dialplan reload"
SHOW[m2]="dialplan show 800@lab"
MESSAGE[m3]="M3: transport-ws, so browser phones (WebRTC) register over the HTTP server's /ws"
RELOAD[m3]="restart"  # PJSIP does not reliably add a transport on reload
SHOW[m3]="pjsip show transports"

[ "$(id -u)" = 0 ] || { echo "Run with sudo."; exit 1; }
[ $# -gt 0 ] || { echo "usage: sudo $0 <step>...   (steps: ${!MESSAGE[*]})"; exit 1; }

apply_step() {
  local step=$1 patch answer
  [ -n "${MESSAGE[$step]:-}" ] || { echo "Unknown step $step (steps: ${!MESSAGE[*]})"; exit 1; }
  patch=$(find "$HERE" -maxdepth 1 -name "$step-*.patch" | head -n1)
  [ -n "$patch" ] || { echo "No $step-*.patch in $HERE"; exit 1; }
  echo "── $step: $(basename "$patch")"

  if [ -n "$("${GIT[@]}" status --porcelain)" ]; then
    echo "/etc/asterisk has uncommitted changes. Commit or stash them first:"
    "${GIT[@]}" status --short
    exit 1
  fi
  if "${GIT[@]}" apply --reverse --check "$patch" 2>/dev/null; then
    echo "Already applied."; return
  fi

  trap 'echo "Failed: putting /etc/asterisk back."; "${GIT[@]}" checkout -- .' ERR
  "${GIT[@]}" apply "$patch"
  "${GIT[@]}" --no-pager diff
  echo
  read -r -p "Commit this and $( [ "${RELOAD[$step]}" = restart ] && echo "restart Asterisk" || echo "run '${RELOAD[$step]}'")? [y/N] " answer
  if [ "$answer" != "y" ]; then
    "${GIT[@]}" checkout -- .
    echo "Nothing changed."; exit 1
  fi
  "${GIT[@]}" commit -q -am "${MESSAGE[$step]}"
  trap - ERR
  "${GIT[@]}" log --oneline -1

  if [ "${RELOAD[$step]}" = restart ]; then
    systemctl restart asterisk
    sleep 4
  else
    asterisk -rx "${RELOAD[$step]}"
  fi
  asterisk -rx "${SHOW[$step]}"
}

for step in "$@"; do
  apply_step "$step"
done
