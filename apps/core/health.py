"""Health endpoints for orchestrators and load balancers.

* ``GET /healthz`` — **liveness**: the process is up and serving. Never touches
  a dependency, so a database outage does not get healthy pods restarted.
* ``GET /readyz`` — **readiness**: the pod can do useful work. Checks the
  database, the cache and (optionally) the task broker; answers 503 when any
  of them is down so traffic is routed elsewhere.

Both are public and say only ``ok`` / ``failed`` per check: the reason is
logged server-side, never returned.
"""

import logging

from django.conf import settings
from django.core.cache import cache
from django.db import connection
from django.http import JsonResponse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET

from apps.core.utils import generate_uuid7

logger = logging.getLogger(__name__)

OK, FAILED = "ok", "failed"


@never_cache
@require_GET
def liveness(_request):
    return JsonResponse({"status": OK})


@never_cache
@require_GET
def readiness(_request):
    checks = {"database": _check_database(), "cache": _check_cache()}
    if getattr(settings, "HEALTH_CHECK_BROKER", False):
        checks["broker"] = _check_broker()
    healthy = all(result == OK for result in checks.values())
    return JsonResponse(
        {"status": OK if healthy else "unavailable", "checks": checks},
        status=200 if healthy else 503,
    )


def _check_database() -> str:
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
        return OK
    except Exception:  # noqa: BLE001
        logger.exception("[health] database check failed")
        return FAILED


def _check_cache() -> str:
    key = f"health:{generate_uuid7()}"
    try:
        cache.set(key, "1", timeout=5)
        alive = cache.get(key) == "1"
        cache.delete(key)
        return OK if alive else FAILED
    except Exception:  # noqa: BLE001
        logger.exception("[health] cache check failed")
        return FAILED


def _check_broker() -> str:
    try:
        from celery import current_app

        with current_app.connection_for_read() as conn:
            conn.ensure_connection(max_retries=1, timeout=2)
        return OK
    except Exception:  # noqa: BLE001
        logger.exception("[health] broker check failed")
        return FAILED
