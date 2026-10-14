"""M1: teams group agents under their supervisors."""

import pytest
from django.test import Client

from apps.core.exceptions import DuplicateError, NotFoundError, ValidationError
from apps.teams import services
from apps.teams.models import Team

pytestmark = pytest.mark.django_db

PAGE = {"searchDto": {"pageableParam": {"pageNumber": 1, "itemsPerPage": 20}}}


@pytest.fixture
def manager(make_user, grant):
    person = make_user(account_type="Internal")
    grant(person, "can_view_teams", "can_manage_teams")
    return person


@pytest.fixture
def people(make_user):
    return [make_user(email=f"person{n}@example.org") for n in range(4)]


def test_a_team_has_supervisors_and_agents(manager, people):
    team = services.create_team(
        actor=manager, name="Support", supervisors=[people[0]], agents=[people[1], people[2]]
    )
    assert set(team.supervisors.all()) == {people[0]}
    assert set(team.agents.all()) == {people[1], people[2]}
    assert team.created_by == manager


def test_nobody_supervises_and_works_in_the_same_team(manager, people):
    with pytest.raises(ValidationError):
        services.create_team(actor=manager, name="Support", supervisors=[people[0]], agents=[people[0]])


def test_names_are_unique_but_free_again_after_delete(manager):
    team = services.create_team(actor=manager, name="Support")
    with pytest.raises(DuplicateError):
        services.create_team(actor=manager, name=" support ")
    services.delete_team(uid=team.uid)
    assert services.create_team(actor=manager, name="Support")


def test_a_blank_name_is_refused(manager):
    with pytest.raises(ValidationError):
        services.create_team(actor=manager, name="  ")


def test_members_are_replaced_not_merged(manager, people):
    team = services.create_team(actor=manager, name="Support", agents=[people[1]])
    services.update_team(uid=team.uid, actor=manager, agents=[people[2], people[3]])
    assert set(Team.objects.get(pk=team.pk).agents.all()) == {people[2], people[3]}


def test_deleting_a_team_keeps_it_for_the_trail(manager):
    team = services.create_team(actor=manager, name="Support")
    services.delete_team(uid=team.uid)
    assert Team.all_objects.get(pk=team.pk).deleted
    with pytest.raises(NotFoundError):
        services.delete_team(uid=team.uid)


def test_the_api(gql, manager, people, user):
    saved = gql(
        "mutation S($input: TeamInput!) { saveTeam(input: $input) { code data { uid name supervisors { email } agents { email } } } }",
        {
            "input": {
                "name": "Support",
                "supervisorUids": [str(people[0].uid)],
                "agentUids": [str(people[1].uid)],
            }
        },
        user=manager,
    )
    assert saved["code"] == 9001
    assert saved["data"]["supervisors"] == [{"email": people[0].email}]
    assert saved["data"]["agents"] == [{"email": people[1].email}]
    uid = saved["data"]["uid"]

    denied = gql(
        "mutation S($input: TeamInput!) { saveTeam(input: $input) { code } }",
        {"input": {"name": "X"}},
        user=user,
    )
    assert denied["code"] == 9000

    page = gql(
        "query L($searchDto: SearchDataDto!, $filtering: TeamFilterInput) {"
        " getTeamsPageable(searchDto: $searchDto, filtering: $filtering) { content { name } page { totalElements } } }",
        {**PAGE, "filtering": {"search": "supp"}},
        user=manager,
    )
    assert page["page"]["totalElements"] == 1
    assert (
        gql("query G($uid: UUID!) { getTeam(uid: $uid) { data { name } } }", {"uid": uid}, user=manager)[
            "data"
        ]["name"]
        == "Support"
    )
    assert (
        gql("mutation D($uid: UUID!) { deleteTeam(uid: $uid) { code } }", {"uid": uid}, user=manager)["code"]
        == 9001
    )


def test_the_admin_page_creates_a_team(admin, people):
    browser = Client()
    browser.force_login(admin)
    response = browser.post(
        "/admin/teams/team/add/",
        {"name": "Support", "description": "", "supervisors": [people[0].pk], "agents": [people[1].pk]},
    )
    assert response.status_code == 302, response.content.decode()[:2000]
    team = Team.objects.get(name="Support")
    assert list(team.agents.all()) == [people[1]]
