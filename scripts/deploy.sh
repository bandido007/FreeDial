#!/usr/bin/env bash
# ════════════════════════════════════════════════════════════════════════════
#  Release on a bare-metal host. Run on the server, as the app user:
#
#      scripts/deploy.sh                # deploy the checked-out branch's newest commit
#      scripts/deploy.sh v1.4.0         # deploy a tag / branch / commit
#
#  What it does, in order — every step is idempotent, so a failed run is
#  simply run again:
#    1. fetch + check out the ref            5. collectstatic
#    2. install the hashed requirements      6. migrate, then seed (catalogues)
#    3. production settings dry run          7. restart web (graceful), worker, beat
#       (refuses insecure configuration)     8. health check, or roll the code back
#    4. Django deployment checks
#
#  Tunables (environment or .env): APP_DIR (default: repo root), APP_SLUG
#  (default: from .env PROJECT_SLUG, else "algodial"), HEALTH_URL, WITH_LDAP=1.
# ════════════════════════════════════════════════════════════════════════════
set -euo pipefail

APP_DIR="${APP_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
cd "$APP_DIR"

ref="${1:-}"
slug="${APP_SLUG:-$(grep -E '^PROJECT_SLUG=' .env 2>/dev/null | cut -d= -f2 | tr -d '"' || true)}"
slug="${slug:-algodial}"
health="${HEALTH_URL:-http://127.0.0.1:8000/readyz}"
python="$APP_DIR/venv/bin/python"
export DJANGO_SETTINGS_MODULE=config.settings.production

say()  { printf '\n\033[1m▶ %s\033[0m\n' "$*"; }
fail() { printf '\n\033[31m✖ %s\033[0m\n' "$*" >&2; exit 1; }

[ -f .env ] || fail ".env is missing in $APP_DIR — copy .env.example and fill the REQUIRED values"

# ── 1. Code ──────────────────────────────────────────────────────────────────
previous="$(git rev-parse HEAD)"
if [ -n "$ref" ]; then
  say "Fetching and checking out $ref"
  git fetch --tags --prune origin
  git checkout --quiet --force "$ref"
  # A branch name moves; a tag or SHA does not.
  git rev-parse --verify --quiet "origin/$ref" >/dev/null && git reset --quiet --hard "origin/$ref"
else
  say "Updating the current branch"
  git pull --ff-only --quiet
fi
current="$(git rev-parse HEAD)"
echo "  $previous → $current"

# ── 2. Dependencies ─────────────────────────────────────────────────────────
say "Installing dependencies (pinned, hashed)"
[ -x "$python" ] || python3.12 -m venv venv
"$python" -m pip install --quiet --upgrade pip
"$python" -m pip install --quiet --require-hashes -r requirements/production.txt
[ "${WITH_LDAP:-0}" = "1" ] && "$python" -m pip install --quiet --require-hashes -r requirements/ldap.txt

# ── 3+4. Configuration must be safe before anything touches the database ────
say "Checking the production configuration"
"$python" -c "import django; django.setup()" || fail "production settings refused to load — fix the values it listed"
"$python" manage.py check --deploy --fail-level ERROR

# ── 5+6. Static files, schema, catalogues ───────────────────────────────────
say "Collecting static files"
"$python" manage.py collectstatic --no-input --clear --verbosity 0
say "Applying migrations"
"$python" manage.py migrate --no-input
say "Seeding catalogues (permissions, templates, settings, workflows)"
"$python" manage.py seed

# ── 7. Restart ──────────────────────────────────────────────────────────────
say "Restarting services"
if command -v systemctl >/dev/null 2>&1; then
  sudo systemctl restart "${slug}-worker" "${slug}-beat"
  # HUP = graceful reload: old workers finish their requests, new ones load the new code.
  sudo systemctl reload-or-restart "${slug}-web"
else
  echo "  systemctl not found — restart the web, worker and beat processes yourself"
fi

# ── 8. Verify, or roll the code back ────────────────────────────────────────
say "Health check: $health"
for _ in $(seq 1 20); do
  if curl -fsS --max-time 3 "$health" >/dev/null 2>&1; then
    echo "  healthy — deployed $current"
    exit 0
  fi
  sleep 2
done
echo "  not healthy after 40 s — rolling the code back to $previous" >&2
git checkout --quiet --force "$previous"
command -v systemctl >/dev/null 2>&1 && sudo systemctl restart "${slug}-web" "${slug}-worker" "${slug}-beat"
fail "deployment of $current failed; $previous is running again (migrations are NOT reverted — check them)"
