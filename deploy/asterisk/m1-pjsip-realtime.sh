#!/usr/bin/env bash
# M1: SIP accounts made in AlgoDial become PJSIP endpoints Asterisk reads live.
#
#   cd ~/AlgoDial && sudo deploy/asterisk/m1-pjsip-realtime.sh
#
# Writes res_pgsql.conf (+ a git-ignored secret file), adds the realtime lines to
# extconfig.conf and sorcery.conf, shows the diff, and only after you say yes
# commits in /etc/asterisk and restarts Asterisk (sorcery mappings are read at
# start). Saying no puts every file back.
set -euo pipefail

ETC=/etc/asterisk
HERE=$(cd "$(dirname "$0")" && pwd)
ENV_FILE=${ENV_FILE:-$HERE/../../.env}
# As root, like the earlier lab commits (their objects in .git are root's).
GIT=(git -C "$ETC" -c safe.directory="$ETC")
# The files this script writes. Leftovers of an earlier, interrupted run are reset first.
OURS=(res_pgsql.conf extconfig.conf sorcery.conf .gitignore res_pgsql_secret.conf)

undo() {
  "${GIT[@]}" reset -q --hard
  rm -f "$ETC/res_pgsql_secret.conf" "$ETC/.gitignore"
}

[ "$(id -u)" = 0 ] || { echo "Run with sudo."; exit 1; }
PASS=$(grep -E '^ASTERISK_DB_PASSWORD=' "$ENV_FILE" | cut -d= -f2-)
[ -n "$PASS" ] || { echo "ASTERISK_DB_PASSWORD is empty in $ENV_FILE"; exit 1; }

others=$("${GIT[@]}" status --porcelain --ignored | awk '{print $2}' | grep -vxF -f <(printf '%s\n' "${OURS[@]}") || true)
if [ -n "$others" ]; then
  echo "/etc/asterisk has changes this script did not make. Commit or stash them first:"; echo "$others"; exit 1
fi
undo  # clean start: drops only this script's own leftovers
trap 'echo "Failed: putting /etc/asterisk back."; undo' ERR

install -o asterisk -g asterisk -m 644 "$HERE/res_pgsql.conf" "$ETC/res_pgsql.conf"
(umask 027; printf 'dbpass=%s\n' "$PASS" > "$ETC/res_pgsql_secret.conf")
chown asterisk:asterisk "$ETC/res_pgsql_secret.conf"
cat "$HERE/extconfig.conf.append" >> "$ETC/extconfig.conf"
cat "$HERE/sorcery.conf.append" >> "$ETC/sorcery.conf"
install -o asterisk -g asterisk -m 644 /dev/null "$ETC/.gitignore"
echo 'res_pgsql_secret.conf' > "$ETC/.gitignore"

"${GIT[@]}" add -A
"${GIT[@]}" --no-pager diff --cached
echo
read -r -p "Commit these changes and restart Asterisk? [y/N] " answer
if [ "$answer" != "y" ]; then
  undo
  echo "Nothing changed."; exit 1
fi

"${GIT[@]}" commit -q -m "M1: PJSIP realtime from AlgoDial's PostgreSQL (endpoints, auths, aors)"
trap - ERR
"${GIT[@]}" log --oneline -1

systemctl restart asterisk
sleep 4
asterisk -rx "realtime show pgsql status"
asterisk -rx "pjsip show endpoints" | tail -n 4
