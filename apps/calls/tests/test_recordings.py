"""M7: stereo recording. Asterisk's MixMonitor (option D) writes calls/<uniqueid>.raw: 8 kHz 16-bit,
left = the customer, right = everything they hear. After the call AlgoDial keeps it as a WAV and
plays it to people allowed to listen."""

import io
import math
import struct
import wave
from datetime import timedelta
from unittest import mock

import pytest
from django.core.files.storage import default_storage
from django.urls import reverse
from django.utils import timezone

from apps.calls import recordings, writer
from apps.calls.models import Call, RecordingStatus
from apps.calls.permissions import LISTEN_RECORDINGS
from contracts import messages as m
from contracts.envelope import Event as Message

pytestmark = pytest.mark.django_db

LINKEDID = "1791990000.7"


def tone(hz, samples):
    return [int(8000 * math.sin(2 * math.pi * hz * n / 8000)) for n in range(samples)]


def stereo_raw(left, right):
    """What MixMonitor's D option writes: interleaved left/right 16-bit little-endian samples."""
    return b"".join(struct.pack("<hh", lo, ro) for lo, ro in zip(left, right, strict=True))


def channels_of(wav_bytes):
    with wave.open(io.BytesIO(wav_bytes)) as wav:
        params = (wav.getnchannels(), wav.getframerate(), wav.getsampwidth())
        frames = wav.readframes(wav.getnframes())
    samples = struct.unpack(f"<{len(frames) // 2}h", frames)
    return params, list(samples[0::2]), list(samples[1::2])


