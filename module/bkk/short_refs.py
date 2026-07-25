"""Shared parsing for compact Kanripo text and juan references."""

from __future__ import annotations

import argparse
import re
from typing import Any


_KR_SECTION_PATTERN = r"[0-9][a-z]{1,2}"
_REF_RE = re.compile(
    r"^(?:KR)?"
    rf"(?P<section>{_KR_SECTION_PATTERN})"
    r"(?P<serial>[0-9]{1,4})"
    r"/(?P<juan>[0-9]+)"
    r"(?:/"
    r"(?=@|(?:front|body|back)(?:@|$))"
    r"(?P<bucket>front|body|back)?"
    r"(?:@(?P<offset>[0-9]+)\+(?P<length>[0-9]+))?"
    r")?"
    r"$"
)
_TEXTID_RE = re.compile(
    rf"^KR(?P<section>{_KR_SECTION_PATTERN})(?P<serial>[0-9]{{3,4}})$"
)
_COMPACT_TEXTID_RE = re.compile(
    rf"^(?:KR)?(?P<section>{_KR_SECTION_PATTERN})(?P<serial>[0-9]{{1,4}})$"
)


def _serial_width(section: str) -> int:
    return 3 if len(section) == 3 else 4


def _valid_serial_width(section: str, serial: str) -> bool:
    return len(serial) <= _serial_width(section)


def _canonical_textid(section: str, serial: str) -> str:
    return f"KR{section}{serial.zfill(_serial_width(section))}"


def parse_short_ref(ref: str) -> tuple[str, dict[str, Any]]:
    """Parse ``1h4/1/@0+86`` into ``("KR1h0004", selection)``.

    The optional suffix is ``/<bucket>`` for a whole bucket,
    ``/@<offset>+<length>`` for a body slice, or
    ``/<bucket>@<offset>+<length>`` for an explicit bucket slice.

    ``KR`` and leading zeroes in the text serial may be omitted.
    The bucket defaults to ``body`` and is therefore omitted from the
    normalized selection unless it was explicit and non-body.
    """
    value = ref.strip()
    match = _REF_RE.fullmatch(value)
    if match is None or not _valid_serial_width(
        match.group("section"), match.group("serial")
    ):
        raise ValueError(f"invalid shortcut ref {ref!r}")

    textid = _canonical_textid(match.group("section"), match.group("serial"))
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
    if "/" not in selector and "\\" not in selector:
        normalized = normalize_text_id(selector)
        match = _TEXTID_RE.fullmatch(normalized)
        if match is not None and len(match.group("serial")) == _serial_width(
            match.group("section")
        ):
            return normalized, None
    textid, selection = parse_short_ref(selector)
    if set(selection) != {"juan"}:
        raise ValueError(
            f"selector {value!r} must identify a complete text or juan"
        )
    return textid, selection["juan"]


def normalize_text_id(value: str) -> str:
    """Expand a compact KR text ID, preserving other identifier schemes.

    ``1h4`` and ``KR1h4`` become ``KR1h0004``. Already canonical IDs are
    unchanged. Non-KR identifiers such as CBETA's ``J01nA001`` pass through
    because several shared CLIs accept those schemes too.
    """
    textid = value.strip()
    match = _COMPACT_TEXTID_RE.fullmatch(textid)
    if match is not None and _valid_serial_width(
        match.group("section"), match.group("serial")
    ):
        return _canonical_textid(match.group("section"), match.group("serial"))
    if "/" in textid or "\\" in textid:
        try:
            parsed_textid, _selection = parse_short_ref(textid)
        except ValueError:
            return textid
        raise ValueError(
            f"{value!r} selects part of {parsed_textid}; "
            "this argument requires a complete text"
        )
    return textid


def text_id_arg(value: str) -> str:
    """``argparse`` type for text-only selectors."""
    try:
        return normalize_text_id(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


def text_prefix_arg(value: str) -> str:
    """``argparse`` type for text-id prefix scopes.

    Accepts the same compact full-text shortcuts as :func:`text_id_arg`
    (``1h4`` -> ``KR1h0004``), plus shorter KR section prefixes such as
    ``6`` and ``6q``. Non-KR prefixes pass through for callers that operate
    on non-canonical bundle names.
    """
    prefix = value.strip()
    if "/" in prefix or "\\" in prefix:
        try:
            parsed_textid, _selection = parse_short_ref(prefix)
        except ValueError as exc:
            raise argparse.ArgumentTypeError(str(exc)) from exc
        raise argparse.ArgumentTypeError(
            f"{value!r} selects part of {parsed_textid}; "
            "this argument requires a text-id prefix"
        )
    if re.fullmatch(r"[0-9]", prefix):
        return f"KR{prefix}"
    if re.fullmatch(r"[0-9][a-z]", prefix):
        return f"KR{prefix}"
    try:
        return normalize_text_id(prefix)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


def text_or_path_arg(value: str) -> str:
    """Normalize a compact ID while preserving bundle-directory arguments."""
    expanded = str(value).strip()
    if "/" in expanded or "\\" in expanded:
        try:
            parse_short_ref(expanded)
        except ValueError:
            return expanded
        return text_id_arg(expanded)
    if expanded.startswith((".", "~")):
        return expanded
    return text_id_arg(expanded)
