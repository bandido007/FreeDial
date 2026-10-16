#!/usr/bin/env bash
# M4b: carriers (trunks) on Asterisk. Run once, after `make pjsip-schema` (Asterisk's role may then
# read ps_registrations, ps_endpoint_id_ips and queues_carrier_numbers):
#
#   cd ~/AlgoDial && sudo deploy/asterisk/m4b-carriers.sh
#
#  1. sorcery.conf + extconfig.conf: outbound registrations and IP identification from PostgreSQL,
#     like endpoints already are; the DID forms table as the realtime family "carrier_numbers".
#  2. extensions.conf: the [from-carrier] context. Only DIDs AlgoDial knows reach the brain (one
#     REALTIME_FIELD lookup); anything else is hung up, even with the brain down (POC_PLAN.md M4b, B).
#  3. manager.conf: AMI on 127.0.0.1:5038 only, one user (AMI_USER / AMI_PASSWORD in .env) that may
#     log in from 127.0.0.1 only and holds write=reporting: enough to list trunk registrations
#     (PJSIPShowRegistrationsOutbound), not to originate, run commands, reload or restart. Its
#     password goes in a git-ignored file (rule 9).
#
# Shows the diff, and only after you say yes commits in /etc/asterisk and restarts Asterisk (sorcery
# mappings are read at start). Saying no, or any failure, puts every file back.
set -euo pipefail

ETC=/etc/asterisk
HERE=$(cd "$(dirname "$0")" && pwd)
ENV_FILE=${ENV_FILE:-$HERE/../../.env}
GIT=(git -C "$ETC" -c safe.directory="$ETC")  # as root, like every earlier commit there
SECRET=manager_algodial.conf

undo() {
  "${GIT[@]}" checkout -- .
  rm -f "$ETC/$SECRET"
}

[ "$(id -u)" = 0 ] || { echo "Run with sudo."; exit 1; }
if [ -n "$("${GIT[@]}" status --porcelain)" ]; then
  echo "/etc/asterisk has uncommitted changes. Commit or stash them first:"; "${GIT[@]}" status --short; exit 1
fi
if grep -q '^\[from-carrier\]' "$ETC/extensions.conf"; then
  echo "Already applied: extensions.conf has [from-carrier]."; exit 0
fi
AMI_USER=$(grep -E '^AMI_USER=' "$ENV_FILE" | cut -d= -f2-)
AMI_PASSWORD=$(grep -E '^AMI_PASSWORD=' "$ENV_FILE" | cut -d= -f2-)
[ -n "$AMI_USER" ] && [ -n "$AMI_PASSWORD" ] || { echo "AMI_USER / AMI_PASSWORD are empty in $ENV_FILE"; exit 1; }
if ss -ltn | grep -q ':5038 '; then echo "Port 5038 is already in use."; exit 1; fi

trap 'echo "Failed: putting /etc/asterisk back."; undo' ERR

cat >> "$ETC/sorcery.conf" <<'EOF'

; AlgoDial M4b: carriers' outbound registrations and IP identification come from PostgreSQL too.
; Registrations are read when res_pjsip_outbound_registration loads; AlgoDial reloads it over ARI
; after a trunk changes.
[res_pjsip_outbound_registration]
registration=realtime,ps_registrations

[res_pjsip_endpoint_identifier_ip]
identify=realtime,ps_endpoint_id_ips
EOF

cat >> "$ETC/extconfig.conf" <<'EOF'
; AlgoDial M4b: trunks' registrations and identifies, and every form a carrier may send for a DID.
ps_registrations => pgsql,algodial
ps_endpoint_id_ips => pgsql,algodial
carrier_numbers => pgsql,algodial,queues_carrier_numbers
EOF

cat >> "$ETC/extensions.conf" <<'EOF'

; AlgoDial M4b: calls from carriers (trunk endpoints have context=from-carrier). Only DIDs AlgoDial
; knows reach the brain: carrier_numbers maps every form a carrier may send (+255..., 00255...,
; 255..., 0...) to the E.164 DID. Anything else is refused here, even when the brain is down.
[from-carrier]
exten => _[+0-9].,1,NoOp(Carrier call to ${EXTEN} on ${CHANNEL(endpoint)})
 same => n,Set(DID=${REALTIME_FIELD(carrier_numbers,form,${EXTEN},did)})
 same => n,GotoIf($["${DID}" = ""]?unknown)
 ; M7: the stereo recording, as on every 8xx call (left = the caller, right = what they hear)
 same => n,MixMonitor(${ASTSPOOLDIR}/recording/calls/${UNIQUEID}.raw,D)
 same => n,Stasis(algodial,inbound,${DID},${CHANNEL(endpoint)},${CHANNEL(linkedid)})
 same => n,GotoIf($["${STASISSTATUS}" = "FAILED"]?fallback)
 same => n,Hangup()
 ; AlgoDial rule 5: the brain is down, so a plain queue takes a known DID's call.
 same => n(fallback),NoOp(AlgoDial brain unavailable: plain Queue() fallback)
 same => n,Answer()
 same => n,Queue(support)
 same => n,Hangup()
 same => n(unknown),NoOp(${EXTEN} is not a DID AlgoDial knows: refused)
 same => n,Hangup(1)

exten => s,1,NoOp(Carrier call without a number: refused)
 same => n,Hangup(1)
EOF

sed -i -E 's/^enabled = no/enabled = yes/; s/^bindaddr = 0\.0\.0\.0/bindaddr = 127.0.0.1/' "$ETC/manager.conf"
cat >> "$ETC/manager.conf" <<EOF

; AlgoDial M4b: one AMI user for trunk health (registrations), from this machine only.
; Its secret is in $SECRET (not in git, rule 9).
#include $SECRET
EOF
(umask 027; cat > "$ETC/$SECRET" <<EOF
[$AMI_USER]
secret = $AMI_PASSWORD
deny = 0.0.0.0/0.0.0.0
permit = 127.0.0.1/255.255.255.255
read = reporting
write = reporting
EOF
)
chown asterisk:asterisk "$ETC/$SECRET"
grep -qx "$SECRET" "$ETC/.gitignore" 2>/dev/null || echo "$SECRET" >> "$ETC/.gitignore"

"${GIT[@]}" --no-pager diff
echo
read -r -p "Commit this and restart Asterisk? [y/N] " answer
if [ "$answer" != "y" ]; then
  undo
  echo "Nothing changed."; exit 1
fi
"${GIT[@]}" add -A
"${GIT[@]}" commit -q -m "M4b: carriers: registrations and identifies from PostgreSQL, [from-carrier] for known DIDs only, AMI on 127.0.0.1 for trunk health"
trap - ERR
"${GIT[@]}" log --oneline -1

systemctl restart asterisk
sleep 5
asterisk -rx "dialplan show from-carrier" | head -n 8
asterisk -rx "manager show settings" | grep -E "Manager \(AMI\)|Web Manager|TCP Bindaddress" || true
asterisk -rx "manager show user $AMI_USER" | head -n 6
