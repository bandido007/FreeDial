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
GIT=(sudo -u asterisk git -C "$ETC" -c safe.directory="$ETC")

[ "$(id -u)" = 0 ] || { echo "Run with sudo."; exit 1; }
PASS=$(grep -E '^ASTERISK_DB_PASSWORD=' "$ENV_FILE" | cut -d= -f2-)
[ -n "$PASS" ] || { echo "ASTERISK_DB_PASSWORD is empty in $ENV_FILE"; exit 1; }
if [ -n "$("${GIT[@]}" status --porcelain)" ]; then
  echo "/etc/asterisk has uncommitted changes. Commit or stash them first:"; "${GIT[@]}" status --short; exit 1
fi

install -o asterisk -g asterisk -m 644 "$HERE/res_pgsql.conf" "$ETC/res_pgsql.conf"
umask 027
printf 'dbpass=%s\n' "$PASS" > "$ETC/res_pgsql_secret.conf"
chown asterisk:asterisk "$ETC/res_pgsql_secret.conf"
grep -q '^ps_endpoints' "$ETC/extconfig.conf" || cat "$HERE/extconfig.conf.append" >> "$ETC/extconfig.conf"
grep -q '^\[res_pjsip\]' "$ETC/sorcery.conf" || cat "$HERE/sorcery.conf.append" >> "$ETC/sorcery.conf"
grep -qx 'res_pgsql_secret.conf' "$ETC/.gitignore" 2>/dev/null || echo 'res_pgsql_secret.conf' >> "$ETC/.gitignore"
chown asterisk:asterisk "$ETC/.gitignore"

"${GIT[@]}" add -A
"${GIT[@]}" --no-pager diff --cached
echo
read -r -p "Commit these changes and restart Asterisk? [y/N] " answer
if [ "$answer" != "y" ]; then
  "${GIT[@]}" reset -q --hard
  rm -f "$ETC/res_pgsql_secret.conf"
  echo "Nothing changed."; exit 1
fi

AUTHOR=$("${GIT[@]}" log -1 --format='%an <%ae>')
"${GIT[@]}" -c user.name="${AUTHOR% <*}" -c user.email="$(echo "$AUTHOR" | sed -E 's/.*<(.*)>/\1/')" \
  commit -q -m "M1: PJSIP realtime from AlgoDial's PostgreSQL (endpoints, auths, aors)"
"${GIT[@]}" log --oneline -1

systemctl restart asterisk
sleep 4
asterisk -rx "realtime show pgsql status"
asterisk -rx "pjsip show endpoints" | tail -n 4
