from django.urls import path, re_path

from apps.workspace import views

urlpatterns = [
    path("app/", views.frontend, {"path": ""}),
    path("workspace/config", views.public_config),
    path("workspace/bootstrap", views.bootstrap),
    path("workspace/catalogue", views.catalogue),
    path("workspace/records/<str:label>", views.records),
    path("workspace/records/<str:label>/<str:pk>", views.record),
    path("workspace/choices/<str:label>", views.choices),
    path("workspace/action/<str:label>", views.action),
    path("workspace/history/<str:label>/<str:pk>", views.history),
    path("workspace/reports", views.reports),
    path("workspace/settings", views.settings_view),
    path("workspace/user-password/<str:pk>", views.user_password),
    path("workspace/setup", views.setup),
    re_path(r"^app/(?P<path>.*)$", views.frontend),
]
