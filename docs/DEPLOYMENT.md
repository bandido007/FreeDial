# Deployment (bare metal)

One Linux host (Debian/Ubuntu shown) running PostgreSQL, Redis, nginx and three
systemd services: the API (gunicorn), the Celery worker and beat. The same layout
scales to several hosts by pointing `DATABASE_URL` / `REDIS_URL` at shared
servers and running beat on exactly one of them.

Throughout, `algodial` is the project slug — `init_project.py` renamed it in
`deploy/` already.

## 1. One-time server setup

```bash
# System packages
sudo apt install -y python3.12 python3.12-venv build-essential libpq-dev nginx postgresql redis-server git curl
# + libldap2-dev libsasl2-dev if directory sign-in is used (WITH_LDAP=1)

# The app user and directory
sudo useradd --system --create-home --home-dir /srv/algodial --shell /bin/bash algodial
sudo -iu algodial git clone <repo-url> /srv/algodial      # or: clone, then chown -R algodial:algodial
sudo -iu algodial bash -c 'cd /srv/algodial && python3.12 -m venv venv'

# Database and cache
sudo -u postgres psql -c "CREATE USER algodial WITH PASSWORD '<strong-password>';"
sudo -u postgres psql -c "CREATE DATABASE algodial OWNER algodial;"
# redis: the default local instance is fine; bind it to 127.0.0.1

# Configuration
sudo -iu algodial cp /srv/algodial/.env.example /srv/algodial/.env
sudo -iu algodial chmod 600 /srv/algodial/.env
# Edit .env: every "REQUIRED IN PRODUCTION" line, plus
#   DJANGO_SETTINGS_MODULE=config.settings.production
#   TRUSTED_PROXY_COUNT=1                (nginx is in front)
#   CELERY_TASK_ALWAYS_EAGER=False       (there is a real worker now)
#   EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend + SMTP values
# Keys: `make secret-key`, `make encryption-key` on any machine with the venv.

# Services
sudo cp /srv/algodial/deploy/systemd/algodial-*.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable algodial-web algodial-worker algodial-beat

# The app user may restart its own services without a password (deploy.sh needs this)
echo 'algodial ALL=(root) NOPASSWD: /bin/systemctl restart algodial-web, /bin/systemctl restart algodial-worker, /bin/systemctl restart algodial-beat, /bin/systemctl reload-or-restart algodial-web, /bin/systemctl restart algodial-web algodial-worker algodial-beat, /bin/systemctl restart algodial-worker algodial-beat' \
  | sudo tee /etc/sudoers.d/algodial-deploy

# nginx
sudo cp /srv/algodial/deploy/nginx/site.conf /etc/nginx/sites-available/algodial
#   edit server_name and the certificate paths (certbot --nginx api.example.org)
sudo ln -s /etc/nginx/sites-available/algodial /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
```

Install Node.js **22.23+** and npm for the application user as well. The release script
builds `frontend/` before restarting services. Django serves the compiled Angular app
at `/app/`; the production host does not run `ng serve`. See
[ANGULAR_FRONTEND.md](ANGULAR_FRONTEND.md).

## 2. First release

```bash
sudo -iu algodial bash -c 'cd /srv/algodial && scripts/deploy.sh main'
```

`deploy.sh` installs the hashed requirements, **refuses to continue if the
production settings are unsafe** (it lists every problem), runs Django's
deployment checks, collects static files, migrates, seeds the catalogues (and the
first administrator when `DEFAULT_ADMIN_EMAIL` / `DEFAULT_ADMIN_PASSWORD` are set),
restarts the services and waits for `/readyz`. If the health check fails it puts
the previous commit back and restarts.

Then: `curl -s https://api.example.org/readyz`, sign in as the administrator,
and change that password.

## 3. Every release after that

From CI: the `deploy` job in `.gitlab-ci.yml` runs
`ssh $DEPLOY_USER@$DEPLOY_HOST "cd $DEPLOY_PATH && scripts/deploy.sh $CI_COMMIT_SHA"`.
Set the CI variables `DEPLOY_HOST`, `DEPLOY_USER` (= `algodial`),
`DEPLOY_SSH_KEY` (type *file*, the private key whose public half is in
`/srv/algodial/.ssh/authorized_keys`) and `DEPLOY_PATH` (= `/srv/algodial`). The
job is manual on tags and on the default branch.

By hand: `scripts/deploy.sh v1.4.0` (a tag, a branch or a commit).

Rolling back: `scripts/deploy.sh <previous tag>`. Migrations are not reversed
automatically — write reversible migrations and run `manage.py migrate <app>
<name>` when needed.

## 4. Operating

| Task | Command |
|------|---------|
| Logs | `journalctl -u algodial-web -f` (JSON lines; `LOG_FORMAT=text` for humans) · `logs/` when `LOG_TO_FILE=True` |
| Status | `systemctl status algodial-web algodial-worker algodial-beat` |
| A management command | `sudo -iu algodial bash -c 'cd /srv/algodial && DJANGO_SETTINGS_MODULE=config.settings.production venv/bin/python manage.py <command>'` |
| Rotate the encryption key | prepend the new key to `ENCRYPTION_KEYS`, restart, `manage.py rotate_secrets`, drop the old key |
| Health | `/healthz` (process up) · `/readyz` (database, cache, broker when `HEALTH_CHECK_BROKER=True`) |
| Backups | `pg_dump algodial` + `/srv/algodial/media` (or the S3 bucket when `STORAGE_BACKEND=s3`) |
| Scale the API | raise `--workers` in `algodial-web.service` (2 × CPU + 1 is the usual start) |

## 5. Checklist before going live

- [ ] `.env`: every *REQUIRED IN PRODUCTION* line set; `DEBUG=False`; exact
      `ALLOWED_HOSTS`; `CORS_ALLOWED_ORIGINS` = the SPA origin; `FRONTEND_DOMAIN`
- [ ] `AXES_ENABLED=True`, `LDAP_SIMULATE_DIRECTORY=False`,
      `SMS_ALLOW_LOG_PROVIDER=False`, `EXPOSE_ERROR_DETAILS=False`
      (the server refuses to start otherwise — `make deploy-check` shows it)
- [ ] TLS on nginx; `SECURE_SSL_REDIRECT=True`; HSTS on
- [ ] SMTP works: *Notifications → E-mail configurations → Test*
- [ ] The administrator's password changed; two-factor turned on for System accounts
      (`MFA_REQUIRED_ACCOUNT_TYPES=System`)
- [ ] Error-report recipients chosen (*Audit → Endpoint errors*)
- [ ] Backups scheduled and restore tested once
