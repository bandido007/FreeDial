"""Under ASGI (daphne), asgiref inspects every context variable each time it hops between the
event loop and a worker thread. The acting user must not be a lazy object there: touching it
would load the user from the database inside the event loop (SynchronousOnlyOperation)."""

from asgiref.sync import async_to_sync
from django.contrib.auth.models import AnonymousUser
from django.http import HttpResponse
from django.test import RequestFactory
from django.utils.functional import SimpleLazyObject

from apps.core.context import get_current_user
from apps.core.middleware import CurrentUserMiddleware


async def _hop():
    return None


def test_a_lazy_request_user_is_not_loaded_when_asgiref_switches_threads():
    request = RequestFactory().get("/")
    loaded = []
    request.user = SimpleLazyObject(lambda: loaded.append(1) or AnonymousUser())
    seen = []

    def view(request):
        async_to_sync(_hop)()  # what Django's ASGI handler does around sync code
        assert loaded == [], "the lazy user was loaded by asgiref"
        seen.append(get_current_user())  # code that asks for the user still gets it
        return HttpResponse()

    CurrentUserMiddleware(view)(request)
    assert isinstance(seen[0], AnonymousUser) and loaded == [1], "loaded on first use, in the sync thread"
    assert get_current_user() is None, "no user leaks past the request"
