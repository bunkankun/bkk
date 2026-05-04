"""Example values for OpenAPI / Swagger UI prefills.

Surfaced via ``openapi_examples=`` on Path/Query parameters. Swagger UI
renders the dict as a dropdown labeled "Examples" and prefills the input
with the first entry's ``value``. Picks come from the bundles shipped under
``module/samples/`` (KR3a0013 = 傅子, KR6q0053 = 春秋繁露).
"""

from __future__ import annotations

from typing import Any

# Bundle identifiers
TEXTID: dict[str, Any] = {
    "傅子":      {"summary": "傅子 (KR3a0013)",      "value": "KR3a0013"},
    "春秋繁露":  {"summary": "春秋繁露 (KR6q0053)",  "value": "KR6q0053"},
}

PREFIX: dict[str, Any] = {
    "philosophers (KR3a)": {"value": "KR3a"},
    "classics (KR1)":      {"value": "KR1"},
}

CANONICAL: dict[str, Any] = {
    "傅子 master": {"value": "bkk:krp/KR3a0013/v1"},
}

# /texts/{identifier}: textid OR slug OR krp OR cbeta value
IDENTIFIER: dict[str, Any] = {
    "by textid":              {"summary": "傅子 by KRP textid",       "value": "KR3a0013"},
    "by canonical_identifier": {"summary": "春秋繁露 by canonical id", "value": "bkk:krp/KR6q0053/v1"},
}

# Juan / bucket / marker selectors
SEQ: dict[str, Any] = {
    "first juan": {"value": 1},
}

BUCKET: dict[str, Any] = {
    "body":  {"summary": "main text",   "value": "body"},
    "front": {"summary": "front matter","value": "front"},
    "back":  {"summary": "back matter", "value": "back"},
}

MARKER_TYPE: dict[str, Any] = {
    "variant":    {"summary": "witness variants",      "value": "variant"},
    "page-break": {"summary": "edition page breaks",   "value": "page-break"},
}

# Assets
ASSET_NAME: dict[str, Any] = {
    "PUA-map.yaml": {"summary": "PUA → real-codepoint mapping", "value": "PUA-map.yaml"},
}

# Search
QUERY: dict[str, Any] = {
    "甞不盡 (傅子)": {"value": "甞不盡"},
    "天命":          {"value": "天命"},
}

WITNESS_LIST: dict[str, Any] = {
    "Wenyuange (WYG)":   {"value": ["WYG"]},
    "WYG and SBCK":       {"value": ["WYG", "SBCK"]},
}

# Range hints (markers)
FROM: dict[str, Any] = {"start": {"value": 0}}
TO: dict[str, Any] = {"first 200 chars": {"value": 200}}

# /slice query params
SLICE_OFFSET: dict[str, Any] = {"start of bucket": {"value": 0}}
SLICE_LENGTH: dict[str, Any] = {"first 200 chars": {"value": 200}}
SLICE_FROM_MARKER: dict[str, Any] = {
    "傅子 WYG juan 1, 1a": {"value": "KR3a0013_WYG_001-1a"},
}
SLICE_TO_MARKER: dict[str, Any] = {
    "傅子 WYG juan 1, 1b": {"value": "KR3a0013_WYG_001-1b"},
}
SLICE_TOC: dict[str, Any] = {
    "first TOC entry": {"value": "KR3a0013_WYG_001-1a"},
}

# Catalog
CATALOG_HINT = (
    "Try ?tags.kr-categories=KR3a or ?authors.name=傅玄 or "
    "?metadata.identifiers.krp=KR3a0013. See the unknown_filter_keys 400 "
    "response for the full whitelist."
)
