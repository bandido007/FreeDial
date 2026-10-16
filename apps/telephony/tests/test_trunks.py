"""M4b: a trunk saved in AlgoDial becomes the PJSIP objects Asterisk reads live, and an outbound route
says which trunks an outside number may take. DIDs (entry points in E.164) get every form a carrier
may send written to a table the dialplan reads."""

import pytest

from apps.core.exceptions import ValidationError
from apps.queues import services as queues
from apps.queues.models import CarrierNumber
from apps.telephony import services, trunks
from apps.telephony.models import (
    PsAor,
    PsAuth,
    PsEndpoint,
    PsEndpointIdIp,
    PsRegistration,
    Trunk,
)

pytestmark = pytest.mark.django_db

SECRET = "Carrier-secret-123"


@pytest.fixture(autouse=True)
def no_ari(monkeypatch):
    """Asterisk is asked to re-read registrations after a save; not in these tests."""
    calls = []
    monkeypatch.setattr(trunks, "reload_registrations", lambda: calls.append(1))
    return calls


def test_an_ip_trunk_is_an_endpoint_that_only_its_carrier_may_use():
    trunk = trunks.save_trunk(actor=None, name="lab-carrier", style="ip", hosts="127.0.0.2:5070")
    endpoint = PsEndpoint.objects.get(id="trunk-lab-carrier")
    assert (endpoint.context, endpoint.transport, endpoint.aors) == (
        "from-carrier",
        "transport-udp",
        "trunk-lab-carrier",
    )
    assert (endpoint.allow, endpoint.dtmf_mode, endpoint.direct_media) == ("alaw,ulaw", "rfc4733", "no")
    assert (endpoint.deny, endpoint.permit) == ("0.0.0.0/0.0.0.0", "127.0.0.2/255.255.255.255")
    aor = PsAor.objects.get(id="trunk-lab-carrier")
    assert (aor.contact, aor.qualify_frequency) == ("sip:127.0.0.2:5070", 30)
    assert PsEndpointIdIp.objects.get(id="trunk-lab-carrier").match == "127.0.0.2"
    assert not PsRegistration.objects.filter(id="trunk-lab-carrier").exists()
    assert not PsAuth.objects.filter(id="trunk-lab-carrier").exists(), "an IP trunk has no credentials"
    assert trunk.endpoint == "trunk-lab-carrier"


def test_a_registration_trunk_registers_with_its_credentials(no_ari, django_capture_on_commit_callbacks):
    with django_capture_on_commit_callbacks(execute=True):
        _registration_trunk()
    _check_registration(no_ari)


def _registration_trunk():
    trunks.save_trunk(
        actor=None,
        name="voip",
        style="registration",
        hosts="127.0.0.3",
        username="2550001",
        password=SECRET,
        from_user="2550001",
        send_pai=True,
    )


def _check_registration(no_ari):
    auth = PsAuth.objects.get(id="trunk-voip")
    assert (auth.auth_type, auth.username, auth.password) == ("userpass", "2550001", SECRET)
    registration = PsRegistration.objects.get(id="trunk-voip")
    assert (registration.server_uri, registration.client_uri) == ("sip:127.0.0.3", "sip:2550001@127.0.0.3")
    assert (registration.outbound_auth, registration.endpoint) == ("trunk-voip", "trunk-voip")
    endpoint = PsEndpoint.objects.get(id="trunk-voip")
    assert (endpoint.outbound_auth, endpoint.from_user, endpoint.send_pai) == ("trunk-voip", "2550001", "yes")
    assert no_ari, "Asterisk re-reads its registrations"


def test_a_switched_off_trunk_disappears_from_asterisk():
    trunk = trunks.save_trunk(actor=None, name="lab-carrier", style="ip", hosts="127.0.0.2:5070")
    trunks.save_trunk(
        actor=None, uid=trunk.uid, name="lab-carrier", style="ip", hosts="127.0.0.2:5070", active=False
    )
    assert not PsEndpoint.objects.filter(id="trunk-lab-carrier").exists()
    assert not PsAor.objects.filter(id="trunk-lab-carrier").exists()
    assert not PsEndpointIdIp.objects.filter(id="trunk-lab-carrier").exists()


@pytest.mark.parametrize(
    ("fields", "says"),
    [
        ({"name": "Has Space", "style": "ip", "hosts": "127.0.0.2"}, "name"),
        ({"name": "x1", "style": "ip", "hosts": ""}, "host"),
        ({"name": "x1", "style": "ip", "hosts": "carrier.example.com"}, "IP address"),
        ({"name": "x1", "style": "registration", "hosts": "127.0.0.2"}, "username and password"),
        ({"name": "x1", "style": "ip", "hosts": "127.0.0.2", "codecs": "opus"}, "codec"),
    ],
)
def test_a_trunk_that_cannot_work_is_refused(fields, says):
    with pytest.raises(ValidationError) as refused:
        trunks.save_trunk(actor=None, **fields)
    assert says in refused.value.message


