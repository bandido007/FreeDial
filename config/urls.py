"""Root URL configuration.

Routes:
    /healthz                 liveness probe (process is up)
    /readyz                  readiness probe (database, cache, broker)
    /admin/                  Django admin
    /graphql                 GraphQL endpoint (queries + mutations)
    /oauth2/access_token     OAuth2 token issuance (login / refresh)
    /oauth2/revoke_token     OAuth2 token revocation (logout)
    /oauth2/external/<name>  external sign-in (OIDC ID-token exchange)
    /files/…                 file upload / download
    /agent/                  the agent page (project; its live WebSocket is /ws/agent, see config/asgi.py)
    /supervisor/             the supervisor page (project; live WebSocket /ws/supervisor)

Every route is also served under ``/<API_MOUNT_PREFIX>/`` (``/backend/`` by
default) for deployments that publish the API behind a path prefix on the
frontend's host.
"""

from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path
from django.views.decorators.csrf import csrf_exempt

from apps.core import health
from apps.core.graphql.views import HardenedGraphQLView

admin.site.site_header = f"{settings.PROJECT_NAME} Administration"
admin.site.site_title = f"{settings.PROJECT_NAME} Admin"
admin.site.index_title = settings.PROJECT_NAME

core_urlpatterns = [
    path("healthz", health.liveness, name="healthz"),
    path("readyz", health.readiness, name="readyz"),
    path("admin/", admin.site.urls),
    path(
        "graphql",
        csrf_exempt(
            HardenedGraphQLView.as_view(graphiql=settings.GRAPHIQL, batch=settings.GRAPHQL_BATCH_ENABLED)
        ),
        name="graphql",
    ),
    path("oauth2/", include("apps.authentication.urls")),
    path("", include("apps.workspace.urls")),
    path("files/", include("apps.attachments.urls")),
    path("", include("apps.agentdesk.urls")),  # project: /agent/, the agent page
    path("", include("apps.calls.urls")),  # project: voicemail messages for callback tasks
    path("", include("apps.supervisor.urls")),  # project: /supervisor/, the supervisor page
]


urlpatterns = list(core_urlpatterns)
# Stable same-origin mount used by the Angular client.
urlpatterns.append(path("api/", include((core_urlpatterns, "angular_api"), namespace="angular_api")))
if settings.API_MOUNT_PREFIX:
    # Wrapped in its own namespace so the second mount does not shadow the
    # first when URLs are reversed.
    urlpatterns.append(
        path(f"{settings.API_MOUNT_PREFIX}/", include((core_urlpatterns, "mounted"), namespace="mounted"))
    )

# Development convenience: serve the PUBLIC files straight from disk. Private
# files are never served this way, not even in development — every read goes
# through /files/, which asks the access policy.
if settings.DEBUG:
    for bucket in settings.ATTACHMENT_PUBLIC_BUCKETS:
        urlpatterns += static(f"{settings.MEDIA_URL}{bucket}/", document_root=settings.MEDIA_ROOT / bucket)
    try:
        from debug_toolbar.toolbar import debug_toolbar_urls

        urlpatterns += debug_toolbar_urls()
    except ImportError:
        pass
