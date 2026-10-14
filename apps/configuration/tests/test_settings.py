"""Runtime settings: declared by each app, changed on one screen, read
through ``policy()`` everywhere."""

import pytest

from apps.configuration import catalogue, services
from apps.configuration.models import SystemSetting
from apps.core.catalogue import SettingSpec
from apps.core.exceptions import NotFoundError, ValidationError
from apps.core.hooks import policy

pytestmark = pytest.mark.django_db


def test_every_declared_setting_is_well_formed():
    assert catalogue.problems() == []
    keys = [spec.key for spec in catalogue.specs()]
    assert len(keys) == len(set(keys))
    assert {"SYSTEM_NAME", "PASSWORD_MIN_LENGTH", "MFA_REQUIRED_ACCOUNT_TYPES"} <= set(keys)
    assert catalogue.get("SYSTEM_NAME").public and catalogue.get("NOPE") is None


def test_problems_are_named_precisely(monkeypatch):
    bad = [
        ("apps.x", SettingSpec(key="lower case", group="G", name="", description="short", type="WEIRD")),
        (
            "apps.y",
            SettingSpec(
                key="SYSTEM_NAME",
                group="G",
                name="Dup",
                description="A duplicate of a known key.",
                type="STRING",
            ),
        ),
        (
            "apps.z",
            SettingSpec(
                key="PICK",
                group="G",
                name="Pick",
                description="A choice with no choices at all.",
                type="CHOICE",
            ),
        ),
        (
            "apps.w",
            SettingSpec(
                key="PICK",
                group="G",
                name="Pick again",
                description="The same key, declared twice over.",
                type="STRING",
            ),
        ),
    ]
    monkeypatch.setattr(catalogue, "declared", lambda: bad)
    problems = catalogue.problems()
    assert any("UPPER_SNAKE_CASE" in p for p in problems)
    assert any("unknown type" in p for p in problems)
    assert any("needs a name and a description" in p for p in problems)
    assert any("already declared" in p for p in problems)
    assert any("needs its choices" in p for p in problems)


def test_precedence_saved_then_django_setting_then_default(settings, admin):
    settings.PASSWORD_HISTORY_COUNT = 3
    assert policy("PASSWORD_HISTORY_COUNT", 5) == 3
    assert services.value_of("PASSWORD_HISTORY_COUNT") == 3
    services.set_value(key="PASSWORD_HISTORY_COUNT", value="7", actor=admin)
    assert policy("PASSWORD_HISTORY_COUNT", 5) == 7 and services.value_of("PASSWORD_HISTORY_COUNT") == 7
    described = services.describe(catalogue.get("PASSWORD_HISTORY_COUNT"))
    assert described["is_changed"] and described["default_value"] == 3 and described["value"] == 7
    services.reset_value(key="PASSWORD_HISTORY_COUNT", actor=admin)
    assert policy("PASSWORD_HISTORY_COUNT", 5) == 3
    assert not SystemSetting.objects.filter(key="PASSWORD_HISTORY_COUNT").exists()
    assert SystemSetting.all_objects.filter(key="PASSWORD_HISTORY_COUNT", deleted=True).exists()
    assert policy("NOT_DECLARED_ANYWHERE", "fallback") == "fallback"
    del settings.PASSWORD_HISTORY_COUNT
    assert services.value_of("PASSWORD_HISTORY_COUNT") == 5  # the declared default


def test_saved_values_are_cached_and_forgotten_on_change(admin, django_assert_num_queries):
    services.forget()
    services.overrides()
    with django_assert_num_queries(0):
        services.overrides()
    services.set_value(key="SYSTEM_NAME", value="Renamed Portal", actor=admin)
    assert services.overrides()["SYSTEM_NAME"] == "Renamed Portal"
    SystemSetting.objects.get(key="SYSTEM_NAME").delete()  # any path, even outside the service
    assert "SYSTEM_NAME" not in services.overrides()


@pytest.mark.parametrize(
    "key,value,expected",
    [
        ("PASSWORD_MIN_LENGTH", "12", 12),
        ("MFA_REQUIRED_ACCOUNT_TYPES", "Internal, External, Internal", ["Internal", "External"]),
        ("MFA_REQUIRED_ROLES", ["Auditors", " "], ["Auditors"]),
        ("SUPPORT_EMAIL", " help@example.org ", "help@example.org"),
        ("TERMS_URL", "https://example.org/terms", "https://example.org/terms"),
        ("SYSTEM_NAME", None, ""),
    ],
)
def test_values_are_coerced_to_their_type(key, value, expected):
    assert services.clean(catalogue.get(key), value) == expected


