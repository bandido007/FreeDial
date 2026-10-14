"""Reads of the teams app. Selectors never mutate."""

from django.contrib.auth import get_user_model
from django.db.models import Q, QuerySet

from apps.core.exceptions import NotFoundError
from apps.teams.models import Team

NOT_FOUND = 10100
UNKNOWN_USER = 10103


def get_team(uid) -> Team:
    team = Team.objects.select_related("created_by").filter(uid=uid).first()
    if team is None:
        raise NotFoundError("Team not found", code=NOT_FOUND)
    return team


def teams_queryset(filters: Q | None = None) -> QuerySet[Team]:
    qs = Team.objects.select_related("created_by").prefetch_related("supervisors", "agents")
    return qs.filter(filters) if filters is not None else qs


def users_by_uid(uids) -> list:
    uids = list(dict.fromkeys(str(uid) for uid in uids))
    users = list(get_user_model().objects.filter(uid__in=uids))
    if len(users) != len(uids):
        raise NotFoundError("One or more users were not found", code=UNKNOWN_USER)
    return users