def test_trunk_names_and_sip_account_ids_never_collide():
    with pytest.raises(ValidationError):
        services.create_sip_account(actor=None, sip_id="trunk-lab", kind="SOFTPHONE", password=SECRET)


def test_carrier_presets_fill_the_defaults():
    names = {preset.key for preset in trunks.presets()}
    assert {"generic-ip", "generic-registration", "lab-sipp"} <= names
    trunk = trunks.save_trunk(actor=None, name="lab-carrier", preset="lab-sipp")
    assert (trunk.style, trunk.hosts, trunk.max_channels) == ("ip", "127.0.0.2:5070", 4)
    assert (trunk.country_code, trunk.national_prefix, trunk.international_prefix) == ("255", "0", "00")


def test_every_form_of_a_did_is_written_for_the_dialplan():
    trunks.save_trunk(actor=None, name="lab-carrier", preset="lab-sipp")
    support = queues.save_queue(actor=None, name="Support")
    entry = queues.save_entry_point(actor=None, number="+255222123456", queue=support)
    queues.save_entry_point(actor=None, number="800", queue=support)
    forms = dict(CarrierNumber.objects.values_list("form", "did"))
    assert forms == {
        "+255222123456": "+255222123456",
        "00255222123456": "+255222123456",
        "255222123456": "+255222123456",
        "0222123456": "+255222123456",
    }, "the internal 800 is not reachable from a carrier"
    entry.delete()
    assert not CarrierNumber.objects.exists()


def test_a_did_entry_point_must_be_e164():
    support = queues.save_queue(actor=None, name="Support")
    with pytest.raises(ValidationError):
        queues.save_entry_point(actor=None, number="+0222", queue=support)


def test_an_outbound_route_allows_e164_prefixes_and_tries_its_trunks_in_order():
    first = trunks.save_trunk(actor=None, name="first", preset="lab-sipp")
    second = trunks.save_trunk(actor=None, name="second", preset="lab-sipp", hosts="127.0.0.3:5070")
    route = trunks.save_route(actor=None, name="Mobiles", patterns="+2556\n+2557", trunks=[second, first])
    assert route.pattern_list() == ["+2556", "+2557"]
    assert [link.trunk.name for link in route.links.order_by("position")] == ["second", "first"]
    with pytest.raises(ValidationError):
        trunks.save_route(actor=None, name="Bad", patterns="07", trunks=[first])
    with pytest.raises(ValidationError):
        trunks.save_route(actor=None, name="No trunk", patterns="+2557", trunks=[])


def test_trunks_and_routes_reach_the_brain(monkeypatch):
    from apps.queues import receivers

    told = []
    monkeypatch.setattr(receivers, "config_changed", lambda: told.append(1))
    trunk = trunks.save_trunk(actor=None, name="lab-carrier", preset="lab-sipp")
    trunks.save_route(actor=None, name="Mobiles", patterns="+2557", trunks=[trunk])
    assert len(told) >= 2
    assert Trunk.objects.get().active


def test_the_admin_adds_a_trunk_from_a_preset_and_a_route(client, make_user, grant):
    admin_user = make_user(account_type="Internal", is_staff=True)
    grant(admin_user, "can_view_trunks", "can_manage_trunks")
    client.force_login(admin_user)
    form = client.get("/admin/telephony/trunk/add/").content.decode()
    assert "Lab: SIPp playing a carrier" in form and "new_password" in form
    response = client.post(
        "/admin/telephony/trunk/add/",
        {
            "name": "lab-carrier", "preset": "lab-sipp", "style": "ip", "hosts": "127.0.0.2:5070",
            "codecs": "alaw,ulaw", "dtmf_mode": "rfc4733", "max_channels": "4", "country_code": "255",
            "national_prefix": "0", "international_prefix": "00", "national_length": "9",
            "strip_digits": "0", "prefix": "", "external_prefix": "", "active": "on",
        },
    )  # fmt: skip
    assert response.status_code == 302, response.content.decode()[:1500]
    trunk = Trunk.objects.get(name="lab-carrier")
    assert PsEndpoint.objects.filter(id="trunk-lab-carrier").exists()
    response = client.post(
        "/admin/telephony/outboundroute/add/",
        {
            "name": "Mobiles", "patterns": "+2557\n+2556", "caller_id": "", "required_permission": "can_dial_external",
            "active": "on", "links-TOTAL_FORMS": "1", "links-INITIAL_FORMS": "0",
            "links-0-trunk": str(trunk.pk), "links-0-position": "1",
        },
    )  # fmt: skip
    assert response.status_code == 302, response.content.decode()[:1500]
    bad = client.post(
        "/admin/telephony/outboundroute/add/",
        {"name": "Bad", "patterns": "0712", "required_permission": "can_dial_external", "active": "on",
         "links-TOTAL_FORMS": "0", "links-INITIAL_FORMS": "0"},
    )  # fmt: skip
    assert bad.status_code == 200 and "E.164 prefix" in bad.content.decode()
    stranger = make_user(account_type="Internal", is_staff=True)
    client.force_login(stranger)
    assert client.get("/admin/telephony/trunk/").status_code == 403
