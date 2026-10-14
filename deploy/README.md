# Deployment files (bare metal)

Templates for a single server or a small fleet: systemd runs gunicorn, the
Celery worker and beat as the unprivileged `algodial` user under `/srv/algodial`;
nginx terminates TLS in front. `scripts/init_project.py` renames `algodial` to your
slug in every file here.

| File | Purpose |
|------|---------|
| `systemd/algodial-web.service` | The API (gunicorn on a unix socket) |
| `systemd/algodial-worker.service` | Celery worker |
| `systemd/algodial-beat.service` | Celery beat — exactly one instance |
| `nginx/site.conf` | TLS, proxy, static + public media, upload limit |

`scripts/deploy.sh` is what CI (or you) runs on the server for every release.
The full procedure is in [docs/DEPLOYMENT.md](../docs/DEPLOYMENT.md).
