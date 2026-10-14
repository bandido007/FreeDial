#!/usr/bin/env bash
# Apply one AlgoDial patch to /etc/asterisk: show it, ask, commit, reload.
#
#   cd ~/AlgoDial && sudo deploy/asterisk/apply.sh m2
#
# Each step is a NAME.patch here plus a line in STEPS below (commit message and the
# CLI command that loads the change). Saying no, or any failure, puts every file back.
set -euo pipefail

ETC=/etc/asterisk
HERE=$(cd "$(dirname "$0")" && pwd)
GIT=(git -C "$ETC" -c safe.directory="$ETC")  # as root, like every earlier commit there

declare -A MESSAGE RELOAD SHOW
MESSAGE[m2]="M2: 800 passes the linkedid to AlgoDial, and falls back to Queue(support) when the brain is down"
RELOAD[m2]="dialplan reload"
SHOW[m2]="dialplan show 800@lab"

step=${1:-}
[ -n "$step" ] && [ -n "${MESSAGE[$step]:-}" ] || { echo "usage: sudo $0 <step>   (steps: ${!MESSAGE[*]})"; exit 1; }
[ "$(id -u)" = 0 ] || { echo "Run with sudo."; exit 1; }
PATCH=$(find "$HERE" -maxdepth 1 -name "$step-*.patch" | head -n1)
[ -n "$PATCH" ] || { echo "No $step-*.patch in $HERE"; exit 1; }

if [ -n "$("${GIT[@]}" status --porcelain)" ]; then
  echo "/etc/asterisk has uncommitted changes. Commit or stash them first:"
  "${GIT[@]}" status --short
  exit 1
fi
if "${GIT[@]}" apply --reverse --check "$PATCH" 2>/dev/null; then
  echo "Already applied: $(basename "$PATCH")"; exit 0
fi

trap 'echo "Failed: putting /etc/asterisk back."; "${GIT[@]}" checkout -- .' ERR
"${GIT[@]}" apply "$PATCH"
"${GIT[@]}" --no-pager diff
echo
read -r -p "Commit this and run '${RELOAD[$step]}'? [y/N] " answer
if [ "$answer" != "y" ]; then
  "${GIT[@]}" checkout -- .
  echo "Nothing changed."; exit 1
fi
"${GIT[@]}" commit -q -am "${MESSAGE[$step]}"
trap - ERR
"${GIT[@]}" log --oneline -1

asterisk -rx "${RELOAD[$step]}"
asterisk -rx "${SHOW[$step]}"
