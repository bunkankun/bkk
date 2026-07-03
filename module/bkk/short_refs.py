"""Shared parsing for compact Kanripo text and juan references."""

from __future__ import annotations

import re
from typing import Any


_REF_RE = re.compile(
    r"^(?:KR)?"
    r"(?P<section>[0-9][a-z])"
    r"(?P<serial>[0-9]{1,4})"
    r"/(?P<juan>[0-9]+)"
    r"(?:/"
    r"(?=@|(?:front|body|back)(?:@|$))"
    r"(?P<bucket>front|body|back)?"
    r"(?:@(?P<offset>[0-9]+)\+(?P<length>[0-9]+))?"
    r")?"
    r"$"
)
_TEXTID_RE = re.compile(r"^KR[0-9][a-z][0-9]{4}$")


def parse_short_ref(ref: str) -> tuple[str, dict[str, Any]]:
    """Parse ``1h4/1/@0+86`` into ``("KR1h0004", selection)``.

    The optional suffix is ``/<bucket>`` for a whole bucket,
    ``/@<offset>+<length>`` for a body slice, or
    ``/<bucket>@<offset>+<length>`` for an explicit bucket slice.

    ``KR`` and leading zeroes in the four-digit text serial may be omitted.
    The bucket defaults to ``body`` and is therefore omitted from the
    normalized selection unless it was explicit and non-body.
    """
    value = ref.strip()
    match = _REF_RE.fullmatch(value)
    if match is None:
        raise ValueError(f"invalid shortcut ref {ref!r}")

    serial = match.group("serial").zfill(4)
    textid = f"KR{match.group('section')}{serial}"
    selection: dict[str, Any] = {"juan": int(match.group("juan"))}

    bucket = match.group("bucket")
    if bucket and bucket != "body":
        selection["bucket"] = bucket

    offset = match.group("offset")
    length = match.group("length")
    if offset is not None and length is not None:
        selection["offset"] = int(offset)
        selection["length"] = int(length)

    return textid, selection


def parse_text_juan_selector(value: str) -> tuple[str, int | None]:
    """Normalize a canonical text ID or compact whole-juan reference.

    Accepted examples are ``KR1h0004``, ``KR1h0004/1``, and ``1h4/1``.
    Bucket and slice suffixes are deliberately rejected because this selector
    addresses either a complete bundle or a complete juan.
    """
    selector = value.strip()
    if _TEXTID_RE.fullmatch(selector):
        return selector, None
    textid, selection = parse_short_ref(selector)
    if set(selection) != {"juan"}:
        raise ValueError(
            f"selector {value!r} must identify a complete text or juan"
        )
    return textid, selection["juan"]