@pytest.fixture(autouse=True)
def media(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path


@pytest.fixture
def ended_call():
    return Call.objects.create(
        linkedid=LINKEDID,
        entry="899",
        started_at=timezone.now() - timedelta(seconds=40),
        ended_at=timezone.now() - timedelta(seconds=5),
        result="answered",
        recording_status=RecordingStatus.PENDING,
    )


class FakeAsterisk:
    """The three ARI calls the converter makes, with what a test wants Asterisk to say."""

    def __init__(self, raw=None, channel_up=False):
        self.raw, self.channel_up, self.deleted, self.fetched = raw, channel_up, [], []

    def channel_exists(self, channel_id):
        return self.channel_up

    def fetch_recording(self, name):
        self.fetched.append(name)
        return self.raw

    def delete_recording(self, name):
        self.deleted.append(name)


def patched(fake):
    return mock.patch.multiple(
        recordings.asterisk,
        channel_exists=fake.channel_exists,
        fetch_recording=fake.fetch_recording,
        delete_recording=fake.delete_recording,
    )


# ── the WAV ───────────────────────────────────────────────────────────────
def test_stereo_raw_becomes_a_two_channel_wav_with_the_customer_on_the_left():
    customer, heard = tone(440, 8000), tone(700, 8000)
    params, left, right = channels_of(recordings.stereo_wav(stereo_raw(customer, heard)))
    assert params == (2, 8000, 2)
    assert left == customer and right == heard


def test_a_torn_last_frame_is_dropped_not_shifted():
    raw = stereo_raw(tone(440, 160), tone(700, 160)) + b"\x01\x02"  # half a stereo frame
    _params, left, right = channels_of(recordings.stereo_wav(raw))
    assert left == tone(440, 160) and right == tone(700, 160)


# ── the writer ────────────────────────────────────────────────────────────
def test_the_writer_marks_an_ended_calls_recording_pending():
    at = "2026-10-14T05:00:00+00:00"
    started = {
        "call": LINKEDID,
        "leg": LINKEDID,
        "entry": "899",
        "queue": "Support",
        "caller": "c",
        "sip_call_id": "x",
    }
    writer.write(Message(m.CALL_STARTED, started, at=at))
    assert Call.objects.get(linkedid=LINKEDID).recording_status == RecordingStatus.NONE
    ended = {"call": LINKEDID, "result": "answered", "reason": "customer hung up"}
    writer.write(Message(m.CALL_ENDED, ended, at=at))
    assert Call.objects.get(linkedid=LINKEDID).recording_status == RecordingStatus.PENDING


# ── after the call: Asterisk's raw file becomes our WAV ──────────────────
def test_a_pending_recording_is_kept_as_wav_and_removed_from_asterisk(ended_call):
    fake = FakeAsterisk(raw=stereo_raw(tone(440, 16000), tone(700, 16000)))
    with patched(fake):
        assert recordings.convert_pending() == 1
    call = Call.objects.get(pk=ended_call.pk)
    assert call.recording_status == RecordingStatus.READY
    assert call.recording_seconds == 2
    assert fake.fetched == [f"calls/{LINKEDID}"] and fake.deleted == [f"calls/{LINKEDID}"]
    with default_storage.open(call.recording, "rb") as stored:
        _params, left, right = channels_of(stored.read())
    assert left == tone(440, 16000) and right == tone(700, 16000)


def test_it_waits_while_the_customers_channel_is_still_up(ended_call):
    fake = FakeAsterisk(raw=b"\x00" * 640, channel_up=True)  # MixMonitor may still be writing
    with patched(fake):
        assert recordings.convert_pending() == 0
    assert Call.objects.get(pk=ended_call.pk).recording_status == RecordingStatus.PENDING
    assert fake.fetched == []


def test_it_waits_a_moment_after_the_call_ended(ended_call):
    Call.objects.filter(pk=ended_call.pk).update(ended_at=timezone.now())
    fake = FakeAsterisk(raw=b"\x00" * 640)
    with patched(fake):
        assert recordings.convert_pending() == 0
    assert fake.fetched == []


def test_no_file_yet_stays_pending_then_missing_after_a_minute(ended_call):
    fake = FakeAsterisk(raw=None)  # brain-down fallback calls, or MixMonitor not in the dialplan
    with patched(fake):
        recordings.convert_pending()
        assert Call.objects.get(pk=ended_call.pk).recording_status == RecordingStatus.PENDING
        Call.objects.filter(pk=ended_call.pk).update(ended_at=timezone.now() - timedelta(minutes=2))
        recordings.convert_pending()
    assert Call.objects.get(pk=ended_call.pk).recording_status == RecordingStatus.MISSING
    assert fake.deleted == []


def test_converting_again_changes_nothing(ended_call):
    fake = FakeAsterisk(raw=stereo_raw(tone(440, 800), tone(700, 800)))
    with patched(fake):
        recordings.convert_pending()
        first = Call.objects.get(pk=ended_call.pk).recording
        assert recordings.convert_pending() == 0
    assert Call.objects.get(pk=ended_call.pk).recording == first
    assert len(fake.fetched) == 1


# ── playback ──────────────────────────────────────────────────────────────
def ready_call():
    call = Call.objects.create(linkedid=LINKEDID, recording_status=RecordingStatus.READY)
    call.recording = default_storage.save(
        f"recordings/{LINKEDID}.wav", io.BytesIO(recordings.stereo_wav(stereo_raw([1] * 80, [2] * 80)))
    )
    call.save()
    return call


def test_only_people_allowed_to_listen_get_the_recording(client, user, grant):
    call = ready_call()
    url = reverse("call_recording", args=[call.pk])
    client.force_login(user)
    assert client.get(url).status_code == 403
    grant(user, LISTEN_RECORDINGS)
    response = client.get(url)
    assert response.status_code == 200 and response["Content-Type"] == "audio/wav"
    assert response["Cache-Control"] == "private, no-store"
    params, left, right = channels_of(b"".join(response.streaming_content))
    assert params == (2, 8000, 2) and left == [1] * 80 and right == [2] * 80


def test_a_call_without_a_recording_is_not_found(client, user, grant):
    grant(user, LISTEN_RECORDINGS)
    call = Call.objects.create(linkedid=LINKEDID, recording_status=RecordingStatus.MISSING)
    client.force_login(user)
    assert client.get(reverse("call_recording", args=[call.pk])).status_code == 404


def test_signed_out_people_are_sent_to_sign_in(client):
    call = ready_call()
    response = client.get(reverse("call_recording", args=[call.pk]))
    assert response.status_code == 302


def test_the_call_page_shows_the_player_only_to_listeners(client, make_user, grant):
    from apps.calls.permissions import VIEW_CALLS

    call = ready_call()
    page_url, player = f"/admin/calls/call/{call.pk}/change/", f"/calls/{call.pk}/recording".encode()
    viewer = make_user(account_type="Internal", is_staff=True)
    grant(viewer, VIEW_CALLS)
    client.force_login(viewer)
    page = client.get(page_url)
    assert page.status_code == 200 and player not in page.content
    listener = make_user(account_type="Internal", is_staff=True)
    grant(listener, VIEW_CALLS, LISTEN_RECORDINGS)
    client.force_login(listener)
    page = client.get(page_url)
    assert page.status_code == 200 and player in page.content and b"left = the customer" in page.content
