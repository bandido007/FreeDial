"""M7: keep each call's stereo recording as a WAV.

The dialplan starts ``MixMonitor(${ASTSPOOLDIR}/recording/calls/${UNIQUEID}.raw,D)`` before
``Stasis()``: 8 kHz 16-bit samples, interleaved left (what the customer says) and right
(everything they hear). The customer's uniqueid is the call's linkedid, so the file is Asterisk's
stored recording ``calls/<linkedid>``.

When a call has ended (the writer marks it ``pending``), :func:`convert_pending` waits until the
customer's channel is gone (MixMonitor closes the file at hangup), fetches the raw file over ARI,
saves a WAV in the default storage (private: played only through the permission-checked view),
then deletes Asterisk's copy. Run by ``manage.py run_recordings``; doing it twice changes nothing.
"""

import io
import logging
import wave
from datetime import timedelta

from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.utils import timezone

from apps.calls import asterisk
from apps.calls.models import Call, RecordingStatus

log = logging.getLogger(__name__)

RATE = 8000
FRAME = 4  # one stereo sample: two 16-bit values
SETTLE = timedelta(seconds=1)  # after call.ended, before looking for the file
GIVE_UP = timedelta(minutes=1)  # no file by then: Asterisk kept none


def asterisk_name(linkedid: str) -> str:
    return f"calls/{linkedid}"


def stereo_wav(raw: bytes) -> bytes:
    """MixMonitor's D output as a WAV file (2 channels, 8 kHz, 16-bit); a torn last frame is dropped."""
    usable = len(raw) - len(raw) % FRAME
    out = io.BytesIO()
    with wave.open(out, "wb") as wav:
        wav.setnchannels(2)
        wav.setsampwidth(2)
        wav.setframerate(RATE)
        wav.writeframes(raw[:usable])
    return out.getvalue()


def storage_key(call: Call) -> str:
    day = (call.started_at or call.ended_at or timezone.now()).strftime("%Y/%m/%d")
    return f"recordings/{day}/{call.linkedid}.wav"


def convert(call: Call, now=None) -> bool:
    """Keep this call's recording if Asterisk has it ready. True when it was kept now."""
    now = now or timezone.now()
    if call.recording_status != RecordingStatus.PENDING:
        return False
    if asterisk.channel_exists(call.linkedid):
        return False  # still up (the brain was restarted mid-call, say): MixMonitor is writing
    name = asterisk_name(call.linkedid)
    raw = asterisk.fetch_recording(name)
    if raw is None:
        if call.ended_at and now - call.ended_at > GIVE_UP:
            call.recording_status = RecordingStatus.MISSING
            call.save(update_fields=["recording_status"])
            log.info("call %s: Asterisk kept no recording", call.linkedid)
        return False
    call.recording = default_storage.save(storage_key(call), ContentFile(stereo_wav(raw)))
    call.recording_seconds = len(raw) // FRAME // RATE
    call.recording_status = RecordingStatus.READY
    call.save(update_fields=["recording", "recording_seconds", "recording_status"])
    asterisk.delete_recording(name)  # only once our copy is saved
    log.info("call %s: recording kept (%s s) as %s", call.linkedid, call.recording_seconds, call.recording)
    return True


def convert_pending(now=None) -> int:
    """Every ended call whose recording is waiting; returns how many were kept."""
    now = now or timezone.now()
    waiting = Call.objects.filter(recording_status=RecordingStatus.PENDING, ended_at__lte=now - SETTLE)
    kept = 0
    for call in waiting.order_by("ended_at")[:50]:
        try:
            kept += convert(call, now)
        except Exception:  # one bad file must not stop the others; it is retried next round
            log.exception("call %s: recording not kept this time", call.linkedid)
    return kept
