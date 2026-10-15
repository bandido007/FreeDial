#!/usr/bin/env bash
# The Zoiper phones 1001 and 1002 move from pjsip.conf to AlgoDial's database, like every
# other account (admin -> SIP accounts). Run `make agent-login` first: it writes their rows
# with the passwords they already have, so the phones keep working unchanged.
#
#   cd ~/AlgoDial && make agent-login && sudo deploy/asterisk/phones-in-database.sh
#
# A script, not a patch: a patch would have to carry the removed lines, passwords included,
# into AlgoDial's git (rule 9). Shows the diff, and only after you say yes commits in
# /etc/asterisk and reloads PJSIP. Saying no, or any failure, puts every file back.
set -euo pipefail

ETC=/etc/asterisk
GIT=(git -C "$ETC" -c safe.directory="$ETC")  # as root, like every earlier commit there
PHONES=(1001 1002)

[ "$(id -u)" = 0 ] || { echo "Run with sudo."; exit 1; }
if [ -n "$("${GIT[@]}" status --porcelain)" ]; then
  echo "/etc/asterisk has uncommitted changes. Commit or stash them first:"
  "${GIT[@]}" status --short
  exit 1
fi
if ! grep -qE '^\[(1001|1002)\]' "$ETC/pjsip.conf"; then
  echo "Already applied: pjsip.conf has no 1001 or 1002."; exit 0
fi
for id in "${PHONES[@]}"; do  # never leave a phone with no account at all
  asterisk -rx "realtime load ps_auths id $id" | grep -q password_digest \
    || { echo "Asterisk cannot read $id from the database yet. Run 'make agent-login' first."; exit 1; }
done

trap 'echo "Failed: putting /etc/asterisk back."; "${GIT[@]}" checkout -- .' ERR
# Drop every [1001] / [1002] section of pjsip.conf and the "; ===== 100x: ..." line above it.
awk '
  /^; =====/            { if ($0 ~ /^; ===== 100[12]:/) { skip = 1; next } skip = 0 }
  /^\[/                 { skip = ($0 ~ /^\[100[12]\]/) }
  !skip
' "$ETC/pjsip.conf" > "$ETC/pjsip.conf.new"
cat >> "$ETC/pjsip.conf.new" <<'EOF'
; 1001 (Zoiper on the laptop) and 1002 (Zoiper on the phone) live in AlgoDial's database
; (admin -> SIP accounts; ps_endpoints / ps_auths / ps_aors), like every other account.
EOF
cat -s "$ETC/pjsip.conf.new" > "$ETC/pjsip.conf"
rm "$ETC/pjsip.conf.new"
sed -i \
  -e 's/^; AlgoDial: PJSIP objects from pjsip.conf first (1001, 1002, transports),$/; AlgoDial: PJSIP objects from pjsip.conf first (the transports),/' \
  -e 's/^; then from PostgreSQL (accounts made in AlgoDial)\. /; then from PostgreSQL (every account, 1001 and 1002 included). /' \
  "$ETC/sorcery.conf"

"${GIT[@]}" --no-pager diff
echo
read -r -p "Commit this and reload PJSIP? [y/N] " answer
if [ "$answer" != "y" ]; then
  "${GIT[@]}" checkout -- .
  echo "Nothing changed."; exit 1
fi
"${GIT[@]}" commit -q -am "Phones 1001 and 1002 now come from AlgoDial's database, like every other account"
trap - ERR
"${GIT[@]}" log --oneline -1

asterisk -rx "module reload res_pjsip.so"
sleep 2
for id in "${PHONES[@]}"; do
  asterisk -rx "pjsip show endpoint $id" | grep -E "Endpoint:|Contact:|callerid " || true
done
echo "If a Zoiper shows 'not registered', press its register button once."