@pytest.mark.parametrize(
    "key,value",
    [
        ("PASSWORD_MIN_LENGTH", "six"),
        ("PASSWORD_MIN_LENGTH", True),
        ("PASSWORD_MIN_LENGTH", 4),
        ("PASSWORD_MIN_LENGTH", 999),
        ("MFA_REQUIRED_ACCOUNT_TYPES", ["Martian"]),
        ("MFA_REQUIRED_ACCOUNT_TYPES", 42),
        ("SUPPORT_EMAIL", "not-an-address"),
        ("TERMS_URL", "ftp://example.org"),
    ],
)
def test_bad_values_are_refused_with_the_setting_named(key, value):
    with pytest.raises(ValidationError) as excinfo:
        services.clean(catalogue.get(key), value)
    assert excinfo.value.code == 9201 and catalogue.get(key).name in excinfo.value.message


def test_a_boolean_setting():
    spec = SettingSpec(
        key="OPEN", group="Look", name="Open", description="Whether the portal is open.", type="BOOLEAN"
    )
    assert services.clean(spec, "no") is False and services.clean(spec, True) is True
    assert services.clean(spec, " ON ") is True
    with pytest.raises(ValidationError):
        services.clean(spec, "maybe")


def test_a_choice_setting():
    spec = SettingSpec(
        key="THEME",
        group="Look",
        name="Theme",
        description="The colour theme of the portal.",
        type="CHOICE",
        choices=("light", "dark"),
    )
    assert services.clean(spec, "dark") == "dark"
    with pytest.raises(ValidationError):
        services.clean(spec, "blue")


def test_unknown_keys(admin):
    with pytest.raises(NotFoundError) as excinfo:
        services.set_value(key="NOPE", value=1, actor=admin)
    assert excinfo.value.code == 9200
    with pytest.raises(NotFoundError):
        services.reset_value(key="NOPE")


def test_public_values_never_include_private_settings(admin):
    services.set_value(key="SYSTEM_NAME", value="Renamed Portal", actor=admin)
    services.set_value(key="PASSWORD_HISTORY_COUNT", value=9, actor=admin)
    public = services.public_values()
    assert public["SYSTEM_NAME"] == "Renamed Portal" and "PASSWORD_HISTORY_COUNT" not in public
    assert all(row["public"] for row in services.all_settings(public_only=True))


def test_the_api(gql, admin, user, grant):
    listed = gql(
        'query { getSystemSettings(group: "Identity") { code dataList { key value isChanged public } } }',
        user=admin,
    )
    assert listed["code"] == 9001 and {row["key"] for row in listed["dataList"]} >= {
        "SYSTEM_NAME",
        "SUPPORT_EMAIL",
    }
    assert gql("query { getSystemSettings { code } }", user=user)["code"] == 9000
    grant(user, "can_view_system_settings")
    assert gql("query { getSystemSettings { code } }", user=user)["code"] == 9001

    changed = gql(
        'mutation U($value: GenericScalar!) { updateSystemSetting(key: "SYSTEM_NAME", value: $value) { code data { value isChanged } } }',
        {"value": "Renamed Portal"},
        user=admin,
    )
    assert changed["data"] == {"value": "Renamed Portal", "isChanged": True}
    refused = gql(
        'mutation { updateSystemSetting(key: "PASSWORD_MIN_LENGTH", value: 2) { code message } }', user=admin
    )
    assert refused["code"] == 9201 and "Minimum password length" in refused["message"]
    assert (
        gql('mutation { updateSystemSetting(key: "SYSTEM_NAME", value: "x") { code } }', user=user)["code"]
        == 9000
    )

    public = gql("query { getPublicSettings { code data { projectName values } } }")
    assert (
        public["data"]["projectName"] == "Renamed Portal"
        and public["data"]["values"]["SYSTEM_NAME"] == "Renamed Portal"
    )
    assert "PASSWORD_HISTORY_COUNT" not in public["data"]["values"]

    reset = gql('mutation { resetSystemSetting(key: "SYSTEM_NAME") { code data { isChanged } } }', user=admin)
    assert reset["data"]["isChanged"] is False
    assert (
        gql("query { getPublicSettings { data { projectName } } }")["data"]["projectName"] != "Renamed Portal"
    )


def test_the_seed_step_refuses_a_broken_catalogue(monkeypatch):
    from io import StringIO

    from apps.configuration import seed

    class Command:
        stdout = StringIO()

    seed.run(Command)
    assert "runtime settings declared" in Command.stdout.getvalue()
    monkeypatch.setattr(catalogue, "problems", lambda: ["X: broken"])
    with pytest.raises(RuntimeError):
        seed.run(Command)
