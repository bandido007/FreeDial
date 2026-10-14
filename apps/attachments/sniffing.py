"""Does the content match the file type it claims to be?

A file name is whatever the uploader typed. ``invoice.pdf`` can be an
executable, a script, or an HTML page that runs in the next reader's browser.
:func:`matches` looks at the first bytes of the content — the *signature* — and
says whether they fit the extension.

Images are not checked here: they are decoded and re-encoded by Pillow, which
is a stronger proof than any signature.
"""

OLE_COMPOUND = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"  # legacy Office (.doc, .xls, .ppt)
ZIP_ARCHIVE = (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08")  # modern Office is a ZIP

SIGNATURES = {
    "pdf": (b"%PDF-",),
    "doc": (OLE_COMPOUND,),
    "xls": (OLE_COMPOUND,),
    "ppt": (OLE_COMPOUND,),
    "docx": ZIP_ARCHIVE,
    "xlsx": ZIP_ARCHIVE,
    "pptx": ZIP_ARCHIVE,
    "png": (b"\x89PNG\r\n\x1a\n",),
    "jpg": (b"\xff\xd8\xff",),
    "jpeg": (b"\xff\xd8\xff",),
    "gif": (b"GIF87a", b"GIF89a"),
}

# What a modern Office file must contain, so an arbitrary ZIP renamed to
# .docx is refused.
OFFICE_MARKERS = {
    "docx": b"word/",
    "xlsx": b"xl/",
    "pptx": b"ppt/",
}

TEXT_EXTENSIONS = {"csv", "txt"}
# Bytes that never appear in a text file but do in every executable.
_BINARY_BYTES = bytes(range(0, 9)) + bytes([11, 12]) + bytes(range(14, 32))
TEXT_SAMPLE_BYTES = 8192
# Markup that turns a "text" file into something a browser would run.
ACTIVE_CONTENT = (b"<script", b"<html", b"<iframe", b"<svg", b"javascript:", b"<?php")


def looks_like_text(data: bytes) -> bool:
    sample = data[:TEXT_SAMPLE_BYTES]
    if any(byte in _BINARY_BYTES for byte in sample):
        return False
    for encoding in ("utf-8", "utf-16", "latin-1"):
        try:
            sample.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:
        return False
    lowered = sample.lower()
    return not any(marker in lowered for marker in ACTIVE_CONTENT)


def matches(data: bytes, extension: str) -> bool:
    """True when ``data`` is plausibly a file of type ``extension``."""
    extension = (extension or "").lower()
    if not data:
        return False
    if extension in TEXT_EXTENSIONS:
        return looks_like_text(data)
    if extension == "webp":
        return data[:4] == b"RIFF" and data[8:12] == b"WEBP"
    signatures = SIGNATURES.get(extension)
    if signatures is None:
        return False  # an extension with no known signature is never accepted blind
    if not data.startswith(signatures):
        return False
    marker = OFFICE_MARKERS.get(extension)
    return marker is None or marker in data
