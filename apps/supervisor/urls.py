from django.urls import path

from apps.supervisor.views import supervisor_page

urlpatterns = [
    path("supervisor/", supervisor_page, name="supervisor_page"),
]
